"""
apps/buyers/services.py

All buyer domain business logic.

Sections:
  1. Helpers             — currency, buyer/cart lookups
  2. Cart service        — get_or_create_cart, add/update/remove/clear, coupons
  3. Order service       — create_order_from_cart, confirm/dispatch/deliver/cancel, reorder
  4. Payment service     — initiate, validate_signature, process_webhook_event, refund

Design principles:
  • All multi-step DB operations wrapped in @transaction.atomic.
  • Views never mutate models directly — they call these functions.
  • send_event() is called from services, not views.
  • Celery email/notification tasks are dispatched after DB commits (on_commit).
  • ValueError is the contract for business-rule violations; views convert to ValidationError.
  • No hardcoded currency — always reads settings.DEFAULT_CURRENCY.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.signals import send_event

logger = logging.getLogger("apps.buyers")


# =============================================================================
# 1. HELPERS
# =============================================================================

def _currency() -> str:
    """Return the platform's default currency from settings."""
    return getattr(settings, "DEFAULT_CURRENCY", "GHS")


def get_buyer_or_raise(user) -> "Buyer":
    """
    Return the buyer profile for the given user.
    Raises PermissionError if no buyer profile exists (view converts to 403).
    """
    buyer = getattr(user, "buyer_profile", None)
    if not buyer:
        raise PermissionError("No buyer profile found for this account.")
    return buyer


# =============================================================================
# 2. CART SERVICE
# =============================================================================

def get_or_create_cart(buyer) -> "Cart":
    """
    Return the buyer's active cart, creating one if none exists.
    Abandons any expired cart before creating a fresh one.
    """
    from apps.buyers.models import Cart

    cart = (
        Cart.objects.filter(buyer=buyer, status="active")
        .order_by("-created_at")
        .first()
    )
    if cart and cart.is_expired:
        cart.mark_abandoned()
        cart = None
        logger.info("cart_expired_abandoned | buyer_pk=%s", buyer.pk)

    if not cart:
        cart = Cart.objects.create(buyer=buyer, currency=_currency())
        logger.info("cart_created | buyer_pk=%s | cart_pk=%s", buyer.pk, cart.pk)

    return cart


def _recalculate_discount(cart) -> None:
    """
    Recompute cart.discount_amount from the attached coupon.
    Called internally after every cart mutation. Safe with no coupon.
    """
    cart.discount_amount = (
        cart.coupon.compute_discount(cart.subtotal)
        if cart.coupon
        else Decimal("0.00")
    )
    cart.save(update_fields=["discount_amount"])


@transaction.atomic
def add_item(cart, product, quantity_kg: Decimal, notes: str = "") -> "Cart":
    """
    Add a product to the cart or update its quantity if already present.
    Recalculates coupon discount after the change.

    Raises ValueError on availability or stock violations.
    """
    from apps.buyers.models import CartItem

    if not product.is_available:
        raise ValueError(f"'{product.name}' is not available.")
    if product.stock_kg < quantity_kg:
        raise ValueError(
            f"Only {product.stock_kg} kg of '{product.name}' available; "
            f"requested {quantity_kg} kg."
        )
    if quantity_kg < product.min_order_kg:
        raise ValueError(
            f"Minimum order for '{product.name}' is {product.min_order_kg} kg."
        )

    CartItem.objects.update_or_create(
        cart    = cart,
        product = product,
        defaults={
            "quantity_kg": quantity_kg,
            "unit_price":  product.price_per_kg or Decimal("0.00"),
            "currency":    product.currency or _currency(),
            "notes":       notes,
            "is_active":   True,
        },
    )
    _recalculate_discount(cart)
    cart.refresh_from_db()
    return cart


@transaction.atomic
def update_item(cart, product_id: str, quantity_kg: Decimal) -> "Cart":
    """
    Update quantity of an existing cart item.
    Raises ValueError on stock violations or missing item.
    """
    from apps.buyers.models import CartItem

    try:
        item = cart.items.select_for_update().get(product_id=product_id, is_active=True)
    except CartItem.DoesNotExist:
        raise ValueError("Item not found in cart.")

    item.quantity_kg = quantity_kg
    try:
        item.validate_stock()
    except (ValueError, AttributeError) as exc:
        raise ValueError(str(exc)) from exc

    item.save(update_fields=["quantity_kg"])
    _recalculate_discount(cart)
    cart.refresh_from_db()
    return cart


@transaction.atomic
def remove_item(cart, product_id: str) -> "Cart":
    """Remove a product from the cart. Raises ValueError if not present."""
    deleted, _ = cart.items.filter(product_id=product_id).delete()
    if not deleted:
        raise ValueError("Item not found in cart.")
    _recalculate_discount(cart)
    cart.refresh_from_db()
    return cart


@transaction.atomic
def clear_cart(cart) -> "Cart":
    """Remove all items and detach any coupon."""
    cart.items.all().delete()
    cart.coupon          = None
    cart.discount_amount = Decimal("0.00")
    cart.save(update_fields=["coupon", "discount_amount"])
    logger.info("cart_cleared | cart_pk=%s", cart.pk)
    cart.refresh_from_db()
    return cart


@transaction.atomic
def apply_coupon(cart, coupon) -> "Cart":
    """
    Attach a coupon to the cart and compute the discount.
    Raises ValueError if the coupon is expired, inactive, or already used.
    """
    from apps.buyers.models import CouponUsage

    if not coupon.is_active:
        raise ValueError("This coupon is no longer active.")
    if coupon.valid_until and coupon.valid_until < timezone.now():
        raise ValueError("This coupon has expired.")
    if CouponUsage.objects.filter(coupon=coupon, buyer=cart.buyer).exists():
        raise ValueError("You have already used this coupon.")

    try:
        cart.apply_coupon(coupon)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    logger.info("coupon_applied | cart_pk=%s | coupon=%s", cart.pk, coupon.code)
    cart.refresh_from_db()
    return cart


@transaction.atomic
def remove_coupon(cart) -> "Cart":
    """Detach coupon and zero the discount."""
    cart.remove_coupon()
    cart.refresh_from_db()
    return cart


# =============================================================================
# 3. ORDER SERVICE
# =============================================================================

@transaction.atomic
def create_order_from_cart(
    cart,
    buyer,
    delivery_address_id=None,
    billing_address_id=None,
    notes: str = "",
) -> "Order":
    """
    Convert an active cart into a pending Order.

    Validates:
      - Buyer is verified.
      - Cart is non-empty and not expired.

    Creates Order + OrderItems via bulk_create.
    Marks cart as checked_out.
    Emits order.created domain event.

    Raises ValueError for any business rule violation.
    """
    from apps.buyers.models import Order, OrderItem
    from apps.buyers.tasks import create_buyer_notification

    if buyer.verification_status != "verified":
        raise ValueError("Your buyer account must be verified before placing orders.")
    if not cart or cart.item_count == 0:
        raise ValueError("Your cart is empty.")
    if cart.is_expired:
        cart.mark_abandoned()
        raise ValueError("Your cart has expired. Please start a new one.")

    order = Order.objects.create(
        buyer               = buyer,
        currency            = cart.currency or _currency(),
        subtotal            = cart.subtotal,
        discount_amount     = cart.discount_amount or Decimal("0.00"),
        total_amount        = cart.total,
        coupon              = cart.coupon,
        shipping_address_id = delivery_address_id,
        billing_address_id  = billing_address_id,
        notes               = notes,
        status              = "pending",
    )

    OrderItem.objects.bulk_create([
        OrderItem(
            order       = order,
            product     = item.product,
            quantity_kg = item.quantity_kg,
            unit_price  = item.unit_price,
            currency    = item.currency or _currency(),
        )
        for item in cart.items.select_related("product").filter(is_active=True)
    ])

    cart.status = "checked_out"
    cart.save(update_fields=["status"])

    send_event("order.created", order, buyer=buyer)

    transaction.on_commit(lambda: create_buyer_notification.delay(
        buyer_pk          = str(buyer.pk),
        notification_type = "order_placed",
        title             = "Order placed",
        body              = f"Your order #{order.pk} has been placed successfully.",
        reference_id      = str(order.pk),
    ))

    logger.info(
        "order_created | order_pk=%s | buyer_pk=%s | total=%s",
        order.pk, buyer.pk, order.total_amount,
    )
    return order


@transaction.atomic
def confirm_order(order, confirmed_by) -> "Order":
    """
    Admin confirms a pending order → deducts stock.
    Raises ValueError if order is not pending.
    """
    from apps.buyers.tasks import send_order_confirmed_email

    try:
        order.confirm(confirmed_by=confirmed_by)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    send_event("order.confirmed", order, confirmed_by=confirmed_by)

    transaction.on_commit(lambda: send_order_confirmed_email.delay(
        order_pk    = str(order.pk),
        buyer_email = order.buyer.user.email,
        buyer_name  = order.buyer.company_name or order.buyer.contact_person,
    ))

    logger.info("order_confirmed | order_pk=%s | by=%s", order.pk, confirmed_by.pk)
    return order


@transaction.atomic
def dispatch_order(
    order,
    tracking_number: str,
    carrier: str,
    dispatch_notes: str,
    dispatched_by,
) -> "Order":
    """
    Admin dispatches a confirmed order.
    Raises ValueError if order is not confirmed.
    """
    from apps.buyers.tasks import send_order_dispatched_email

    try:
        order.dispatch(
            tracking_number = tracking_number,
            carrier         = carrier,
            changed_by      = dispatched_by,
        )
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    if dispatch_notes:
        order.dispatch_notes = dispatch_notes
        order.save(update_fields=["dispatch_notes"])

    send_event("order.dispatched", order, dispatched_by=dispatched_by)

    transaction.on_commit(lambda: send_order_dispatched_email.delay(
        order_pk        = str(order.pk),
        buyer_email     = order.buyer.user.email,
        buyer_name      = order.buyer.company_name or order.buyer.contact_person,
        tracking_number = tracking_number,
        carrier         = carrier,
    ))

    logger.info(
        "order_dispatched | order_pk=%s | tracking=%s | by=%s",
        order.pk, tracking_number, dispatched_by.pk,
    )
    return order


@transaction.atomic
def deliver_order(order, delivered_by) -> "Order":
    """
    Admin marks an order as delivered.
    Raises ValueError if order is not dispatched.
    """
    try:
        order.mark_delivered(changed_by=delivered_by)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    send_event("order.delivered", order)
    logger.info("order_delivered | order_pk=%s | by=%s", order.pk, delivered_by.pk)
    return order


@transaction.atomic
def cancel_order(order, reason: str, cancelled_by) -> "Order":
    """
    Buyer or admin cancels an order. Stock is restored for confirmed orders.
    Raises ValueError if order is already delivered or cancelled.
    """
    from apps.buyers.tasks import send_order_cancelled_email

    try:
        order.cancel(reason=reason, changed_by=cancelled_by)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    send_event("order.cancelled", order, cancelled_by=cancelled_by)

    transaction.on_commit(lambda: send_order_cancelled_email.delay(
        order_pk    = str(order.pk),
        buyer_email = order.buyer.user.email,
        buyer_name  = order.buyer.company_name or order.buyer.contact_person,
        reason      = reason,
    ))

    logger.info(
        "order_cancelled | order_pk=%s | by=%s",
        order.pk, cancelled_by.pk,
    )
    return order


@transaction.atomic
def reorder(order, buyer) -> tuple["Cart", int, int]:
    """
    Copy all available items from a previous order into a new active cart.
    Abandons any existing active cart first.

    Returns (new_cart, added_count, skipped_count).
    """
    from apps.buyers.models import Cart, CartItem

    Cart.objects.filter(buyer=buyer, status="active").update(status="abandoned")
    cart = Cart.objects.create(buyer=buyer, currency=order.currency or _currency())

    items_to_create, added, skipped = [], 0, 0
    for item in order.items.select_related("product").filter(is_active=True):
        p = item.product
        if not p.is_available or p.stock_kg < item.quantity_kg:
            skipped += 1
            continue
        items_to_create.append(CartItem(
            cart        = cart,
            product     = p,
            quantity_kg = item.quantity_kg,
            unit_price  = p.price_per_kg or item.unit_price,
            currency    = p.currency or _currency(),
        ))
        added += 1

    CartItem.objects.bulk_create(items_to_create)
    logger.info(
        "reorder_created | original_order=%s | buyer_pk=%s | added=%s | skipped=%s",
        order.pk, buyer.pk, added, skipped,
    )
    return cart, added, skipped


# =============================================================================
# 4. PAYMENT SERVICE
# =============================================================================

@transaction.atomic
def initiate_payment(buyer, order, payment_channel: str, amount, **kwargs) -> "Payment":
    """
    Create a pending Payment record linked to the order.

    Raises ValueError if the order doesn't belong to this buyer or is already paid.
    Provider SDK call (Paystack/Flutterwave) should follow in the view
    after receiving the returned Payment object.
    """
    from apps.buyers.models import Payment

    if order.buyer_id != buyer.pk:
        raise ValueError("Order does not belong to this buyer.")
    if order.payment_status == "paid":
        raise ValueError("This order has already been paid.")

    payment = Payment.objects.create(
        order                = order,
        buyer                = buyer,
        payment_channel      = payment_channel,
        amount               = amount,
        currency             = order.currency or _currency(),
        mobile_money_number  = kwargs.get("mobile_money_number", ""),
        mobile_money_network = kwargs.get("mobile_money_network", ""),
    )

    send_event("payment.initiated", payment, buyer=buyer)
    logger.info(
        "payment_initiated | payment_pk=%s | order_pk=%s | channel=%s",
        payment.pk, order.pk, payment_channel,
    )
    return payment


def validate_webhook_signature(raw_body: bytes, headers: dict, provider: str) -> bool:
    """
    Validate the HMAC signature from the payment provider.

    Raises ValueError if the webhook secret is not configured in settings.
    No silent fallback — a missing secret is a misconfiguration, not a default.

    Required settings:
      PAYSTACK_WEBHOOK_SECRET
      FLUTTERWAVE_WEBHOOK_SECRET
    """
    secret_key = f"{provider.upper()}_WEBHOOK_SECRET"
    secret     = getattr(settings, secret_key, None)

    if not secret:
        raise ValueError(
            f"Webhook secret not configured. Set {secret_key} in settings."
        )

    if provider == "paystack":
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha512).hexdigest()
        received = headers.get("X-Paystack-Signature", "")
    elif provider == "flutterwave":
        expected = secret
        received = headers.get("Verif-Hash", "")
    else:
        raise ValueError(f"Unknown webhook provider: '{provider}'.")

    return hmac.compare_digest(expected, received)


def process_webhook_event(log_pk: str) -> None:
    """
    Process a stored PaymentWebhookLog record.
    Called by the Celery task — never called directly from the view.

    Routes events to the appropriate private handler.
    """
    from apps.buyers.models import PaymentWebhookLog

    try:
        log = PaymentWebhookLog.objects.get(pk=log_pk)
    except PaymentWebhookLog.DoesNotExist:
        logger.error("process_webhook_event | log not found | pk=%s", log_pk)
        return

    handlers = {
        "charge.success":        _handle_payment_success,
        "transfer.success":      _handle_payment_success,
        "payment.completed":     _handle_payment_success,
        "charge.dispute.create": _handle_dispute,
        "refund.completed":      _handle_refund,
    }
    handler = handlers.get(log.event_type)
    if handler:
        try:
            handler(log.raw_payload, log.provider)
            log.mark_processed()
        except Exception as exc:
            logger.exception(
                "webhook_processing_failed | log_pk=%s | event=%s",
                log_pk, log.event_type,
            )
            log.mark_processed(error=str(exc))
    else:
        logger.info(
            "webhook_event_unhandled | log_pk=%s | event=%s",
            log_pk, log.event_type,
        )
        log.mark_processed()


def _handle_payment_success(data: dict, provider: str) -> None:
    from apps.buyers.models import Payment
    from apps.buyers.tasks import send_payment_confirmed_email

    ref = (data.get("data") or {}).get("reference", "") or data.get("txRef", "")
    if not ref:
        logger.warning("payment_success_missing_ref | provider=%s", provider)
        return

    try:
        payment = Payment.objects.select_related(
            "order", "buyer__user"
        ).get(provider_reference=ref)
    except Payment.DoesNotExist:
        logger.warning("payment_not_found | ref=%s", ref)
        return

    with transaction.atomic():
        payment.mark_completed(provider_ref=ref)
        send_event("payment.completed", payment)

    transaction.on_commit(lambda: send_payment_confirmed_email.delay(
        payment_pk  = str(payment.pk),
        buyer_email = payment.buyer.user.email,
        buyer_name  = payment.buyer.company_name or payment.buyer.contact_person,
        amount      = str(payment.amount),
        currency    = payment.currency,
    ))

    logger.info("payment_completed | payment_pk=%s | ref=%s", payment.pk, ref)


def _handle_dispute(data: dict, provider: str) -> None:
    logger.warning(
        "payment_dispute_received | provider=%s | keys=%s",
        provider, list(data.keys()),
    )


def _handle_refund(data: dict, provider: str) -> None:
    from apps.buyers.models import Payment

    ref = (data.get("data") or {}).get("reference", "")
    if not ref:
        return
    try:
        payment = Payment.objects.get(provider_reference=ref)
        with transaction.atomic():
            payment.refund(reason="Provider-initiated refund")
            send_event("payment.refunded", payment)
        logger.info("payment_refunded_via_webhook | payment_pk=%s", payment.pk)
    except Payment.DoesNotExist:
        logger.warning("refund_webhook_payment_not_found | ref=%s", ref)


@transaction.atomic
def refund_payment(payment, reason: str, refunded_by) -> "Payment":
    """
    Admin-initiated refund.
    Raises ValueError if the payment cannot be refunded.
    """
    try:
        payment.refund(reason=reason, refunded_by=refunded_by)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    send_event("payment.refunded", payment, refunded_by=refunded_by)
    logger.info(
        "payment_refunded | payment_pk=%s | by=%s",
        payment.pk, refunded_by.pk,
    )
    return payment