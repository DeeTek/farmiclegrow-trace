"""
apps/buyers/serializers.py  —  FarmicleGrow-Trace Platform

Complete e-commerce serializer layer — buyers domain only.

Domain boundary:
  ProductReview and ReviewHelpful live in apps.farmers (not here) because
  they review a farmers.Product. Buyers write reviews but the reviewed
  entity is a farm product — farmers domain owns the model and serializers.
  See apps/farmers/serializers.py for ProductReviewSerializer and
  ProductReviewCreateSerializer.

Fixes vs original:
  • FarmerCredential removed — farmer-domain, lives in apps.farmers.models
  • ProductReview / ReviewHelpful removed — farmers domain (apps.farmers)
  • `from apps.orders.models import Order` removed — no apps.orders app
  • ProductReview.can_review() removed — no such class method
  • ser.save() used correctly instead of ser.create_review()
  • BuyerVerificationActionSerializer requires reason for both reject and suspend
  • OrderItemSerializer read_only_fields corrected for computed fields
  • order.code used (not order.order_number — Order uses CodedModel)
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from rest_framework import serializers

from apps.core.serializers import (
    BaseModelSerializer,
    BaseWriteSerializer,
    RoleBasedSerializer,
)
from .models import (
    Buyer, BuyerDocument, BuyerAddress,
    Wishlist, WishlistItem,
    Cart, CartItem,
    Order, OrderItem, OrderStatusHistory,
    Payment, PaymentWebhookLog,
    Coupon, CouponUsage,
    BuyerNotification,
)

# ProductReview and ReviewHelpful are NOT imported here.
# They live in apps.farmers — see apps/farmers/serializers.py.


# =============================================================================
# BUYER PROFILE SERIALIZERS
# =============================================================================

class BuyerAddressSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model  = BuyerAddress
        fields = [
            "id", "address_type", "is_default", "recipient_name", "company_name",
            "address_line1", "address_line2", "city", "state_province",
            "postal_code", "country", "phone", "created_ago",
        ]


class BuyerAddressWriteSerializer(BaseWriteSerializer):
    class Meta(BaseWriteSerializer.Meta):
        model   = BuyerAddress
        exclude = BaseWriteSerializer.Meta.exclude + ["buyer"]


class BuyerDocumentSerializer(BaseModelSerializer):
    is_expired   = serializers.SerializerMethodField()
    is_valid_doc = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = BuyerDocument
        fields = [
            "id", "document_type", "file", "status",
            "expiry_date", "rejection_reason",
            "is_expired", "is_valid_doc",
            "verified_at", "created_at", "created_ago",
        ]
        read_only_fields = ["id", "status", "rejection_reason", "verified_at"]

    def get_is_expired(self, obj) -> bool:
        return getattr(obj, "is_expired", False)

    def get_is_valid_doc(self, obj) -> bool:
        return getattr(obj, "is_valid", False)


class BuyerDocumentUploadSerializer(BaseWriteSerializer):
    class Meta(BaseWriteSerializer.Meta):
        model   = BuyerDocument
        exclude = BaseWriteSerializer.Meta.exclude + [
            "buyer", "status", "rejection_reason", "verified_by", "verified_at",
        ]


class BuyerListSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model  = Buyer
        fields = [
            "id", "buyer_code", "company_name", "buyer_type",
            "country", "verification_status", "created_ago",
        ]
        read_only_fields = fields


class BuyerSerializer(RoleBasedSerializer):
    addresses  = BuyerAddressSerializer(many=True, read_only=True)
    documents  = BuyerDocumentSerializer(many=True, read_only=True)
    user_email = serializers.SerializerMethodField()

    BUYER_FIELDS = [
        "id", "buyer_code", "company_name", "buyer_type", "industry",
        "contact_person", "phone", "email", "website",
        "country", "city", "verification_status", "addresses",
        "preferred_products", "preferred_certifications", "preferred_origins",
        "user_email", "created_at", "created_ago",
    ]
    ADMIN_FIELDS = "__all__"

    class Meta(RoleBasedSerializer.Meta):
        model  = Buyer
        fields = "__all__"

    def get_user_email(self, obj) -> str:
        return obj.user.email


class BuyerCreateSerializer(BaseWriteSerializer):
    class Meta(BaseWriteSerializer.Meta):
        model   = Buyer
        exclude = BaseWriteSerializer.Meta.exclude + [
            "code", "verification_status", "verified_at", "rejection_reason",
        ]


class BuyerUpdateSerializer(BaseWriteSerializer):
    class Meta(BaseWriteSerializer.Meta):
        model   = Buyer
        exclude = BaseWriteSerializer.Meta.exclude + [
            "code", "user", "verification_status", "verified_at", "rejection_reason",
        ]


class BuyerVerificationActionSerializer(serializers.Serializer):
    action           = serializers.ChoiceField(choices=["verify", "reject", "suspend"])
    rejection_reason = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        # Both reject and suspend require a reason
        if attrs["action"] in ("reject", "suspend") and not attrs.get("rejection_reason", "").strip():
            raise serializers.ValidationError(
                {"rejection_reason": "A reason is required for reject and suspend actions."}
            )
        return attrs


# =============================================================================
# WISHLIST SERIALIZERS
# =============================================================================

class WishlistItemSerializer(BaseModelSerializer):
    product_name     = serializers.SerializerMethodField()
    product_category = serializers.SerializerMethodField()
    product_price    = serializers.SerializerMethodField()
    in_stock         = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = WishlistItem
        fields = [
            "id", "product", "product_name", "product_category",
            "product_price", "in_stock", "desired_qty_kg", "target_price",
            "notes", "notify_on_restock", "created_at",
        ]
        read_only_fields = ["id", "product_name", "product_category", "product_price", "in_stock"]

    def get_product_name(self, obj) -> str:
        return getattr(obj.product, "name", "")

    def get_product_category(self, obj) -> str:
        return getattr(obj.product, "category", "")

    def get_product_price(self, obj):
        return getattr(obj.product, "price_per_kg", None)

    def get_in_stock(self, obj) -> bool:
        return bool(obj.product.is_available and obj.product.stock_kg > 0)


class WishlistItemWriteSerializer(BaseWriteSerializer):
    class Meta(BaseWriteSerializer.Meta):
        model   = WishlistItem
        exclude = BaseWriteSerializer.Meta.exclude + ["wishlist"]


class WishlistSerializer(BaseModelSerializer):
    items      = WishlistItemSerializer(many=True, read_only=True)
    item_count = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = Wishlist
        fields = [
            "id", "name", "is_default", "is_public", "description",
            "items", "item_count", "created_at", "created_ago",
        ]

    def get_item_count(self, obj) -> int:
        return obj.items.filter(is_active=True).count()


class WishlistWriteSerializer(BaseWriteSerializer):
    class Meta(BaseWriteSerializer.Meta):
        model   = Wishlist
        exclude = BaseWriteSerializer.Meta.exclude + ["buyer"]


# =============================================================================
# CART SERIALIZERS
# =============================================================================

class CartItemSerializer(BaseModelSerializer):
    subtotal        = serializers.SerializerMethodField()
    product_name    = serializers.SerializerMethodField()
    product_code    = serializers.SerializerMethodField()
    stock_available = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = CartItem
        fields = [
            "id", "product", "product_name", "product_code",
            "quantity_kg", "unit_price", "currency", "subtotal",
            "stock_available", "notes", "created_at",
        ]
        read_only_fields = [
            "id", "unit_price", "subtotal",
            "product_name", "product_code", "stock_available",
        ]

    def get_subtotal(self, obj):
        return obj.subtotal

    def get_product_name(self, obj) -> str:
        return getattr(obj.product, "name", "")

    def get_product_code(self, obj) -> str:
        return getattr(obj.product, "code", "")

    def get_stock_available(self, obj):
        return obj.product.stock_kg


class CartItemAddSerializer(serializers.Serializer):
    """Validates adding or updating a product in the cart."""
    product_id  = serializers.UUIDField()
    quantity_kg = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.001")
    )
    notes = serializers.CharField(max_length=300, required=False, allow_blank=True)

    def validate(self, attrs):
        from apps.farmers.models import Product
        try:
            product = Product.objects.get(pk=attrs["product_id"], is_active=True)
        except Product.DoesNotExist:
            raise serializers.ValidationError(
                {"product_id": "Product not found or unavailable."}
            )
        if not product.is_available:
            raise serializers.ValidationError(
                {"product_id": f"'{product.name}' is not currently available."}
            )
        if product.stock_kg < attrs["quantity_kg"]:
            raise serializers.ValidationError(
                {"quantity_kg": f"Only {product.stock_kg} kg in stock for '{product.name}'."}
            )
        if attrs["quantity_kg"] < product.min_order_kg:
            raise serializers.ValidationError(
                {"quantity_kg": f"Minimum order is {product.min_order_kg} kg for '{product.name}'."}
            )
        attrs["product"] = product
        return attrs


class CartSerializer(BaseModelSerializer):
    items           = CartItemSerializer(many=True, read_only=True)
    subtotal        = serializers.SerializerMethodField()
    total           = serializers.SerializerMethodField()
    discount_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    item_count  = serializers.SerializerMethodField()
    coupon_code = serializers.SerializerMethodField()
    is_expired  = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = Cart
        fields = [
            "id", "status", "currency", "subtotal", "discount_amount", "total",
            "item_count", "coupon_code", "is_expired", "expires_at", "notes",
            "items", "created_at",
        ]
        read_only_fields = ["id", "status"]

    def get_subtotal(self, obj):
        return obj.subtotal

    def get_total(self, obj):
        return obj.total

    def get_item_count(self, obj) -> int:
        return obj.item_count

    def get_coupon_code(self, obj):
        return obj.coupon.code if obj.coupon else None

    def get_is_expired(self, obj) -> bool:
        return obj.is_expired


class CouponApplySerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50)

    def validate_code(self, value):
        try:
            coupon = Coupon.objects.get(code__iexact=value.strip())
        except Coupon.DoesNotExist:
            raise serializers.ValidationError("Coupon code not found.")
        if not coupon.is_currently_valid:
            raise serializers.ValidationError("This coupon is expired or no longer valid.")
        self.coupon_instance = coupon
        return value


# =============================================================================
# ORDER SERIALIZERS
# =============================================================================

class OrderStatusHistorySerializer(serializers.ModelSerializer):
    changed_by_name = serializers.SerializerMethodField()

    class Meta:
        model  = OrderStatusHistory
        fields = ["id", "old_status", "new_status", "changed_by_name", "changed_at", "note"]

    def get_changed_by_name(self, obj) -> str:
        return obj.changed_by.get_full_name() if obj.changed_by else "System"


class OrderItemSerializer(BaseModelSerializer):
    trace_code = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = OrderItem
        fields = [
            "id", "product", "product_name", "product_code",
            "quantity_kg", "unit_price", "currency", "subtotal",
            "trace_code", "notes",
        ]
        # subtotal, product_name, product_code, trace_code are computed — not model fields
        read_only_fields = ["id", "subtotal", "product_name", "product_code", "trace_code"]

    def get_trace_code(self, obj) -> str:
        return getattr(obj.trace_record, "trace_code", "") if obj.trace_record else ""


class OrderListSerializer(BaseModelSerializer):
    buyer_name = serializers.SerializerMethodField()
    item_count = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = Order
        fields = [
            "id", "code", "buyer_name", "status", "payment_status",
            "total_amount", "currency", "item_count", "created_at", "created_ago",
        ]
        read_only_fields = fields

    def get_buyer_name(self, obj) -> str:
        return obj.buyer.display_name

    def get_item_count(self, obj) -> int:
        return obj.items.filter(is_active=True).count()


class OrderSerializer(BaseModelSerializer):
    items           = OrderItemSerializer(many=True, read_only=True)
    status_history  = OrderStatusHistorySerializer(many=True, read_only=True)
    balance_due     = serializers.SerializerMethodField()
    buyer_name      = serializers.SerializerMethodField()
    is_cancellable  = serializers.SerializerMethodField()
    shipping_address_detail = BuyerAddressSerializer(
        source="shipping_address", read_only=True
    )
    billing_address_detail = BuyerAddressSerializer(
        source="billing_address", read_only=True
    )

    class Meta(BaseModelSerializer.Meta):
        model  = Order
        fields = [
            "id", "code", "buyer", "buyer_name",
            "status", "payment_status", "is_cancellable",
            "currency", "subtotal", "discount_amount", "shipping_amount",
            "tax_amount", "total_amount", "amount_paid", "balance_due",
            "coupon_code_used",
            "shipping_address_detail", "billing_address_detail",
            "expected_delivery_date", "actual_delivery_date",
            "tracking_number", "carrier_name", "destination_country",
            "buyer_notes", "cancelled_reason",
            "confirmed_at", "dispatched_at", "delivered_at", "cancelled_at",
            "items", "status_history",
            "created_at", "created_ago",
        ]
        read_only_fields = [
            "id", "code", "buyer", "status", "payment_status",
            "subtotal", "total_amount", "balance_due", "amount_paid",
            "confirmed_at", "dispatched_at", "delivered_at", "cancelled_at",
        ]

    def get_balance_due(self, obj):
        return obj.balance_due

    def get_buyer_name(self, obj) -> str:
        return obj.buyer.display_name

    def get_is_cancellable(self, obj) -> bool:
        return obj.is_cancellable


class OrderCreateSerializer(serializers.Serializer):
    """
    Checkout serializer — converts an active cart into an Order.
    Delegates all DB logic to services.create_order_from_cart().
    No direct model writes here.
    """
    shipping_address_id = serializers.UUIDField()
    billing_address_id  = serializers.UUIDField(required=False, allow_null=True)
    buyer_notes         = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    destination_country = serializers.CharField(max_length=100, required=False, allow_blank=True)

    def validate_shipping_address_id(self, value):
        buyer = self.context["buyer"]
        try:
            return BuyerAddress.objects.get(pk=value, buyer=buyer, is_active=True)
        except BuyerAddress.DoesNotExist:
            raise serializers.ValidationError(
                "Shipping address not found for this buyer."
            )

    def validate_billing_address_id(self, value):
        if not value:
            return None
        buyer = self.context["buyer"]
        try:
            return BuyerAddress.objects.get(pk=value, buyer=buyer, is_active=True)
        except BuyerAddress.DoesNotExist:
            raise serializers.ValidationError(
                "Billing address not found for this buyer."
            )

    def validate(self, attrs):
        cart = self.context["cart"]
        if cart.is_expired:
            raise serializers.ValidationError(
                "Your cart has expired. Please start a new cart."
            )
        if not cart.items.filter(is_active=True).exists():
            raise serializers.ValidationError("Your cart is empty.")
        for item in cart.items.select_related("product").filter(is_active=True):
            try:
                item.validate_stock()
            except ValueError as e:
                raise serializers.ValidationError(str(e))
        if cart.coupon:
            err = cart.coupon.validate_for_cart(cart, self.context["buyer"])
            if err:
                raise serializers.ValidationError(f"Coupon error: {err}")
        return attrs


class OrderDispatchSerializer(serializers.Serializer):
    tracking_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    carrier_name    = serializers.CharField(max_length=100, required=False, allow_blank=True)
    dispatch_notes  = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class OrderCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000, required=False, allow_blank=True)


class OrderStatusUpdateSerializer(serializers.Serializer):
    """Admin-level manual status transition."""
    new_status      = serializers.ChoiceField(choices=Order.OrderStatus.choices)
    note            = serializers.CharField(max_length=500, required=False, allow_blank=True)
    tracking_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    carrier_name    = serializers.CharField(max_length=100, required=False, allow_blank=True)


# =============================================================================
# PAYMENT SERIALIZERS
# =============================================================================

class PaymentSerializer(BaseModelSerializer):
    order_code = serializers.SerializerMethodField()
    buyer_name = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = Payment
        fields = [
            "id", "code", "order", "order_code", "buyer_name",
            "payment_channel", "amount", "currency", "status",
            "mobile_money_number", "mobile_money_network",
            "card_last_four", "card_brand",
            "provider_reference", "payment_date",
            "receipt_url", "failure_reason",
            "refund_reason", "refunded_at",
            "created_at", "created_ago",
        ]
        read_only_fields = [
            "id", "code", "status", "provider_reference",
            "payment_date", "receipt_url", "failure_reason",
        ]

    def get_order_code(self, obj) -> str:
        return obj.order.code if obj.order else ""

    def get_buyer_name(self, obj) -> str:
        return obj.buyer.display_name if obj.buyer else ""


class PaymentInitiateSerializer(serializers.Serializer):
    """Buyer initiates a payment for a confirmed order."""
    order_id             = serializers.UUIDField()
    payment_channel      = serializers.ChoiceField(choices=Payment.PaymentChannel.choices)
    mobile_money_number  = serializers.CharField(max_length=20, required=False, allow_blank=True)
    mobile_money_network = serializers.ChoiceField(
        choices=[("mtn", "MTN"), ("vodafone", "Vodafone"), ("airteltigo", "AirtelTigo")],
        required=False, allow_blank=True,
    )
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, required=False)

    def validate(self, attrs):
        buyer = self.context["buyer"]
        try:
            order = Order.objects.get(pk=attrs["order_id"], buyer=buyer)
        except Order.DoesNotExist:
            raise serializers.ValidationError(
                {"order_id": "Order not found for this buyer."}
            )
        if order.status == Order.OrderStatus.CANCELLED:
            raise serializers.ValidationError(
                {"order_id": "Cannot pay for a cancelled order."}
            )
        if order.payment_status == Order.PaymentStatus.PAID:
            raise serializers.ValidationError(
                {"order_id": "Order is already fully paid."}
            )
        if (attrs.get("payment_channel") == "mobile_money"
                and not attrs.get("mobile_money_number")):
            raise serializers.ValidationError(
                {"mobile_money_number": "Required for mobile money payments."}
            )
        attrs["order"] = order
        attrs.setdefault("amount", order.balance_due)
        return attrs


class PaymentRefundSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


# =============================================================================
# COUPON SERIALIZERS
# =============================================================================

class CouponSerializer(BaseModelSerializer):
    is_currently_valid = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = Coupon
        fields = [
            "id", "code", "description", "discount_type", "discount_value",
            "min_order_value", "max_discount_amount",
            "max_uses", "max_uses_per_buyer", "used_count",
            "valid_from", "valid_until", "is_active", "is_currently_valid",
            "applicable_categories", "created_at",
        ]
        read_only_fields = ["id", "used_count", "is_currently_valid"]

    def get_is_currently_valid(self, obj) -> bool:
        return obj.is_currently_valid


class CouponValidateSerializer(serializers.Serializer):
    """Buyer validates a coupon before applying it to the cart."""
    code       = serializers.CharField(max_length=50)
    cart_total = serializers.DecimalField(max_digits=14, decimal_places=2)

    def validate(self, attrs):
        try:
            coupon = Coupon.objects.get(code__iexact=attrs["code"].strip())
        except Coupon.DoesNotExist:
            raise serializers.ValidationError({"code": "Coupon not found."})
        if not coupon.is_currently_valid:
            raise serializers.ValidationError({"code": "Coupon is expired or invalid."})
        attrs["coupon"]   = coupon
        attrs["discount"] = coupon.compute_discount(attrs["cart_total"])
        return attrs


class CouponWriteSerializer(BaseWriteSerializer):
    class Meta(BaseWriteSerializer.Meta):
        model   = Coupon
        exclude = BaseWriteSerializer.Meta.exclude + ["used_count"]


# =============================================================================
# NOTIFICATION SERIALIZERS
# =============================================================================

class BuyerNotificationSerializer(BaseModelSerializer):
    class Meta(BaseModelSerializer.Meta):
        model  = BuyerNotification
        fields = [
            "id", "notification_type", "title", "message",
            "is_read", "read_at",
            "related_object_type", "related_object_id",
            "created_at", "created_ago",
        ]
        read_only_fields = [
            "id", "notification_type", "title", "message",
            "related_object_type", "related_object_id", "read_at",
        ]