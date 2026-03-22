"""
apps/buyers/views.py  —  FarmicleGrow-Trace Platform

All buyer-domain ViewSets in one file.

ViewSets:
  BuyerViewSet              — profile CRUD + KYC docs + addresses + orders summary
  WishlistViewSet           — wishlists + item management + move-to-cart
  CartViewSet               — single active cart per buyer
  OrderViewSet              — order lifecycle + reorder + tracking
  PaymentViewSet            — initiate + webhook + refund + receipt
  CouponViewSet             — admin CRUD + public validate
  BuyerNotificationViewSet  — in-app notifications

Design:
  • Views contain zero business logic — all mutations delegate to services.py.
  • send_event() is called from services, not views.
  • All inline permission checks replaced with permission classes from permissions.py.
  • transaction.atomic lives in services, not views.
  • Hardcoded 'GHS' replaced — services read settings.DEFAULT_CURRENCY.
  • Webhook processing offloaded to Celery (tasks.process_payment_webhook).
  • Throttling applied to checkout, payment initiation, and webhook endpoints.
  • F() expressions used for all atomic counter updates.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.db.models import F
from django.utils import timezone

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from apps.buyers import services
from apps.buyers.models import (
    Buyer, BuyerNotification,
    Cart, CartItem,
    Coupon, CouponUsage,
    Order,
    Payment, PaymentWebhookLog,
    Wishlist, WishlistItem,
)
# ProductReview and ReviewHelpful are NOT imported here.
# They live in apps.farmers — see apps/farmers/views.py::ProductReviewViewSet.
from apps.buyers.permissions import (
    IsBuyerOwner,
    IsOrderOwnerOrAdmin,
    IsReviewOwnerOrAdmin,
    IsVerifiedBuyer,
)
from apps.buyers.serializers import (
    BuyerAddressSerializer, BuyerAddressWriteSerializer,
    BuyerCreateSerializer, BuyerDocumentSerializer,
    BuyerDocumentUploadSerializer, BuyerListSerializer,
    BuyerNotificationSerializer,
    BuyerSerializer, BuyerUpdateSerializer,
    CartItemAddSerializer, CartSerializer,
    CouponApplySerializer, CouponSerializer,
    CouponValidateSerializer, CouponWriteSerializer,
    OrderCancelSerializer, OrderDispatchSerializer,
    OrderListSerializer, OrderSerializer,
    OrderStatusHistorySerializer,
    PaymentInitiateSerializer, PaymentRefundSerializer, PaymentSerializer,
    WishlistItemWriteSerializer, WishlistSerializer, WishlistWriteSerializer,
)
# ProductReviewSerializer / ProductReviewCreateSerializer live in
# apps.farmers.serializers — imported there by ProductReviewViewSet.
from apps.core.models.mixins import (
    AuditCreateMixin, DateRangeFilterMixin, RoleQuerySetMixin,
    SoftDeleteMixin, VerificationActionMixin,
)
from apps.core.signals import send_event

logger = logging.getLogger("apps.buyers")


# =============================================================================
# THROTTLES
# =============================================================================

class CheckoutThrottle(UserRateThrottle):
    """10 checkout attempts per hour per user."""
    rate = "10/hour"


class PaymentInitiateThrottle(UserRateThrottle):
    """20 payment initiations per hour per user."""
    rate = "20/hour"


class WebhookThrottle(AnonRateThrottle):
    """200 webhook requests per minute (absorbs provider burst retries)."""
    rate = "200/min"


# =============================================================================
# HELPERS
# =============================================================================

def _require_buyer(request) -> Buyer:
    """Return buyer profile or raise 403. Used in cart/payment views."""
    try:
        return services.get_buyer_or_raise(request.user)
    except PermissionError as exc:
        raise PermissionDenied(str(exc)) from exc


# =============================================================================
# BUYER PROFILE VIEWSET
# =============================================================================

class BuyerViewSet(
    RoleQuerySetMixin, AuditCreateMixin,
    SoftDeleteMixin, VerificationActionMixin,
    viewsets.ModelViewSet,
):
    """
    Endpoint                                Method  Permission
    ──────────────────────────────────────────────────────────
    /v1/buyers/                             GET     admin / own
    /v1/buyers/                             POST    authenticated
    /v1/buyers/<id>/                        GET     authenticated
    /v1/buyers/<id>/                        PUT     owner
    /v1/buyers/<id>/                        DELETE  owner (soft)
    /v1/buyers/<id>/verify/                 POST    admin
    /v1/buyers/<id>/reject/                 POST    admin
    /v1/buyers/<id>/documents/              GET     owner / admin
    /v1/buyers/<id>/upload-document/        POST    owner
    /v1/buyers/<id>/addresses/              GET/POST owner
    /v1/buyers/<id>/orders/                 GET     owner / admin
    """

    queryset         = Buyer.objects.all().select_related("user")
    serializer_class = BuyerSerializer
    filter_backends  = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["buyer_type", "country", "verification_status", "is_active"]
    search_fields    = ["company_name", "contact_person", "email", "code"]
    ordering_fields  = ["company_name", "created_at", "verification_status"]
    ordering         = ["-created_at"]

    def get_permissions(self):
        if self.action == "create":
            return [permissions.IsAuthenticated()]
        if self.action in ("update", "partial_update", "destroy",
                           "documents", "upload_document", "addresses"):
            return [permissions.IsAuthenticated(), IsBuyerOwner()]
        if self.action in ("verify", "reject", "suspend"):
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == "create":                         return BuyerCreateSerializer
        if self.action in ("update", "partial_update"):     return BuyerUpdateSerializer
        if self.action == "list":                           return BuyerListSerializer
        return BuyerSerializer

    def get_admin_queryset(self, qs):   return qs
    def get_hr_queryset(self, qs):      return qs
    def get_buyer_queryset(self, qs):
      if getattr(self, "swagger_fake_view", False):
        return qs.none()
      return qs.filter(user=self.request.user)

    def perform_create(self, serializer):
        buyer = serializer.save()
        send_event("buyer.registered", buyer)

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        return super().verify(request, pk)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return super().reject(request, pk)

    @action(detail=True, methods=["get"])
    def documents(self, request, pk=None):
        buyer = self.get_object()
        return Response(
            BuyerDocumentSerializer(
                buyer.documents.all().order_by("-uploaded_at"), many=True,
            ).data
        )

    @action(detail=True, methods=["post"], url_path="upload-document")
    def upload_document(self, request, pk=None):
        buyer = self.get_object()
        ser   = BuyerDocumentUploadSerializer(
            data=request.data, context={"request": request},
        )
        ser.is_valid(raise_exception=True)
        doc = ser.save(buyer=buyer)
        return Response(BuyerDocumentSerializer(doc).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"])
    def addresses(self, request, pk=None):
        buyer = self.get_object()
        if request.method == "POST":
            ser = BuyerAddressWriteSerializer(
                data=request.data, context={"request": request},
            )
            ser.is_valid(raise_exception=True)
            addr = ser.save(buyer=buyer)
            return Response(BuyerAddressSerializer(addr).data, status=status.HTTP_201_CREATED)
        return Response(BuyerAddressSerializer(buyer.addresses.all(), many=True).data)

    @action(detail=True, methods=["get"])
    def orders(self, request, pk=None):
        buyer = self.get_object()
        if buyer.user_id != request.user.pk and not request.user.is_staff:
            raise PermissionDenied
        orders = (
            Order.objects.filter(buyer=buyer)
            .select_related("shipping_address")
            .order_by("-created_at")[:20]
        )
        return Response(OrderListSerializer(orders, many=True).data)


# =============================================================================
# WISHLIST VIEWSET
# =============================================================================

class WishlistViewSet(AuditCreateMixin, SoftDeleteMixin, viewsets.ModelViewSet):
    """
    Endpoint                                          Method  Permission
    ─────────────────────────────────────────────────────────────────────
    /v1/wishlists/                                    GET     owner
    /v1/wishlists/                                    POST    owner
    /v1/wishlists/<id>/                               GET     owner
    /v1/wishlists/<id>/                               DELETE  owner (soft)
    /v1/wishlists/<id>/add-item/                      POST    owner
    /v1/wishlists/<id>/remove-item/<product_id>/      DELETE  owner
    /v1/wishlists/<id>/move-to-cart/                  POST    owner
    """

    serializer_class   = WishlistSerializer
    permission_classes = [permissions.IsAuthenticated, IsBuyerOwner]

    def get_queryset(self):
        buyer = getattr(self.request.user, "buyer_profile", None)
        if not buyer:
            return Wishlist.objects.none()
        return Wishlist.objects.filter(
            buyer=buyer, is_active=True,
        ).prefetch_related("items__product")

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return WishlistWriteSerializer
        return WishlistSerializer

    def perform_create(self, serializer):
        serializer.save(buyer=self.request.user.buyer_profile)

    @action(detail=True, methods=["post"], url_path="add-item")
    def add_item(self, request, pk=None):
        wishlist = self.get_object()
        ser = WishlistItemWriteSerializer(
            data=request.data, context={"request": request},
        )
        ser.is_valid(raise_exception=True)
        _, created = WishlistItem.objects.update_or_create(
            wishlist   = wishlist,
            product_id = ser.validated_data["product"].pk,
            defaults   = {k: v for k, v in ser.validated_data.items() if k != "product"},
        )
        return Response(
            {"detail": "Item added to wishlist."},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(
        detail=True, methods=["delete"],
        url_path=r"remove-item/(?P<product_id>[^/.]+)",
    )
    def remove_item(self, request, pk=None, product_id=None):
        wishlist   = self.get_object()
        deleted, _ = WishlistItem.objects.filter(
            wishlist=wishlist, product_id=product_id,
        ).delete()
        if not deleted:
            raise NotFound("Item not found in this wishlist.")
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="move-to-cart")
    def move_to_cart(self, request, pk=None):
        """Move all available wishlist items into the buyer's active cart."""
        wishlist = self.get_object()
        buyer    = _require_buyer(request)
        cart     = services.get_or_create_cart(buyer)

        added, skipped = 0, 0
        for item in wishlist.items.select_related("product").filter(is_active=True):
            try:
                services.add_item(cart, item.product, item.desired_qty_kg or item.product.min_order_kg)
                added += 1
            except ValueError:
                skipped += 1

        return Response({"added": added, "skipped_unavailable": skipped})


# =============================================================================
# CART VIEWSET
# =============================================================================

class CartViewSet(viewsets.ViewSet):
    """
    Endpoint                          Method  Permission
    ────────────────────────────────────────────────────
    /v1/cart/                         GET     authenticated
    /v1/cart/add-item/                POST    authenticated
    /v1/cart/update-item/             PATCH   authenticated
    /v1/cart/remove-item/             DELETE  authenticated
    /v1/cart/clear/                   POST    authenticated
    /v1/cart/apply-coupon/            POST    authenticated
    /v1/cart/remove-coupon/           DELETE  authenticated
    /v1/cart/checkout/                POST    verified buyer  (10/hr throttle)
    """

    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        buyer = _require_buyer(request)
        cart  = services.get_or_create_cart(buyer)
        return Response(CartSerializer(cart, context={"request": request}).data)

    @action(detail=False, methods=["post"], url_path="add-item")
    def add_item(self, request):
        buyer = _require_buyer(request)
        cart  = services.get_or_create_cart(buyer)
        ser   = CartItemAddSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        try:
            cart = services.add_item(
                cart        = cart,
                product     = ser.validated_data["product"],
                quantity_kg = ser.validated_data["quantity_kg"],
                notes       = ser.validated_data.get("notes", ""),
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(CartSerializer(cart, context={"request": request}).data)

    @action(detail=False, methods=["patch"], url_path="update-item")
    def update_item(self, request):
        buyer      = _require_buyer(request)
        cart       = services.get_or_create_cart(buyer)
        product_id = request.data.get("product_id")
        qty        = request.data.get("quantity_kg")
        if not product_id or qty is None:
            raise ValidationError({"detail": "product_id and quantity_kg are required."})
        try:
            cart = services.update_item(cart, product_id, Decimal(str(qty)))
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(CartSerializer(cart, context={"request": request}).data)

    @action(detail=False, methods=["delete"], url_path="remove-item")
    def remove_item(self, request):
        buyer      = _require_buyer(request)
        cart       = services.get_or_create_cart(buyer)
        product_id = request.data.get("product_id") or request.query_params.get("product_id")
        if not product_id:
            raise ValidationError({"detail": "product_id is required."})
        try:
            cart = services.remove_item(cart, product_id)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(CartSerializer(cart, context={"request": request}).data)

    @action(detail=False, methods=["post"])
    def clear(self, request):
        buyer = _require_buyer(request)
        cart  = services.get_or_create_cart(buyer)
        services.clear_cart(cart)
        return Response({"detail": "Cart cleared."})

    @action(detail=False, methods=["post"], url_path="apply-coupon")
    def apply_coupon(self, request):
        buyer = _require_buyer(request)
        cart  = services.get_or_create_cart(buyer)
        ser   = CouponApplySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            cart = services.apply_coupon(cart, ser.coupon_instance)
        except ValueError as exc:
            raise ValidationError({"code": str(exc)}) from exc
        return Response(CartSerializer(cart, context={"request": request}).data)

    @action(detail=False, methods=["delete"], url_path="remove-coupon")
    def remove_coupon(self, request):
        buyer = _require_buyer(request)
        cart  = services.get_or_create_cart(buyer)
        cart  = services.remove_coupon(cart)
        return Response(CartSerializer(cart, context={"request": request}).data)

    @action(
        detail=False, methods=["post"],
        permission_classes=[permissions.IsAuthenticated, IsVerifiedBuyer],
        throttle_classes=[CheckoutThrottle],
    )
    def checkout(self, request):
        """
        POST /v1/cart/checkout/
        Converts active cart → pending Order.
        Requires verified buyer. Throttled to 10/hr.
        """
        buyer = _require_buyer(request)
        cart  = Cart.objects.filter(
            buyer=buyer, status="active",
        ).order_by("-created_at").first()
        try:
            order = services.create_order_from_cart(
                cart                = cart,
                buyer               = buyer,
                delivery_address_id = request.data.get("delivery_address"),
                billing_address_id  = request.data.get("billing_address"),
                notes               = request.data.get("notes", ""),
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(
            OrderSerializer(order, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


# =============================================================================
# ORDER VIEWSET
# =============================================================================

class OrderViewSet(DateRangeFilterMixin, viewsets.ModelViewSet):
    """
    Endpoint                                Method  Permission
    ──────────────────────────────────────────────────────────
    /v1/orders/                             GET     authenticated
    /v1/orders/<id>/                        GET     owner / admin
    /v1/orders/<id>/confirm/                POST    admin
    /v1/orders/<id>/dispatch/               POST    admin
    /v1/orders/<id>/deliver/                POST    admin
    /v1/orders/<id>/cancel/                 POST    owner / admin
    /v1/orders/<id>/track/                  GET     owner / admin
    /v1/orders/<id>/reorder/                POST    owner
    /v1/orders/<id>/status-history/         GET     owner / admin
    """

    serializer_class = OrderSerializer
    filter_backends  = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status", "payment_status", "destination_country"]
    search_fields    = ["code", "buyer__company_name", "tracking_number"]
    ordering_fields  = ["created_at", "total_amount", "status"]
    ordering         = ["-created_at"]
    date_range_field = "created_at"

    def get_permissions(self):
        if self.action in ("confirm", "dispatch", "deliver"):
            return [permissions.IsAdminUser()]
        if self.action in ("cancel", "retrieve", "track", "reorder", "status_history"):
            return [permissions.IsAuthenticated(), IsOrderOwnerOrAdmin()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        qs = Order.objects.select_related(
            "buyer", "shipping_address", "billing_address", "coupon",
        ).prefetch_related("items__product", "status_history", "items__trace_record")
        if self.request.user.is_staff or self.request.user.is_superuser:
            return qs
        buyer = getattr(self.request.user, "buyer_profile", None)
        return qs.filter(buyer=buyer) if buyer else Order.objects.none()

    def get_serializer_class(self):
        if self.action == "list": return OrderListSerializer
        return OrderSerializer

    def create(self, request, *args, **kwargs):
        raise ValidationError({"detail": "Orders must be created through /v1/cart/checkout/."})

    def update(self, request, *args, **kwargs):
        raise ValidationError({"detail": "Orders cannot be directly updated. Use action endpoints."})

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        order = self.get_object()
        try:
            order = services.confirm_order(order, confirmed_by=request.user)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(OrderSerializer(order, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def dispatch(self, request, pk=None):
        order = self.get_object()
        ser   = OrderDispatchSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            order = services.dispatch_order(
                order           = order,
                tracking_number = ser.validated_data.get("tracking_number", ""),
                carrier         = ser.validated_data.get("carrier_name", ""),
                dispatch_notes  = ser.validated_data.get("dispatch_notes", ""),
                dispatched_by   = request.user,
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(OrderSerializer(order, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def deliver(self, request, pk=None):
        order = self.get_object()
        try:
            order = services.deliver_order(order, delivered_by=request.user)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(OrderSerializer(order, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        ser   = OrderCancelSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            order = services.cancel_order(
                order        = order,
                reason       = ser.validated_data.get("reason", ""),
                cancelled_by = request.user,
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(OrderSerializer(order, context={"request": request}).data)

    @action(detail=True, methods=["get"])
    def track(self, request, pk=None):
        order = self.get_object()
        return Response({
            "order_code":             order.code,
            "status":                 order.status,
            "payment_status":         order.payment_status,
            "tracking_number":        order.tracking_number,
            "carrier_name":           order.carrier_name,
            "expected_delivery_date": order.expected_delivery_date,
            "actual_delivery_date":   order.actual_delivery_date,
            "dispatched_at":          order.dispatched_at,
            "delivered_at":           order.delivered_at,
            "destination_country":    order.destination_country,
        })

    @action(detail=True, methods=["post"])
    def reorder(self, request, pk=None):
        order = self.get_object()
        buyer = _require_buyer(request)
        if order.buyer_id != buyer.pk:
            raise PermissionDenied
        cart, added, skipped = services.reorder(order, buyer)
        return Response(
            {
                "detail": f"Reorder cart created with {added} item(s). {skipped} unavailable.",
                "cart":   CartSerializer(cart, context={"request": request}).data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"], url_path="status-history")
    def status_history(self, request, pk=None):
        order = self.get_object()
        return Response(
            OrderStatusHistorySerializer(
                order.status_history.all().order_by("created_at"), many=True,
            ).data
        )


# =============================================================================
# PAYMENT VIEWSET
# =============================================================================

class PaymentViewSet(viewsets.ModelViewSet):
    """
    Endpoint                          Method  Permission
    ─────────────────────────────────────────────────────
    /v1/payments/                     GET     authenticated
    /v1/payments/<id>/                GET     authenticated
    /v1/payments/initiate/            POST    authenticated (20/hr throttle)
    /v1/payments/webhook/             POST    AllowAny (200/min throttle, sig verified)
    /v1/payments/<id>/refund/         POST    admin
    /v1/payments/<id>/receipt/        GET     authenticated
    """

    serializer_class = PaymentSerializer
    filter_backends  = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["status", "payment_channel"]
    ordering         = ["-created_at"]

    def get_permissions(self):
        if self.action == "webhook":  return [permissions.AllowAny()]
        if self.action == "refund":   return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def get_throttles(self):
        if self.action == "initiate": return [PaymentInitiateThrottle()]
        if self.action == "webhook":  return [WebhookThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        qs = Payment.objects.select_related("order", "buyer")
        if self.request.user.is_staff or self.request.user.is_superuser:
            return qs
        buyer = getattr(self.request.user, "buyer_profile", None)
        return qs.filter(buyer=buyer) if buyer else Payment.objects.none()

    def create(self, request, *args, **kwargs):
        raise ValidationError({"detail": "Use /v1/payments/initiate/ to create a payment."})

    @action(detail=False, methods=["post"])
    def initiate(self, request):
        buyer = _require_buyer(request)
        ser   = PaymentInitiateSerializer(
            data=request.data, context={"buyer": buyer, "request": request},
        )
        ser.is_valid(raise_exception=True)
        try:
            payment = services.initiate_payment(
                buyer           = buyer,
                order           = ser.validated_data["order"],
                payment_channel = ser.validated_data["payment_channel"],
                amount          = ser.validated_data["amount"],
                **{k: ser.validated_data.get(k, "") for k in (
                    "mobile_money_number", "mobile_money_network",
                )},
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(
            {
                "payment_id":   str(payment.pk),
                "payment_code": payment.code,
                "amount":       str(payment.amount),
                "currency":     payment.currency,
                "status":       payment.status,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["post"])
    def webhook(self, request):
        """
        Receive provider webhook events.
        1. Validate HMAC signature — reject on failure (no silent fallback).
        2. Idempotency check — return 200 immediately if already processed.
        3. Store log record.
        4. Dispatch Celery task — view returns in < 50ms.
        """
        from apps.buyers.tasks import process_payment_webhook

        provider   = request.query_params.get("provider", "paystack")
        raw_body   = request.body
        event_type = request.data.get("event", "")
        event_id   = str(
            (request.data.get("data") or {}).get("id", "")
            or request.headers.get("X-Paystack-Event-Id", "")
            or request.headers.get("X-Flutterwave-Event-Id", "")
            or f"{provider}-{timezone.now().timestamp()}"
        )

        if PaymentWebhookLog.objects.filter(event_id=event_id).exists():
            return Response({"status": "already_processed"})

        try:
            sig_valid = services.validate_webhook_signature(
                raw_body = raw_body,
                headers  = dict(request.headers),
                provider = provider,
            )
        except ValueError as exc:
            logger.error("webhook_secret_not_configured | provider=%s | %s", provider, exc)
            return Response(
                {"status": "configuration_error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if not sig_valid:
            logger.warning(
                "webhook_invalid_signature | provider=%s | event_id=%s",
                provider, event_id,
            )
            return Response(
                {"status": "invalid_signature"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        log = PaymentWebhookLog.objects.create(
            provider        = provider,
            event_id        = event_id,
            event_type      = event_type,
            raw_payload     = request.data,
            signature_valid = True,
        )
        process_payment_webhook.delay(log_pk=str(log.pk))
        return Response({"status": "ok"})

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        payment = self.get_object()
        ser     = PaymentRefundSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            payment = services.refund_payment(
                payment     = payment,
                reason      = ser.validated_data["reason"],
                refunded_by = request.user,
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(PaymentSerializer(payment).data)

    @action(detail=True, methods=["get"])
    def receipt(self, request, pk=None):
        payment = self.get_object()
        if payment.receipt_url:
            from django.http import HttpResponseRedirect
            return HttpResponseRedirect(payment.receipt_url)
        return Response(
            {"detail": "No receipt available for this payment."},
            status=status.HTTP_404_NOT_FOUND,
        )


# =============================================================================
# COUPON VIEWSET
# =============================================================================

class CouponViewSet(viewsets.ModelViewSet):
    """
    Endpoint                          Method  Permission
    ─────────────────────────────────────────────────────
    /v1/coupons/                      GET     admin
    /v1/coupons/                      POST    admin
    /v1/coupons/<id>/                 PUT     admin
    /v1/coupons/validate/             POST    authenticated
    """

    queryset         = Coupon.objects.all()
    serializer_class = CouponSerializer
    filter_backends  = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["discount_type", "is_active"]
    search_fields    = ["code", "description"]
    ordering_fields  = ["created_at", "valid_until", "used_count"]
    ordering         = ["-created_at"]

    def get_permissions(self):
        if self.action == "validate": return [permissions.IsAuthenticated()]
        return [permissions.IsAdminUser()]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"): return CouponWriteSerializer
        return CouponSerializer

    @action(detail=False, methods=["post"])
    def validate(self, request):
        ser = CouponValidateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        coupon   = ser.validated_data["coupon"]
        discount = ser.validated_data["discount"]
        return Response({
            "code":            coupon.code,
            "discount_type":   coupon.discount_type,
            "discount_value":  str(coupon.discount_value),
            "discount_amount": str(discount),
            "description":     coupon.description,
            "valid_until":     coupon.valid_until,
        })


# =============================================================================
# BUYER NOTIFICATION VIEWSET
# =============================================================================

class BuyerNotificationViewSet(viewsets.ModelViewSet):
    """
    Endpoint                                Method  Permission
    ──────────────────────────────────────────────────────────
    /v1/notifications/                      GET     authenticated
    /v1/notifications/<id>/                 GET     authenticated
    /v1/notifications/<id>/read/            POST    authenticated
    /v1/notifications/mark-all-read/        POST    authenticated
    /v1/notifications/unread-count/         GET     authenticated
    """

    serializer_class   = BuyerNotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends    = [DjangoFilterBackend, OrderingFilter]
    filterset_fields   = ["notification_type", "is_read"]
    ordering           = ["-created_at"]

    def get_queryset(self):
        buyer = getattr(self.request.user, "buyer_profile", None)
        if not buyer:
            return BuyerNotification.objects.none()
        return BuyerNotification.objects.filter(buyer=buyer)

    def create(self, request, *args, **kwargs):
        raise ValidationError({"detail": "Notifications are system-generated."})

    def update(self, request, *args, **kwargs):
        raise ValidationError({"detail": "Use the /read/ action."})

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notif = self.get_object()
        if not notif.is_read:
            notif.is_read = True
            notif.read_at = timezone.now()
            notif.save(update_fields=["is_read", "read_at"])
        return Response(BuyerNotificationSerializer(notif).data)

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        buyer = getattr(request.user, "buyer_profile", None)
        if not buyer:
            raise PermissionDenied
        updated = BuyerNotification.objects.filter(
            buyer=buyer, is_read=False,
        ).update(is_read=True, read_at=timezone.now())
        return Response({"marked_read": updated})

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        buyer = getattr(request.user, "buyer_profile", None)
        if not buyer:
            return Response({"unread": 0})
        count = BuyerNotification.objects.filter(buyer=buyer, is_read=False).count()
        return Response({"unread": count})