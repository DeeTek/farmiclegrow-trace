"""
apps/buyers/admin.py  —  FarmicleGrow-Trace Platform

Registers all buyers-domain models with the Django admin.

Domain boundary:
  ProductReview and ReviewHelpful are NOT registered here.
  They live in apps.farmers.admin — see that file for their admin classes.
"""
from django.contrib import admin
from django.utils import timezone

from apps.core.admin import (
    BaseModelAdmin,
    CodedModelAdmin,
    ImmutableLogAdmin,
    StatusModelAdmin,
    VerifiableModelAdmin,
)
from .models import (
    Buyer, BuyerDocument, BuyerAddress,
    Cart, CartItem,
    Order, OrderItem, OrderStatusHistory,
    Payment, PaymentWebhookLog,
    Coupon, CouponUsage,
    Wishlist, WishlistItem,
    BuyerNotification,
)


# =============================================================================
# INLINES
# =============================================================================

class BuyerDocumentInline(admin.TabularInline):
    model            = BuyerDocument
    extra            = 0
    readonly_fields  = ["document_type", "status", "verified_at"]
    fields           = ["document_type", "file", "status", "verified_at"]
    show_change_link = True
    can_delete       = False


class BuyerAddressInline(admin.TabularInline):
    model  = BuyerAddress
    extra  = 0
    fields = ["address_type", "is_default", "recipient_name", "city", "country", "is_active"]


class CartItemInline(admin.TabularInline):
    model           = CartItem
    extra           = 0
    fields          = ["product", "quantity_kg", "unit_price", "currency", "is_active"]
    readonly_fields = ["unit_price"]


class OrderItemInline(admin.TabularInline):
    model           = OrderItem
    extra           = 0
    fields          = ["product", "quantity_kg", "unit_price", "currency"]
    readonly_fields = ["unit_price"]
    can_delete      = False


class OrderStatusHistoryInline(admin.TabularInline):
    model           = OrderStatusHistory
    extra           = 0
    fields          = ["old_status", "new_status", "changed_by", "changed_at", "note"]
    readonly_fields = ["old_status", "new_status", "changed_by", "changed_at", "note"]
    can_delete      = False


class CouponUsageInline(admin.TabularInline):
    model           = CouponUsage
    extra           = 0
    fields          = ["buyer", "order", "amount_saved", "used_at"]
    readonly_fields = ["buyer", "order", "amount_saved", "used_at"]
    can_delete      = False


class WishlistItemInline(admin.TabularInline):
    model  = WishlistItem
    extra  = 0
    fields = ["product", "desired_qty_kg", "target_price", "notify_on_restock"]


# =============================================================================
# BUYER
# =============================================================================

@admin.register(Buyer)
class BuyerAdmin(VerifiableModelAdmin, CodedModelAdmin):
    list_display   = [
        "code", "company_name", "buyer_type", "country",
        "verification_status", "is_active", "created_at",
    ]
    list_filter    = ["buyer_type", "country", "verification_status", "is_active"]
    search_fields  = ["code", "company_name", "contact_person", "email", "user__email"]
    readonly_fields = ["id", "code", "created_at", "updated_at"]
    inlines        = [BuyerDocumentInline, BuyerAddressInline]
    ordering       = ["-created_at"]


# =============================================================================
# CART
# =============================================================================

@admin.register(Cart)
class CartAdmin(BaseModelAdmin):
    list_display   = ["id", "buyer", "status", "currency", "discount_amount", "created_at"]
    list_filter    = ["status", "currency"]
    search_fields  = ["buyer__company_name", "buyer__user__email"]
    readonly_fields = ["id", "created_at"]
    inlines        = [CartItemInline]
    ordering       = ["-created_at"]


# =============================================================================
# ORDER
# =============================================================================

@admin.register(Order)
class OrderAdmin(StatusModelAdmin, CodedModelAdmin):
    list_display   = [
        "code", "buyer", "status", "payment_status",
        "total_amount", "currency", "created_at",
    ]
    list_filter    = ["status", "payment_status", "destination_country", "currency"]
    search_fields  = ["code", "buyer__company_name", "tracking_number"]
    readonly_fields = [
        "id", "code",
        "subtotal", "total_amount", "amount_paid",
        "confirmed_at", "dispatched_at", "delivered_at", "cancelled_at",
        "created_at", "updated_at",
    ]
    inlines        = [OrderItemInline, OrderStatusHistoryInline]
    ordering       = ["-created_at"]

    @admin.action(description="Mark selected orders as confirmed")
    def confirm_orders(self, request, queryset):
        from apps.buyers import services
        for order in queryset.filter(status="pending"):
            try:
                services.confirm_order(order, confirmed_by=request.user)
            except Exception as exc:
                self.message_user(request, f"{order.code}: {exc}", level="error")


# =============================================================================
# PAYMENT
# =============================================================================

@admin.register(Payment)
class PaymentAdmin(StatusModelAdmin, CodedModelAdmin):
    list_display   = [
        "code", "order", "buyer", "payment_channel",
        "amount", "currency", "status", "payment_date",
    ]
    list_filter    = ["status", "payment_channel"]
    search_fields  = ["code", "order__code", "provider_reference"]
    readonly_fields = [
        "id", "code",
        "provider_reference", "payment_date",
        "created_at", "updated_at",
    ]
    ordering       = ["-created_at"]


@admin.register(PaymentWebhookLog)
class PaymentWebhookLogAdmin(ImmutableLogAdmin):
    list_display   = [
        "provider", "event_type", "event_id",
        "signature_valid", "processed",
    ]
    list_filter    = ["provider", "processed", "event_type"]
    search_fields  = ["event_id", "event_type"]
    readonly_fields = [
        f.name for f in PaymentWebhookLog._meta.get_fields()
        if hasattr(f, "name")
    ]


# =============================================================================
# COUPON
# =============================================================================

@admin.register(Coupon)
class CouponAdmin(BaseModelAdmin):
    list_display   = [
        "code", "discount_type", "discount_value",
        "used_count", "max_uses", "valid_from", "valid_until", "is_active",
    ]
    list_filter    = ["discount_type", "is_active"]
    search_fields  = ["code", "description"]
    readonly_fields = ["id", "used_count", "created_at"]
    inlines        = [CouponUsageInline]


# =============================================================================
# WISHLIST
# =============================================================================

@admin.register(Wishlist)
class WishlistAdmin(BaseModelAdmin):
    list_display   = ["id", "buyer", "name", "is_default", "is_public", "created_at"]
    list_filter    = ["is_default", "is_public"]
    search_fields  = ["buyer__company_name", "name"]
    readonly_fields = ["id", "created_at"]
    inlines        = [WishlistItemInline]


# =============================================================================
# BUYER NOTIFICATION
# =============================================================================

@admin.register(BuyerNotification)
class BuyerNotificationAdmin(BaseModelAdmin):
    list_display   = [
        "buyer", "notification_type", "title",
        "is_read", "created_at",
    ]
    list_filter    = ["notification_type", "is_read"]
    search_fields  = ["buyer__company_name", "title", "message"]
    readonly_fields = ["id", "created_at", "read_at"]
    ordering       = ["-created_at"]