"""
apps/buyers/models.py  —  FarmicleGrow-Trace Platform

Complete e-commerce model layer — buyers domain only.

Models:
  Buyer               Commercial entity linked to User (role=by)
  BuyerDocument       KYC / compliance documents
  BuyerAddress        Shipping / billing addresses

  Wishlist / WishlistItem   Named wishlists
  Cart / CartItem           Mutable basket with unit_price snapshots

  Order               Confirmed purchase with full status machine
  OrderItem           Immutable line item linked to TraceRecord
  OrderStatusHistory  Append-only transition audit log

  Payment             Financial transaction (mobile money / card / bank)
  PaymentWebhookLog   Raw provider callback store for idempotency

  Coupon / CouponUsage   Discount codes with race-condition-safe usage tracking

  BuyerNotification   In-app notification for order / payment events

Domain boundary:
  ProductReview and ReviewHelpful are NOT here.
  They live in apps.farmers.models — they review a farmers.Product,
  making them a farmers-domain concern, not a buyers-domain concern.
  FarmerCredential is NOT here — it lives in apps.farmers.models.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models.abstract import BaseModel, CodedModel, StatusModel
from apps.core.models.base import BaseOrganisationModel, BaseDocumentModel, BaseTransactionModel
from apps.core.models.managers import (
    VerifiableManager, CartManager, OrderManager, PaymentManager, NotificationManager,
)
from apps.core.models.querysets import (
    VerifiableQuerySet, CartQuerySet, OrderQuerySet, PaymentQuerySet,
    CouponQuerySet, NotificationQuerySet,
)
# ProductReviewQuerySet intentionally NOT imported here —
# it belongs in apps.core.querysets and is used by apps.farmers.models.ProductReview.


# =============================================================================
# BUYER
# =============================================================================

class Buyer(BaseOrganisationModel):
    """
    Marketplace buyer — links one-to-one with a User account (role=by).

    Inherits from BaseOrganisationModel:
      UUID pk · code (BYR-...) · timestamps · soft-delete · verification
      workflow · company_name · registration_number · website ·
      contact_person · phone · email · country · city · address fields.
    """

    CODE_PREFIX = "BYR"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete    = models.PROTECT,
        related_name = "buyer_profile",
    )
    buyer_type = models.CharField(
        max_length = 20,
        choices    = [
            ("company",    _("Company")),
            ("individual", _("Individual Trader")),
            ("ngo",        _("NGO / Non-Profit")),
            ("government", _("Government Agency")),
        ],
        default  = "company",
        db_index = True,
    )
    industry                   = models.CharField(max_length=100, blank=True)
    trade_license_number       = models.CharField(max_length=100, blank=True)
    tax_identification         = models.CharField(max_length=100, blank=True)
    annual_purchase_volume_usd = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
    )
    preferred_products       = models.JSONField(default=list, blank=True)
    preferred_certifications = models.JSONField(default=list, blank=True)
    preferred_origins        = models.JSONField(default=list, blank=True)

    objects = VerifiableManager.from_queryset(VerifiableQuerySet)()

    class Meta(BaseOrganisationModel.Meta):
        verbose_name        = _("Buyer")
        verbose_name_plural = _("Buyers")

    @property
    def buyer_code(self) -> str:
        return self.code

    @property
    def display_name(self) -> str:
        return self.company_name or self.user.get_full_name() or self.user.email

    def __str__(self) -> str:
        return f"{self.code} — {self.company_name}"


# =============================================================================
# BUYER DOCUMENT
# =============================================================================

class BuyerDocument(BaseDocumentModel):
    """KYC / compliance document uploaded by a buyer."""

    DOCUMENT_TYPES = [
        ("certificate_of_incorporation", _("Certificate of Incorporation")),
        ("trade_license",                _("Trade License")),
        ("tax_certificate",              _("Tax Certificate")),
        ("passport",                     _("Passport")),
        ("national_id",                  _("National ID")),
        ("utility_bill",                 _("Utility Bill")),
        ("bank_statement",               _("Bank Statement")),
        ("other",                        _("Other")),
    ]

    buyer         = models.ForeignKey(Buyer, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(
        max_length=40, choices=DOCUMENT_TYPES, default="other", db_index=True,
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="verified_buyer_documents",
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Buyer Document")
        ordering     = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.buyer.company_name} — {self.document_type}"


# =============================================================================
# BUYER ADDRESS
# =============================================================================

class BuyerAddress(BaseModel):
    """Shipping / billing address. One buyer → many; one per type may be default."""

    ADDRESS_TYPES = [
        ("shipping", _("Shipping")),
        ("billing",  _("Billing")),
        ("both",     _("Shipping & Billing")),
    ]

    buyer          = models.ForeignKey(Buyer, on_delete=models.CASCADE, related_name="addresses")
    address_type   = models.CharField(max_length=10, choices=ADDRESS_TYPES, default="both")
    is_default     = models.BooleanField(default=False, db_index=True)
    recipient_name = models.CharField(max_length=150)
    company_name   = models.CharField(max_length=200, blank=True)
    address_line1  = models.CharField(max_length=255)
    address_line2  = models.CharField(max_length=255, blank=True)
    city           = models.CharField(max_length=100)
    state_province = models.CharField(max_length=100, blank=True)
    postal_code    = models.CharField(max_length=20, blank=True)
    country        = models.CharField(max_length=100, db_index=True)
    phone          = models.CharField(max_length=20, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = _("Buyer Address")
        ordering     = ["-is_default", "-created_at"]

    def save(self, *args, **kwargs):
        # Enforce single default per address_type per buyer
        if self.is_default:
            BuyerAddress.objects.filter(
                buyer        = self.buyer,
                address_type = self.address_type,
                is_default   = True,
            ).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.buyer.company_name} — {self.city}, {self.country}"


# =============================================================================
# COUPON  (declared before Cart so Cart can FK to it)
# =============================================================================

class Coupon(BaseModel):
    """
    Discount coupon — percentage (capped) or fixed amount.
    Race-condition safety: CouponUsage uses select_for_update at checkout.
    """

    class DiscountType(models.TextChoices):
        PERCENTAGE = "percentage", _("Percentage")
        FIXED      = "fixed",      _("Fixed Amount")

    code                  = models.CharField(max_length=50, unique=True, db_index=True)
    description           = models.CharField(max_length=255, blank=True)
    discount_type         = models.CharField(
        max_length=15, choices=DiscountType.choices, default=DiscountType.PERCENTAGE,
    )
    discount_value        = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    min_order_value       = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_discount_amount   = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_uses              = models.PositiveIntegerField(null=True, blank=True)
    max_uses_per_buyer    = models.PositiveIntegerField(default=1)
    used_count            = models.PositiveIntegerField(default=0)
    valid_from            = models.DateTimeField()
    valid_until           = models.DateTimeField(null=True, blank=True)
    is_active             = models.BooleanField(default=True, db_index=True)  # type: ignore[override]
    applicable_categories = models.JSONField(default=list, blank=True)

    objects = CouponQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        verbose_name = _("Coupon")

    def __str__(self) -> str:
        return f"{self.code} ({self.discount_type}: {self.discount_value})"

    @property
    def is_currently_valid(self) -> bool:
        now = timezone.now()
        if not self.is_active:                              return False
        if now < self.valid_from:                           return False
        if self.valid_until and now > self.valid_until:     return False
        if self.max_uses and self.used_count >= self.max_uses: return False
        return True

    def validate_for_cart(self, cart: "Cart", buyer: "Buyer | None" = None) -> str:
        """Return error string if invalid, empty string if valid."""
        if not self.is_currently_valid:
            return "This coupon is no longer valid or has reached its usage limit."
        if self.min_order_value and cart.subtotal < self.min_order_value:
            return f"Minimum order of {self.min_order_value} {cart.currency} required."
        if buyer:
            uses = CouponUsage.objects.filter(coupon=self, buyer=buyer).count()
            if uses >= self.max_uses_per_buyer:
                return "You have already used this coupon the maximum number of times."
        return ""

    def compute_discount(self, subtotal: Decimal) -> Decimal:
        if self.discount_type == self.DiscountType.PERCENTAGE:
            d = (subtotal * self.discount_value / Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
            if self.max_discount_amount:
                d = min(d, self.max_discount_amount)
        else:
            d = self.discount_value
        return min(d, subtotal)


# =============================================================================
# WISHLIST
# =============================================================================

class Wishlist(BaseModel):
    """Named wishlist. One buyer → many wishlists; one may be is_default."""

    buyer       = models.ForeignKey(Buyer, on_delete=models.CASCADE, related_name="wishlists")
    name        = models.CharField(max_length=200, default="My Wishlist")
    is_default  = models.BooleanField(default=False, db_index=True)
    is_public   = models.BooleanField(default=False)
    description = models.TextField(blank=True)

    class Meta(BaseModel.Meta):
        verbose_name    = _("Wishlist")
        unique_together = [("buyer", "name")]
        ordering        = ["-is_default", "-created_at"]

    def save(self, *args, **kwargs):
        if self.is_default:
            Wishlist.objects.filter(
                buyer=self.buyer, is_default=True,
            ).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.buyer.company_name} — {self.name}"


class WishlistItem(BaseModel):
    """Product entry inside a wishlist with optional target price and restock alert."""

    wishlist          = models.ForeignKey(Wishlist, on_delete=models.CASCADE, related_name="items")
    product           = models.ForeignKey(
        "farmers.Product", on_delete=models.CASCADE, related_name="wishlist_entries",
    )
    desired_qty_kg    = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    target_price      = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    notes             = models.CharField(max_length=500, blank=True)
    notify_on_restock = models.BooleanField(default=False)

    class Meta(BaseModel.Meta):
        verbose_name    = _("Wishlist Item")
        unique_together = [("wishlist", "product")]
        ordering        = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.wishlist.name} → {self.product.name}"


# =============================================================================
# CART
# =============================================================================

class Cart(BaseModel):
    """
    Mutable shopping basket — one active cart per buyer at any time.
    unit_price is snapshotted on CartItem at add-to-cart time so product
    price changes never silently alter the basket total.
    """

    CART_STATUSES = [
        ("active",      _("Active")),
        ("checked_out", _("Checked Out")),
        ("abandoned",   _("Abandoned")),
        ("expired",     _("Expired")),
    ]

    buyer           = models.ForeignKey(Buyer, on_delete=models.CASCADE, related_name="carts")
    status          = models.CharField(
        max_length=15, choices=CART_STATUSES, default="active", db_index=True,
    )
    currency        = models.CharField(max_length=5, default="GHS")
    coupon          = models.ForeignKey(
        Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name="applied_carts",
    )
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    expires_at      = models.DateTimeField(null=True, blank=True)
    notes           = models.TextField(blank=True)

    objects = CartManager.from_queryset(CartQuerySet)()

    class Meta(BaseModel.Meta):
        verbose_name = _("Cart")
        ordering     = ["-created_at"]

    def __str__(self) -> str:
        return f"Cart [{self.status}] — {self.buyer.company_name}"

    @property
    def subtotal(self) -> Decimal:
        return sum(
            (item.subtotal for item in self.items.filter(is_active=True)),
            Decimal("0.00"),
        )

    @property
    def total(self) -> Decimal:
        return max(self.subtotal - self.discount_amount, Decimal("0.00"))

    @property
    def item_count(self) -> int:
        return self.items.filter(is_active=True).count()

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and timezone.now() > self.expires_at)

    def apply_coupon(self, coupon: Coupon) -> None:
        error = coupon.validate_for_cart(self, self.buyer)
        if error:
            raise ValueError(error)
        self.coupon          = coupon
        self.discount_amount = coupon.compute_discount(self.subtotal)
        self.save(update_fields=["coupon", "discount_amount"])

    def remove_coupon(self) -> None:
        self.coupon          = None
        self.discount_amount = Decimal("0.00")
        self.save(update_fields=["coupon", "discount_amount"])

    def mark_checked_out(self) -> None:
        self.status = "checked_out"
        self.save(update_fields=["status"])

    def mark_abandoned(self) -> None:
        self.status = "abandoned"
        self.save(update_fields=["status"])


class CartItem(BaseModel):
    """
    Single product line in a Cart.
    unit_price snapshotted at add-to-cart time — price changes don't silently
    alter basket total until the buyer explicitly refreshes.
    """

    cart        = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product     = models.ForeignKey(
        "farmers.Product", on_delete=models.PROTECT, related_name="cart_items",
    )
    quantity_kg = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit_price  = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text=_("Price per kg snapshotted at add-to-cart time."),
    )
    currency    = models.CharField(max_length=5, default="GHS")
    notes       = models.CharField(max_length=300, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name    = _("Cart Item")
        unique_together = [("cart", "product")]
        ordering        = ["-created_at"]

    @property
    def subtotal(self) -> Decimal:
        return (self.quantity_kg * self.unit_price).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        )

    def validate_stock(self) -> None:
        p = self.product
        if not p.is_available:
            raise ValueError(f"'{p.name}' is not currently available.")
        if p.stock_kg < self.quantity_kg:
            raise ValueError(
                f"Only {p.stock_kg} kg of '{p.name}' in stock; {self.quantity_kg} kg requested."
            )
        if self.quantity_kg < p.min_order_kg:
            raise ValueError(f"Minimum order for '{p.name}' is {p.min_order_kg} kg.")

    def __str__(self) -> str:
        return f"{self.product.name} × {self.quantity_kg} kg @ {self.unit_price}"


# =============================================================================
# ORDER
# =============================================================================

class Order(BaseModel, CodedModel, StatusModel):
    """
    Confirmed purchase order.

    Status machine:
        pending → confirmed → processing → dispatched → delivered
                                                      ↘ cancelled

    Stock is deducted at confirm(), restored at cancel() for pre-dispatch orders.
    All monetary columns are snapshotted — the order is a self-contained
    financial record.
    """

    CODE_PREFIX = "ORD"

    class OrderStatus(models.TextChoices):
        PENDING    = "pending",    _("Pending")
        CONFIRMED  = "confirmed",  _("Confirmed")
        PROCESSING = "processing", _("Processing")
        DISPATCHED = "dispatched", _("Dispatched")
        DELIVERED  = "delivered",  _("Delivered")
        CANCELLED  = "cancelled",  _("Cancelled")
        REFUNDED   = "refunded",   _("Refunded")

    class PaymentStatus(models.TextChoices):
        UNPAID   = "unpaid",   _("Unpaid")
        PARTIAL  = "partial",  _("Partially Paid")
        PAID     = "paid",     _("Paid")
        REFUNDED = "refunded", _("Refunded")
        FAILED   = "failed",   _("Payment Failed")

    STATUS_CHOICES = OrderStatus.choices
    status = models.CharField(
        max_length=15, choices=OrderStatus.choices,
        default=OrderStatus.PENDING, db_index=True,
    )

    buyer            = models.ForeignKey(Buyer, on_delete=models.PROTECT, related_name="orders")
    shipping_address = models.ForeignKey(
        BuyerAddress, on_delete=models.PROTECT,
        related_name="shipping_orders", null=True, blank=True,
    )
    billing_address  = models.ForeignKey(
        BuyerAddress, on_delete=models.PROTECT,
        related_name="billing_orders", null=True, blank=True,
    )
    coupon           = models.ForeignKey(
        Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders",
    )

    # ── Financials (immutable after confirmation) ─────────────────────────────
    currency         = models.CharField(max_length=5, default="GHS")
    subtotal         = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    discount_amount  = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    shipping_amount  = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax_amount       = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_amount     = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    coupon_code_used = models.CharField(max_length=50, blank=True)
    payment_status   = models.CharField(
        max_length=10, choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID, db_index=True,
    )
    amount_paid      = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    # ── Logistics ─────────────────────────────────────────────────────────────
    expected_delivery_date = models.DateField(null=True, blank=True)
    actual_delivery_date   = models.DateField(null=True, blank=True)
    tracking_number        = models.CharField(max_length=100, blank=True, db_index=True)
    carrier_name           = models.CharField(max_length=100, blank=True)
    destination_country    = models.CharField(max_length=100, blank=True, db_index=True)
    dispatch_notes         = models.TextField(blank=True)

    buyer_notes      = models.TextField(blank=True)
    internal_notes   = models.TextField(blank=True)
    cancelled_reason = models.TextField(blank=True)

    confirmed_at  = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    delivered_at  = models.DateTimeField(null=True, blank=True)
    cancelled_at  = models.DateTimeField(null=True, blank=True)

    objects = OrderManager.from_queryset(OrderQuerySet)()

    class Meta(BaseModel.Meta):
        verbose_name        = _("Order")
        verbose_name_plural = _("Orders")
        ordering            = ["-created_at"]
        indexes             = [
            models.Index(fields=["buyer",            "status"]),
            models.Index(fields=["payment_status",   "status"]),
            models.Index(fields=["destination_country", "status"]),
        ]

    @property
    def balance_due(self) -> Decimal:
        return max(self.total_amount - self.amount_paid, Decimal("0.00"))

    @property
    def is_cancellable(self) -> bool:
        return self.status in (
            self.OrderStatus.PENDING,
            self.OrderStatus.CONFIRMED,
            self.OrderStatus.PROCESSING,
        )

    def compute_totals(self) -> None:
        self.subtotal = sum(
            (item.subtotal for item in self.items.filter(is_active=True)),
            Decimal("0.00"),
        )
        self.total_amount = max(
            self.subtotal - self.discount_amount + self.shipping_amount + self.tax_amount,
            Decimal("0.00"),
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.save(update_fields=["subtotal", "total_amount"])

    @transaction.atomic
    def confirm(self, confirmed_by=None) -> None:
        if self.status != self.OrderStatus.PENDING:
            raise ValueError(f"Cannot confirm order in status '{self.status}'.")
        for item in self.items.select_related("product").filter(is_active=True):
            prod = item.product.__class__.objects.select_for_update().get(pk=item.product_id)
            if prod.stock_kg < item.quantity_kg:
                raise ValueError(
                    f"Insufficient stock for '{prod.name}': {prod.stock_kg} kg available."
                )
            prod.stock_kg -= item.quantity_kg
            prod.save(update_fields=["stock_kg"])
        self.status       = self.OrderStatus.CONFIRMED
        self.confirmed_at = timezone.now()
        self.save(update_fields=["status", "confirmed_at"])
        OrderStatusHistory.objects.create(
            order      = self,
            old_status = self.OrderStatus.PENDING,
            new_status = self.OrderStatus.CONFIRMED,
            changed_by = confirmed_by,
        )
        from apps.core.signals import send_event
        send_event("order.confirmed", self)

    def dispatch(self, tracking_number: str = "", carrier: str = "", changed_by=None) -> None:
        if self.status not in (self.OrderStatus.CONFIRMED, self.OrderStatus.PROCESSING):
            raise ValueError(f"Cannot dispatch order in status '{self.status}'.")
        old_status           = self.status
        self.status          = self.OrderStatus.DISPATCHED
        self.dispatched_at   = timezone.now()
        self.tracking_number = tracking_number
        self.carrier_name    = carrier
        self.save(update_fields=["status", "dispatched_at", "tracking_number", "carrier_name"])
        OrderStatusHistory.objects.create(
            order=self, old_status=old_status,
            new_status=self.OrderStatus.DISPATCHED, changed_by=changed_by,
        )
        from apps.core.signals import send_event
        send_event("order.dispatched", self)

    def mark_delivered(self, changed_by=None) -> None:
        if self.status != self.OrderStatus.DISPATCHED:
            raise ValueError(f"Cannot mark delivered from status '{self.status}'.")
        old_status                = self.status
        self.status               = self.OrderStatus.DELIVERED
        self.delivered_at         = timezone.now()
        self.actual_delivery_date = timezone.now().date()
        self.save(update_fields=["status", "delivered_at", "actual_delivery_date"])
        OrderStatusHistory.objects.create(
            order=self, old_status=old_status,
            new_status=self.OrderStatus.DELIVERED, changed_by=changed_by,
        )
        from apps.core.signals import send_event
        send_event("order.delivered", self)

    @transaction.atomic
    def cancel(self, reason: str = "", changed_by=None) -> None:
        if not self.is_cancellable:
            raise ValueError(f"Order cannot be cancelled in status '{self.status}'.")
        old_status = self.status
        # Restore stock for confirmed/processing orders
        if old_status in (self.OrderStatus.CONFIRMED, self.OrderStatus.PROCESSING):
            for item in self.items.select_related("product").filter(is_active=True):
                prod = item.product.__class__.objects.select_for_update().get(pk=item.product_id)
                prod.stock_kg += item.quantity_kg
                prod.save(update_fields=["stock_kg"])
        self.status           = self.OrderStatus.CANCELLED
        self.cancelled_at     = timezone.now()
        self.cancelled_reason = reason
        self.save(update_fields=["status", "cancelled_at", "cancelled_reason"])
        OrderStatusHistory.objects.create(
            order=self, old_status=old_status,
            new_status=self.OrderStatus.CANCELLED, changed_by=changed_by, note=reason,
        )

    def record_payment(self, amount: Decimal) -> None:
        self.amount_paid = (self.amount_paid + amount).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        )
        if self.amount_paid >= self.total_amount:
            self.payment_status = self.PaymentStatus.PAID
        elif self.amount_paid > 0:
            self.payment_status = self.PaymentStatus.PARTIAL
        self.save(update_fields=["amount_paid", "payment_status"])

    def __str__(self) -> str:
        return f"{self.code} — {self.buyer.company_name} [{self.status}]"


# =============================================================================
# ORDER ITEM
# =============================================================================

class OrderItem(BaseModel):
    """
    Immutable order line item.
    Stores its own financial data so the order is a complete record regardless
    of future product price changes. trace_record links to the full chain.
    """

    order        = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product      = models.ForeignKey(
        "farmers.Product", on_delete=models.PROTECT, related_name="order_items",
    )
    trace_record = models.ForeignKey(
        "traceability.TraceRecord", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="order_items",
    )
    quantity_kg  = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit_price   = models.DecimalField(max_digits=10, decimal_places=2)
    currency     = models.CharField(max_length=5, default="GHS")
    subtotal     = models.DecimalField(max_digits=14, decimal_places=2)
    product_name = models.CharField(max_length=200, blank=True)
    product_code = models.CharField(max_length=30,  blank=True)
    notes        = models.CharField(max_length=500,  blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = _("Order Item")
        ordering     = ["created_at"]

    def save(self, *args, **kwargs):
        self.subtotal = (self.quantity_kg * self.unit_price).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        )
        if self.product_id and not self.product_name:
            self.product_name = self.product.name
            self.product_code = getattr(self.product, "code", "")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.order.code} → {self.product_name} × {self.quantity_kg} kg"


# =============================================================================
# ORDER STATUS HISTORY  (append-only)
# =============================================================================

class OrderStatusHistory(models.Model):
    """Append-only log of every order status transition. Never deleted."""

    order      = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_history")
    old_status = models.CharField(max_length=15)
    new_status = models.CharField(max_length=15)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="order_status_changes",
    )
    changed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    note       = models.TextField(blank=True)

    class Meta:
        verbose_name = _("Order Status History")
        ordering     = ["changed_at"]

    def __str__(self) -> str:
        return f"{self.order.code}: {self.old_status} → {self.new_status}"


# =============================================================================
# PAYMENT
# =============================================================================

class Payment(BaseTransactionModel):
    """
    Financial transaction for an order.
    mark_completed() uses select_for_update to prevent double-processing
    of concurrent webhook retries.
    """

    CODE_PREFIX = "PAY"

    class TransactionStatus(models.TextChoices):
        PENDING   = "pending",   _("Pending")
        COMPLETED = "completed", _("Completed")
        FAILED    = "failed",    _("Failed")
        REFUNDED  = "refunded",  _("Refunded")
        CANCELLED = "cancelled", _("Cancelled")

    class PaymentChannel(models.TextChoices):
        MOBILE_MONEY  = "mobile_money",  _("Mobile Money")
        CARD          = "card",          _("Card")
        BANK_TRANSFER = "bank_transfer", _("Bank Transfer")
        PAYSTACK      = "paystack",      _("Paystack")
        FLUTTERWAVE   = "flutterwave",   _("Flutterwave")
        STRIPE        = "stripe",        _("Stripe")

    STATUS_CHOICES = TransactionStatus.choices
    status = models.CharField(
        max_length=15, choices=TransactionStatus.choices,
        default=TransactionStatus.PENDING, db_index=True,
    )

    order                   = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payments")
    buyer                   = models.ForeignKey(Buyer, on_delete=models.PROTECT, related_name="payments")
    payment_channel         = models.CharField(
        max_length=20, choices=PaymentChannel.choices, db_index=True,
    )
    mobile_money_number     = models.CharField(max_length=20, blank=True)
    mobile_money_network    = models.CharField(
        max_length=20, blank=True,
        choices=[("mtn", "MTN"), ("vodafone", "Vodafone"), ("airteltigo", "AirtelTigo")],
    )
    card_last_four          = models.CharField(max_length=4,   blank=True)
    card_brand              = models.CharField(max_length=20,  blank=True)
    provider_reference      = models.CharField(max_length=200, blank=True, db_index=True)
    provider_transaction_id = models.CharField(max_length=200, blank=True, unique=True, null=True)
    payment_date            = models.DateTimeField(null=True, blank=True, db_index=True)
    receipt_url             = models.URLField(blank=True)
    failure_reason          = models.TextField(blank=True)
    refund_reason           = models.TextField(blank=True)
    refunded_at             = models.DateTimeField(null=True, blank=True)

    objects = PaymentManager.from_queryset(PaymentQuerySet)()

    class Meta(BaseTransactionModel.Meta):
        verbose_name        = _("Payment")
        verbose_name_plural = _("Payments")
        ordering            = ["-created_at"]
        indexes             = [models.Index(fields=["order", "status"])]

    @transaction.atomic
    def mark_completed(self, provider_ref: str = "", payment_dt=None) -> None:
        """Idempotent via select_for_update — safe against concurrent webhook retries."""
        locked = Payment.objects.select_for_update().get(pk=self.pk)
        if locked.status == self.TransactionStatus.COMPLETED:
            return
        locked.status             = self.TransactionStatus.COMPLETED
        locked.provider_reference = provider_ref or locked.provider_reference
        locked.payment_date       = payment_dt or timezone.now()
        locked.save(update_fields=["status", "provider_reference", "payment_date"])
        locked.order.record_payment(locked.amount)
        from apps.core.signals import send_event
        send_event("payment.completed", locked)

    def mark_failed(self, reason: str = "") -> None:
        self.status         = self.TransactionStatus.FAILED
        self.failure_reason = reason
        self.save(update_fields=["status", "failure_reason"])

    @transaction.atomic
    def refund(self, reason: str, refunded_by=None) -> None:
        if self.status != self.TransactionStatus.COMPLETED:
            raise ValueError("Only completed payments can be refunded.")
        self.status        = self.TransactionStatus.REFUNDED
        self.refund_reason = reason
        self.refunded_at   = timezone.now()
        self.save(update_fields=["status", "refund_reason", "refunded_at"])
        self.order.payment_status = Order.PaymentStatus.REFUNDED
        self.order.save(update_fields=["payment_status"])

    def __str__(self) -> str:
        return f"{self.code} — {self.buyer.display_name} [{self.status}]"


# =============================================================================
# PAYMENT WEBHOOK LOG
# =============================================================================

class PaymentWebhookLog(models.Model):
    """
    Raw store for every incoming provider webhook event.
    Deduplicated by event_id — prevents double-processing on provider retries.
    """

    provider         = models.CharField(max_length=20, db_index=True)
    event_id         = models.CharField(max_length=200, unique=True, db_index=True)
    event_type       = models.CharField(max_length=100)
    raw_payload      = models.JSONField()
    signature_valid  = models.BooleanField(default=False)
    processed        = models.BooleanField(default=False, db_index=True)
    processing_error = models.TextField(blank=True)
    received_at      = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at     = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Payment Webhook Log")
        ordering     = ["-received_at"]

    def mark_processed(self, error: str = "") -> None:
        self.processed        = not bool(error)
        self.processing_error = error
        self.processed_at     = timezone.now()
        self.save(update_fields=["processed", "processing_error", "processed_at"])

    def __str__(self) -> str:
        return f"{self.provider}/{self.event_type} [{self.event_id}]"


# =============================================================================
# COUPON USAGE
# =============================================================================

class CouponUsage(BaseModel):
    """
    Atomic coupon redemption log.
    select_for_update on Coupon at checkout prevents race-condition double-use.
    """

    coupon       = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="usages")
    buyer        = models.ForeignKey(Buyer,  on_delete=models.CASCADE, related_name="coupon_usages")
    order        = models.ForeignKey(
        Order, on_delete=models.SET_NULL, null=True, related_name="coupon_usages",
    )
    used_at      = models.DateTimeField(auto_now_add=True)
    amount_saved = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    class Meta(BaseModel.Meta):
        verbose_name = _("Coupon Usage")
        ordering     = ["-used_at"]

    def __str__(self) -> str:
        return f"{self.coupon.code} used by {self.buyer.company_name}"


# =============================================================================
# BUYER NOTIFICATION
# =============================================================================

class BuyerNotification(BaseModel):
    """In-app notification for order / payment / restock events."""

    class NotificationType(models.TextChoices):
        ORDER_CONFIRMED  = "order_confirmed",  _("Order Confirmed")
        ORDER_DISPATCHED = "order_dispatched", _("Order Dispatched")
        ORDER_DELIVERED  = "order_delivered",  _("Order Delivered")
        ORDER_CANCELLED  = "order_cancelled",  _("Order Cancelled")
        PAYMENT_SUCCESS  = "payment_success",  _("Payment Successful")
        PAYMENT_FAILED   = "payment_failed",   _("Payment Failed")
        RESTOCK_ALERT    = "restock_alert",    _("Product Restocked")
        REVIEW_REPLY     = "review_reply",     _("Reply on your Review")
        PROMO            = "promo",            _("Promotion / Offer")
        SYSTEM           = "system",           _("System Message")

    buyer             = models.ForeignKey(Buyer, on_delete=models.CASCADE, related_name="notifications")
    notification_type = models.CharField(
        max_length=25, choices=NotificationType.choices, db_index=True,
    )
    title               = models.CharField(max_length=200)
    message             = models.TextField()
    is_read             = models.BooleanField(default=False, db_index=True)
    read_at             = models.DateTimeField(null=True, blank=True)
    related_object_type = models.CharField(max_length=50, blank=True)
    related_object_id   = models.CharField(max_length=50, blank=True)

    objects = NotificationManager.from_queryset(NotificationQuerySet)()

    class Meta(BaseModel.Meta):
        verbose_name = _("Buyer Notification")
        ordering     = ["-created_at"]
        indexes      = [models.Index(fields=["buyer", "is_read"])]

    def mark_read(self) -> None:
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at"])

    def __str__(self) -> str:
        return f"[{self.notification_type}] {self.title} → {self.buyer.company_name}"