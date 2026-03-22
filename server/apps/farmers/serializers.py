"""
apps/farmers/serializers.py  —  FarmicleGrow-Trace Platform

Scope: all farmer domain serialization.

Sections:
  1. Read serializers    — list + detail shapes for API responses
  2. Write serializers   — create / update input validation
  3. Auth / onboarding   — farmer account creation, credential reset, impersonation
                           (DB writes delegated to apps/farmers/services.py)

Import fix vs previous version:
  Removed: from apps.farmers.models import FarmerCredential  (top-level — caused ImportError)
  Fixed:   ghana_card_number uniqueness is now validated against Farmer.ghana_card_number
           (the field lives on Farmer, not on FarmerCredential directly).
           FarmerCredential is still created by onboard_farmer() in services.py — it
           just doesn't need to be imported at serializer module load time.
"""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db import transaction

from rest_framework import serializers

from apps.core.serializers import (
    BaseModelSerializer,
    BaseWriteSerializer,
    RoleBasedSerializer,
    PhoneField,
    GhanaCardField,
)
from .models import Farmer, Farm, Product, CropSeason, FarmVisit, ProductReview, ReviewHelpful

User   = get_user_model()
logger = logging.getLogger(__name__)


# =============================================================================
# 1. READ SERIALIZERS
# =============================================================================

class FarmerListSerializer(BaseModelSerializer):
    """Lightweight shape for list views, search results, and QR output."""

    class Meta(BaseModelSerializer.Meta):
        model  = Farmer
        fields = [
            "id", "farmer_code", "first_name", "last_name", "gender",
            "phone_number", "community", "district", "region",
            "verification_status", "created_ago",
        ]
        read_only_fields = fields


class CropSeasonSerializer(BaseModelSerializer):
    yield_variance_pct = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = CropSeason
        fields = [
            "id", "code", "harvest_year", "crop_variety", "planting_method",
            "planting_date", "seed_source", "seed_quantity_kg",
            "fertilizer_type", "fertilizer_brand", "fertilizer_quantity_kg",
            "fertilizer_applied_at", "expected_harvest_date", "actual_harvest_date",
            "expected_yield_kg", "actual_yield_kg", "labour_type", "labour_count",
            "yield_variance_pct", "notes", "created_at", "created_ago",
        ]

    def get_yield_variance_pct(self, obj):
        return obj.yield_variance_pct


class FarmVisitSerializer(BaseModelSerializer):
    officer_name = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = FarmVisit
        fields = [
            "id", "visited_at", "purpose", "observations",
            "produce_collected_kg", "latitude", "longitude",
            "gps_accuracy_meters", "officer_name", "photos", "created_ago",
        ]
        read_only_fields = ["id", "officer_name"]

    def get_officer_name(self, obj):
        return obj.field_officer.get_full_name() if obj.field_officer else ""


class FarmSerializer(BaseModelSerializer):
    crop_seasons   = CropSeasonSerializer(many=True, read_only=True)
    visit_count    = serializers.SerializerMethodField()
    total_yield_kg = serializers.SerializerMethodField()
    area_category  = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = Farm
        fields = [
            "id", "farm_code", "name", "area_hectares", "area_category",
            "land_ownership", "soil_type", "community", "district", "region",
            "landmark", "current_crop_type", "previous_crop_type", "cropping_system",
            "latitude", "longitude", "altitude_meters", "polygon_coordinates",
            "gps_accuracy_meters", "has_coordinates",
            "visit_count", "total_yield_kg", "crop_seasons",
            "created_at", "created_ago",
        ]

    def get_visit_count(self, obj):
        return obj.visits.filter(is_active=True).count()

    def get_total_yield_kg(self, obj):
        return sum(
            float(s.actual_yield_kg or 0)
            for s in obj.crop_seasons.filter(is_active=True)
        )

    def get_area_category(self, obj):
        return obj.area_category

class FarmListSerializer(serializers.ModelSerializer):
    """
    Lightweight Farm serializer for list views and search results.
    """
    farmer_code   = serializers.CharField(source="farmer.code",      read_only=True)
    farmer_name   = serializers.CharField(source="farmer.full_name",  read_only=True)
    area_category = serializers.ReadOnlyField()
    has_coordinates = serializers.ReadOnlyField()

    class Meta:
        model  = Farm
        fields = [
            "id",
            "code",
            "name",
            "farmer_code",
            "farmer_name",
            "community",
            "district",
            "region",
            "landmark",
            "area_hectares",
            "area_category",
            "land_ownership",
            "soil_type",
            "current_crop_type",
            "cropping_system",
            "has_coordinates",
            "is_active",
            "created_at",
        ]


class FarmVisitListSerializer(serializers.ModelSerializer):
    """
    Lightweight FarmVisit serializer for list views and search results.
    """
    farm_code           = serializers.CharField(source="farm.code",                        read_only=True)
    field_officer_name  = serializers.SerializerMethodField()

    class Meta:
        model  = FarmVisit
        fields = [
            "id",
            "farm_code",
            "field_officer_name",
            "visited_at",
            "purpose",
            "produce_collected_kg",
            "observations",
            "created_at",
        ]

    def get_field_officer_name(self, obj) -> str:
        officer = obj.field_officer
        if not officer:
            return ""
        return getattr(officer, "get_full_name", lambda: str(officer))()


class FarmerSerializer(RoleBasedSerializer):
    """
    Role-scoped farmer detail serializer.

    BUYER   → anonymised public fields only (no PII)
    OFFICER → full agronomic + contact fields
    ADMIN   → all fields
    """

    farms         = FarmListSerializer(many=True, read_only=True)
    farm_count    = serializers.SerializerMethodField()
    total_area_ha = serializers.SerializerMethodField()
    user_email    = serializers.SerializerMethodField()

    BUYER_FIELDS = [
        "id", "farmer_code", "community", "district", "region",
        "verification_status",
    ]
    OFFICER_FIELDS = [
        "id", "farmer_code", "first_name", "last_name", "gender",
        "phone_number", "national_id", "community", "district", "region",
        "gps_latitude", "gps_longitude", "education_level", "land_ownership",
        "cooperative_name", "verification_status", "farms", "farm_count",
        "created_ago",
    ]
    ADMIN_FIELDS = "__all__"

    class Meta(RoleBasedSerializer.Meta):
        model  = Farmer
        fields = "__all__"

    def get_farm_count(self, obj):
        return obj.farms.filter(is_active=True).count()

    def get_total_area_ha(self, obj):
        return float(
            sum(f.area_hectares for f in obj.farms.filter(is_active=True))
        )

    def get_user_email(self, obj):
        return obj.user.email


class ProductSerializer(BaseModelSerializer):
    stock_status       = serializers.SerializerMethodField()
    origin_farmer_name = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = Product
        fields = [
            "id", "code", "name", "scientific_name", "category", "hs_code",
            "description", "photo", "origin_country", "origin_region",
            "origin_farmer_name", "price_per_kg", "currency", "stock_kg",
            "min_order_kg", "is_available", "stock_status",
            "moisture_pct", "impurity_pct", "grade",
            "created_at", "created_ago",
        ]

    def get_stock_status(self, obj):
        return obj.stock_status

    def get_origin_farmer_name(self, obj):
        return obj.origin_farmer.full_name if obj.origin_farmer else ""

class ProductListSerializer(serializers.ModelSerializer):
    """
    Lightweight Product serializer for list views and search results.
    Excludes heavy fields (description, photo) to keep list payloads small.
    """
    stock_status   = serializers.ReadOnlyField()
    farmer_code    = serializers.CharField(source="origin_farmer.code",      read_only=True)
    farm_code      = serializers.CharField(source="origin_farm.code",        read_only=True)

    class Meta:
        model  = Product
        fields = [
            "id",
            "code",
            "name",
            "scientific_name",
            "category",
            "hs_code",
            "origin_country",
            "origin_region",
            "farmer_code",
            "farm_code",
            "price_per_kg",
            "currency",
            "stock_kg",
            "min_order_kg",
            "is_available",
            "stock_status",
            "grade",
            "moisture_pct",
            "impurity_pct",
            "created_at",
        ]

class ProfileScoreSerializer(serializers.Serializer):
    farmer_id      = serializers.UUIDField()
    farmer_code    = serializers.CharField()
    profile_score  = serializers.IntegerField()
    missing_fields = serializers.ListField(child=serializers.CharField())


# =============================================================================
# 2. WRITE SERIALIZERS
# =============================================================================

class FarmerCreateSerializer(BaseWriteSerializer):
    phone_number = PhoneField()
    national_id  = GhanaCardField(required=False, allow_blank=True)

    class Meta(BaseWriteSerializer.Meta):
        model   = Farmer
        exclude = BaseWriteSerializer.Meta.exclude + [
            "code", "verification_status", "verified_at", "rejection_reason",
        ]

    def validate_phone_number(self, value):
        from apps.core.utils import normalise_phone
        return normalise_phone(value)


class FarmerUpdateSerializer(BaseWriteSerializer):
    class Meta(BaseWriteSerializer.Meta):
        model   = Farmer
        exclude = BaseWriteSerializer.Meta.exclude + [
            "code", "user", "verification_status", "verified_at", "rejection_reason",
        ]


class FarmCreateSerializer(BaseWriteSerializer):
    class Meta(BaseWriteSerializer.Meta):
        model   = Farm
        exclude = BaseWriteSerializer.Meta.exclude + ["code", "farmer"]

    def validate(self, attrs):
        poly = attrs.get("polygon_coordinates")
        if poly:
            from apps.core.utils import validate_polygon
            ok, err = validate_polygon(poly)
            if not ok:
                raise serializers.ValidationError({"polygon_coordinates": err})
        return attrs


class FarmUpdateSerializer(BaseWriteSerializer):
    class Meta(BaseWriteSerializer.Meta):
        model   = Farm
        exclude = BaseWriteSerializer.Meta.exclude + ["code", "farmer"]


class CropSeasonWriteSerializer(BaseWriteSerializer):
    class Meta(BaseWriteSerializer.Meta):
        model   = CropSeason
        exclude = BaseWriteSerializer.Meta.exclude + ["code", "farm"]

    def validate(self, attrs):
        ad = attrs.get("actual_harvest_date")
        pd = attrs.get("planting_date")
        if ad and pd and ad < pd:
            raise serializers.ValidationError(
                {"actual_harvest_date": "Harvest date cannot be before planting date."}
            )
        ey = attrs.get("expected_yield_kg")
        if ey and ey <= 0:
            raise serializers.ValidationError(
                {"expected_yield_kg": "Expected yield must be positive."}
            )
        return attrs


class FarmVisitWriteSerializer(BaseWriteSerializer):
    class Meta(BaseWriteSerializer.Meta):
        model   = FarmVisit
        exclude = BaseWriteSerializer.Meta.exclude + ["farm", "field_officer"]


class ProductWriteSerializer(BaseWriteSerializer):
    class Meta(BaseWriteSerializer.Meta):
        model   = Product
        exclude = BaseWriteSerializer.Meta.exclude + ["code"]

    def validate_stock_kg(self, value):
        if value < 0:
            raise serializers.ValidationError("Stock cannot be negative.")
        return value

    def validate_price_per_kg(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError("Price must be greater than 0.")
        return value


# =============================================================================
# 3. AUTH / ONBOARDING SERIALIZERS
# =============================================================================

class FarmerOnboardSerializer(serializers.Serializer):
    """
    A field officer registers a new farmer in the field.

    Authentication model:
      - Farmers do NOT use email login. They authenticate via phone number
        or Ghana card number in the field.
      - No email is ever sent to the farmer.
      - The generated credential is printed on a physical card and handed
        over in person by the field officer.

    Security posture (enforced in farmers.services.onboard_farmer):
      - Credential generated and stored in FarmerCredential — never on
        the User model in plaintext.
      - Plain password returned once in the API response for credential
        card printing, then discarded.

    Validation:
      - phone uniqueness checked against User.phone (the login identifier).
      - ghana_card_number uniqueness checked against Farmer.ghana_card_number
        (the field lives on Farmer — no need to import FarmerCredential here).
      - email uniqueness checked against User.email if provided.
    """

    first_name        = serializers.CharField(max_length=150)
    last_name         = serializers.CharField(max_length=150, required=False, allow_blank=True)
    phone             = serializers.CharField(max_length=20)
    ghana_card_number = serializers.CharField(max_length=50)
    email             = serializers.EmailField(required=False, allow_blank=True)
    region            = serializers.CharField(max_length=100, required=False, allow_blank=True)
    district          = serializers.CharField(max_length=100, required=False, allow_blank=True)

    def validate_phone(self, phone):
        phone = phone.strip()
        if User.objects.filter(phone=phone).exists():
            raise serializers.ValidationError(
                "A farmer with this phone number already exists."
            )
        return phone

    def validate_ghana_card_number(self, value):
        """
        Check uniqueness against Farmer.ghana_card_number.
        FarmerCredential is NOT imported here — the ghana_card_number field
        lives on the Farmer model itself for fast querying without a join.
        """
        value = value.strip().upper()
        if Farmer.objects.filter(ghana_card_number=value).exists():
            raise serializers.ValidationError(
                "This Ghana Card number is already registered."
            )
        return value

    def validate_email(self, email):
        if not email:
            return None
        email = email.strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(
                "A farmer with this email already exists."
            )
        return email

    def create(self, validated_data):
        from apps.farmers.services import onboard_farmer

        return onboard_farmer(
            validated_data = validated_data,
            registered_by  = self.context["request"].user,
        )


class FarmerPasswordResetSerializer(serializers.Serializer):
    """
    Admin resets a farmer's credential and receives the new plain password
    for printing on a replacement credential card.

    No email is sent. Credential is handed to the farmer in person.
    """

    def save(self, farmer: User, reset_by: User) -> str:
        from apps.farmers.services import reset_farmer_password

        return reset_farmer_password(farmer, reset_by)


class FarmerImpersonateSerializer(serializers.Serializer):
    """
    Admin acquires a short-lived JWT scoped to the farmer.

    No input fields — farmer identified via URL pk, resolved in the view.
    Token carries impersonated_by = admin.pk for audit tracing.
    """

    def save(self, farmer: User, admin: User) -> dict:
        from apps.farmers.services import impersonate_farmer

        return impersonate_farmer(farmer, admin)


# =============================================================================
# PRODUCT REVIEW SERIALIZERS
# =============================================================================

class ProductReviewSerializer(BaseModelSerializer):
    """
    Read serializer — used for public product review listings.
    buyer_name is shown; no sensitive buyer data exposed.
    """
    buyer_name     = serializers.SerializerMethodField()
    product_name   = serializers.SerializerMethodField()
    can_be_helpful = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = ProductReview
        fields = [
            "id", "buyer", "buyer_name", "product", "product_name",
            "rating", "product_satisfaction", "delivery_satisfaction",
            "title", "body", "photos",
            "is_verified_purchase", "helpful_count", "is_published",
            "can_be_helpful", "created_at", "created_ago",
        ]
        read_only_fields = [
            "id", "buyer", "buyer_name", "product_name",
            "is_verified_purchase", "helpful_count", "can_be_helpful",
        ]

    def get_buyer_name(self, obj) -> str:
        return obj.buyer.display_name

    def get_product_name(self, obj) -> str:
        return obj.product.name

    def get_can_be_helpful(self, obj) -> bool:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        buyer = getattr(request.user, "buyer_profile", None)
        if not buyer:
            return False
        from apps.farmers.models import ReviewHelpful
        return not ReviewHelpful.objects.filter(review=obj, buyer=buyer).exists()


class ProductReviewCreateSerializer(serializers.Serializer):
    """
    Review creation — validates verified purchase, prevents duplicates.
    Lives in farmers because it creates a farmers.ProductReview.
    The buyer context is passed in from the view.
    """
    product_id            = serializers.UUIDField(write_only=True)
    order_id              = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    rating                = serializers.IntegerField(min_value=1, max_value=5)
    product_satisfaction  = serializers.IntegerField(min_value=1, max_value=5, required=False)
    delivery_satisfaction = serializers.IntegerField(min_value=1, max_value=5, required=False)
    title  = serializers.CharField(max_length=200, required=False, allow_blank=True)
    body   = serializers.CharField(max_length=5000, required=False, allow_blank=True)
    photos = serializers.JSONField(required=False, default=list)

    def validate(self, attrs):
        from apps.buyers.models import Order, OrderItem

        buyer = self.context["buyer"]

        try:
            product = Product.objects.get(pk=attrs["product_id"], is_active=True)
        except Product.DoesNotExist:
            raise serializers.ValidationError({"product_id": "Product not found."})

        # Must have a delivered order containing this product
        if not OrderItem.objects.filter(
            order__buyer  = buyer,
            order__status = Order.OrderStatus.DELIVERED,
            product       = product,
        ).exists():
            raise serializers.ValidationError(
                "You can only review products from delivered orders."
            )

        if ProductReview.objects.filter(buyer=buyer, product=product).exists():
            raise serializers.ValidationError(
                "You have already reviewed this product."
            )

        attrs["product"] = product

        if attrs.get("order_id"):
            try:
                attrs["order"] = Order.objects.get(pk=attrs["order_id"], buyer=buyer)
            except Order.DoesNotExist:
                raise serializers.ValidationError({"order_id": "Order not found."})

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        buyer = self.context["buyer"]
        validated_data.pop("product_id", None)
        validated_data.pop("order_id", None)
        return ProductReview.objects.create(
            buyer                = buyer,
            is_verified_purchase = True,
            **validated_data,
        )