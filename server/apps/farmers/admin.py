"""
apps/farmers/admin.py  —  FarmicleGrow-Trace Platform

Registers all farmers-domain models with the Django admin.

Includes ProductReview and ReviewHelpful — they were previously in
buyers/admin.py but belong here because they review farmers.Product.
"""
from django.contrib import admin
from django.utils.html import format_html

from apps.core.admin import (
    BaseModelAdmin,
    CodedModelAdmin,
    VerifiableModelAdmin,
)
from .models import (
    Farmer, FarmerCredential,
    Farm, FarmVisit, CropSeason,
    Product,
    ProductReview, ReviewHelpful,
)


# =============================================================================
# INLINES
# =============================================================================

class FarmerCredentialInline(admin.StackedInline):
    model           = FarmerCredential
    extra           = 0
    readonly_fields = ["login_identifier", "registered_by", "created_at"]
    fields          = ["login_identifier", "registered_by", "must_change_password", "created_at"]
    can_delete      = False
    verbose_name    = "Field Login Credential"


class FarmInline(admin.TabularInline):
    model            = Farm
    extra            = 0
    fields           = ["code", "area_hectares", "region", "district", "current_crop_type", "is_active"]
    readonly_fields  = ["code"]
    show_change_link = True


class CropSeasonInline(admin.TabularInline):
    model           = CropSeason
    extra           = 0
    fields          = [
        "code", "harvest_year", "crop_variety",
        "expected_yield_kg", "actual_yield_kg", "is_active",
    ]
    readonly_fields = ["code"]
    show_change_link = True


class FarmVisitInline(admin.TabularInline):
    model           = FarmVisit
    extra           = 0
    fields          = ["visited_at", "field_officer", "purpose", "produce_collected_kg"]
    readonly_fields = ["visited_at"]
    ordering        = ["-visited_at"]


class ReviewHelpfulInline(admin.TabularInline):
    model           = ReviewHelpful
    extra           = 0
    fields          = ["buyer", "created_at"]
    readonly_fields = ["buyer", "created_at"]
    can_delete      = False


# =============================================================================
# FARMER
# =============================================================================

@admin.register(Farmer)
class FarmerAdmin(VerifiableModelAdmin, CodedModelAdmin):
    list_display   = [
        "code", "full_name", "gender", "region", "district",
        "verification_status", "is_active", "created_at",
    ]
    list_filter    = [
        "region", "district", "gender",
        "verification_status", "education_level", "land_ownership", "is_active",
    ]
    search_fields  = [
        "code", "first_name", "last_name",
        "phone_number", "ghana_card_number", "cooperative_name",
    ]
    readonly_fields = [
        "id", "code", "ghana_card_number",
        "verified_at", "created_at", "updated_at",
    ]
    inlines        = [FarmerCredentialInline, FarmInline]
    ordering       = ["-created_at"]
    fieldsets      = (
        ("Identity", {
            "fields": (
                "code", "user", "registered_by",
                "first_name", "last_name", "gender", "date_of_birth",
                "ghana_card_number", "national_id",
            ),
        }),
        ("Location", {
            "fields": (
                "community", "district", "region",
                "gps_latitude", "gps_longitude", "landmark",
            ),
        }),
        ("Socioeconomic", {
            "fields": (
                "education_level", "cooperative_name", "land_ownership",
            ),
        }),
        ("Verification", {
            "fields": (
                "verification_status", "verified_at", "rejection_reason",
            ),
        }),
        ("Status", {
            "fields": ("is_active", "created_at", "updated_at"),
        }),
    )


# =============================================================================
# FARM
# =============================================================================

@admin.register(Farm)
class FarmAdmin(CodedModelAdmin):
    list_display   = [
        "code", "farmer", "area_hectares", "area_category",
        "region", "district", "current_crop_type", "is_active",
    ]
    list_filter    = [
        "region", "district", "cropping_system",
        "land_ownership", "is_active",
    ]
    search_fields  = [
        "code", "farmer__code", "farmer__first_name", "farmer__last_name",
        "community",
    ]
    readonly_fields = ["id", "code", "created_at", "updated_at"]
    inlines        = [CropSeasonInline, FarmVisitInline]
    ordering       = ["-created_at"]

    def area_category(self, obj):
        return obj.area_category
    area_category.short_description = "Size"


# =============================================================================
# PRODUCT
# =============================================================================

@admin.register(Product)
class ProductAdmin(CodedModelAdmin):
    list_display   = [
        "code", "name", "category", "price_per_kg",
        "stock_kg", "stock_status", "is_available", "is_active",
    ]
    list_filter    = [
        "category", "is_available", "is_active",
        "origin_country", "origin_region",
    ]
    search_fields  = ["code", "name", "scientific_name", "category", "hs_code"]
    readonly_fields = ["id", "code", "created_at", "updated_at"]
    ordering       = ["-created_at"]

    def stock_status(self, obj):
        colour = {"in_stock": "green", "low_stock": "orange", "out_of_stock": "red"}
        label  = obj.stock_status.replace("_", " ").title()
        return format_html(
            '<span style="color:{}">{}</span>',
            colour.get(obj.stock_status, "black"),
            label,
        )
    stock_status.short_description = "Stock"


# =============================================================================
# PRODUCT REVIEW  (moved from buyers/admin.py — reviews a farmers.Product)
# =============================================================================

@admin.register(ProductReview)
class ProductReviewAdmin(BaseModelAdmin):
    list_display   = [
        "product", "buyer", "rating",
        "is_verified_purchase", "helpful_count", "is_published", "created_at",
    ]
    list_filter    = ["rating", "is_verified_purchase", "is_published"]
    search_fields  = [
        "buyer__company_name", "product__name",
        "title", "body",
    ]
    readonly_fields = [
        "id", "is_verified_purchase", "helpful_count", "created_at",
    ]
    inlines        = [ReviewHelpfulInline]
    ordering       = ["-created_at"]

    @admin.action(description="Unpublish selected reviews")
    def unpublish_reviews(self, request, queryset):
        updated = queryset.update(is_published=False)
        self.message_user(request, f"{updated} review(s) unpublished.")

    @admin.action(description="Publish selected reviews")
    def publish_reviews(self, request, queryset):
        updated = queryset.update(is_published=True)
        self.message_user(request, f"{updated} review(s) published.")