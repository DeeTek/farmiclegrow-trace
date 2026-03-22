"""
apps/farmers/models.py  —  FarmicleGrow-Trace Platform

Models:
  Farmer           Smallholder farmer profile
  FarmerCredential Ghana card + generated credential for field login
  Farm             GPS-mapped farm plot
  Product          Marketplace agricultural commodity
  CropSeason       Seasonal planting/harvest data per farm
  FarmVisit        Field officer site visit + produce collection

SRD coverage (Section 4 — MODULE 2, 3, 4):
  ✓ Farmer personal info, location, socioeconomic, Ghana card
  ✓ Farm GPS, size, land ownership, soil, cropping system
  ✓ Seasonal data: variety, seeds, fertiliser, harvest, labour
  ✓ Field officer produce collection per farm per visit
  ✓ FarmerCredential: stores generated password + login identifier
  ✓ Product marketplace: quality, stock, origin traceability link
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models.abstract import BaseModel, CodedModel, GeoModel
from apps.core.models.base import BasePersonModel, BaseTracedModel
from apps.core.models.managers import FarmerManager, FarmManager, ProductManager
from apps.core.models.querysets import (
  FarmerQuerySet, FarmQuerySet, ProductQuerySet, ProductReviewQuerySet
  )


# =============================================================================
# FARMER
# =============================================================================

class Farmer(BasePersonModel):
    """
    Smallholder farmer profile.

    Inherits from BasePersonModel:
      UUID pk · code (FMR-AS-83421) · timestamps · soft-delete ·
      verification workflow · first/last name · gender · DOB ·
      national_id · profile_photo · phone_number · email.

    Ghana card number lives here (not on FarmerCredential) so it can be
    queried for duplicate checking without joining the credential table.
    FarmerCredential stores the generated password and login metadata.
    """

    CODE_PREFIX       = "FMR"
    CODE_REGION_FIELD = "region"

    # ── Auth link ─────────────────────────────────────────────────────────────
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete     = models.PROTECT,
        related_name  = "farmer_profile",
    )
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete    = models.SET_NULL,
        null=True, blank=True,
        related_name = "registered_farmers",
        help_text    = _("Field officer or admin who registered this farmer."),
    )

    # ── Identity / verification ───────────────────────────────────────────────
    ghana_card_number = models.CharField(
        max_length=30, unique=True, db_index=True,
        help_text=_("Ghana National ID card number (GHA-XXXXXXXXX-X)."),
    )

    # ── Location ──────────────────────────────────────────────────────────────
    community     = models.CharField(max_length=150, blank=True, db_index=True)
    district      = models.CharField(max_length=100, db_index=True)
    region        = models.CharField(max_length=100, db_index=True)
    gps_latitude  = models.DecimalField(
                        max_digits=9, decimal_places=6, null=True, blank=True,
                        help_text=_("Approximate home/farm GPS latitude."),
                    )
    gps_longitude = models.DecimalField(
                        max_digits=9, decimal_places=6, null=True, blank=True,
                        help_text=_("Approximate home/farm GPS longitude."),
                    )
    landmark      = models.CharField(
                        max_length=255, blank=True,
                        help_text=_("Nearest landmark to the farmer's location."),
                    )

    # ── Socioeconomic ─────────────────────────────────────────────────────────
    education_level = models.CharField(
        max_length=30,
        choices=[
            ("none",       _("No Formal Education")),
            ("primary",    _("Primary")),
            ("jhs",        _("Junior High School")),
            ("shs",        _("Senior High School")),
            ("vocational", _("Vocational / Technical")),
            ("tertiary",   _("Tertiary")),
        ],
        blank=True,
    )
    cooperative_name = models.CharField(max_length=200, blank=True, db_index=True)
    land_ownership   = models.CharField(
        max_length=20,
        choices=[
            ("owned",    _("Owned")),
            ("leased",   _("Leased")),
            ("communal", _("Communal")),
            ("family",   _("Family Land")),
        ],
        blank=True,
    )

    objects = FarmerManager.from_queryset(FarmerQuerySet)()

    class Meta(BasePersonModel.Meta):
        verbose_name        = _("Farmer")
        verbose_name_plural = _("Farmers")
        indexes = [
            models.Index(fields=["region", "district"]),
            models.Index(fields=["region", "verification_status"]),
            models.Index(fields=["ghana_card_number"]),
        ]

    @property
    def farmer_code(self) -> str:
        return self.code

    @property
    def has_gps(self) -> bool:
        return self.gps_latitude is not None and self.gps_longitude is not None

    def __str__(self) -> str:
        return f"{self.code} — {self.full_name}"


# =============================================================================
# FARMER CREDENTIAL
# =============================================================================

class FarmerCredential(models.Model):
    """
    Stores the generated credential for a farmer's field login.

    Farmers do NOT use email + password authentication. They log in via:
      • Phone number OR
      • Ghana Card number
    combined with a generated plain-text password printed on a physical
    credential card handed over in person.

    Security posture:
      - generated_password stores the plain text for admin/officer reference
        and credential card reprinting ONLY.
      - The hashed version lives on User.password (Django standard).
      - must_change_password is always False (farmers use printed cards).
      - login_identifier stores the primary field identifier (phone or email).
      - registered_by records the field officer who created the record.

    This model lives in apps.farmers (not apps.accounts) because it is a
    farmer-domain concern — not a generic auth concept.
    """

    farmer = models.OneToOneField(
        Farmer,
        on_delete    = models.CASCADE,
        related_name = "credential",
        primary_key  = True,
    )
    login_identifier    = models.CharField(
                              max_length=255,
                              help_text=_("Phone number or email used for field login."),
                          )
    generated_password  = models.CharField(
                              max_length=50,
                              help_text=_("Plain-text credential for printing on credential card."),
                          )
    must_change_password = models.BooleanField(
                              default=False,
                              help_text=_("Always False for farmers — no self-service change flow."),
                          )
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete    = models.SET_NULL,
        null=True, blank=True,
        related_name = "farmer_credentials_created",
        help_text    = _("Field officer or admin who created this credential."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label           = "farmers"
        verbose_name        = _("Farmer Credential")
        verbose_name_plural = _("Farmer Credentials")

    def __str__(self) -> str:
        return f"Credential for {self.farmer.code} — identifier: {self.login_identifier}"


# =============================================================================
# FARM
# =============================================================================

class Farm(BaseModel, CodedModel, GeoModel):
    """
    Individual GPS-mapped farm plot.

    GeoModel provides:
      latitude, longitude, altitude_meters, polygon_coordinates,
      gps_accuracy_meters, gps_captured_at, has_coordinates (property).
    """

    CODE_PREFIX = "FRM"

    farmer    = models.ForeignKey(Farmer, on_delete=models.CASCADE, related_name="farms")
    name      = models.CharField(max_length=200, blank=True)
    community = models.CharField(max_length=150, blank=True)
    district  = models.CharField(max_length=100, db_index=True)
    region    = models.CharField(max_length=100, db_index=True)
    landmark  = models.CharField(max_length=255, blank=True)

    area_hectares = models.DecimalField(
        max_digits  = 8,
        decimal_places = 4,
        validators  = [MinValueValidator(Decimal("0.001"))],
        help_text   = _("Farm size in hectares."),
    )
    land_ownership = models.CharField(
        max_length = 20,
        choices    = [
            ("owned",    _("Owned")),
            ("leased",   _("Leased")),
            ("communal", _("Communal")),
            ("family",   _("Family Land")),
        ],
        blank=True,
    )
    soil_type          = models.CharField(max_length=100, blank=True)
    current_crop_type  = models.CharField(max_length=100, blank=True, db_index=True)
    previous_crop_type = models.CharField(
                             max_length=100, blank=True,
                             help_text=_("Crop grown on this plot last season (SRD: historical cropping)."),
                         )
    cropping_system    = models.CharField(
        max_length = 20,
        choices    = [
            ("monocropping",  _("Mono-cropping")),
            ("intercropping", _("Inter-cropping")),
            ("mixed",         _("Mixed Cropping")),
            ("rotational",    _("Rotational")),
        ],
        blank=True,
    )

    objects = FarmManager.from_queryset(FarmQuerySet)()

    class Meta(BaseModel.Meta):
        verbose_name        = _("Farm")
        verbose_name_plural = _("Farms")
        indexes = [
            models.Index(fields=["farmer", "is_active"]),
            models.Index(fields=["region",  "district"]),
        ]

    @property
    def farm_code(self) -> str:
        return self.code

    @property
    def area_category(self) -> str:
        ha = float(self.area_hectares)
        if ha < 2:   return "smallholder"
        if ha <= 5:  return "medium"
        return "large"

    def __str__(self) -> str:
        return f"{self.code} — {self.farmer.full_name} ({self.area_hectares} ha)"


# =============================================================================
# PRODUCT
# =============================================================================

class Product(BaseModel, CodedModel):
    """Agricultural product listed on the marketplace."""

    CODE_PREFIX = "PRD"

    name            = models.CharField(max_length=200, db_index=True)
    scientific_name = models.CharField(max_length=200, blank=True)
    category        = models.CharField(max_length=100, db_index=True)
    hs_code         = models.CharField(
                          max_length=20, blank=True,
                          help_text=_("Harmonised System tariff code."),
                      )
    description     = models.TextField(blank=True)
    photo           = models.ImageField(
                          upload_to="products/photos/%Y/%m/", null=True, blank=True,
                      )

    # Origin traceability
    origin_farmer  = models.ForeignKey(
                         Farmer, on_delete=models.SET_NULL, null=True, blank=True,
                         related_name="products",
                     )
    origin_farm    = models.ForeignKey(
                         Farm, on_delete=models.SET_NULL, null=True, blank=True,
                         related_name="products",
                     )
    origin_country = models.CharField(max_length=100, default="Ghana")
    origin_region  = models.CharField(max_length=100, blank=True)

    # Marketplace
    price_per_kg = models.DecimalField(
                       max_digits=10, decimal_places=2, null=True, blank=True,
                   )
    currency     = models.CharField(max_length=5, default="GHS")
    stock_kg     = models.DecimalField(
                       max_digits=12, decimal_places=2, default=Decimal("0.00"),
                   )
    min_order_kg = models.DecimalField(
                       max_digits=10, decimal_places=2, default=Decimal("1.00"),
                   )
    is_available = models.BooleanField(default=True, db_index=True)

    # Quality grading (SRD: moisture, impurities, grade, certifications)
    moisture_pct = models.DecimalField(
                       max_digits=5, decimal_places=2, null=True, blank=True,
                       help_text=_("Moisture content percentage."),
                   )
    impurity_pct = models.DecimalField(
                       max_digits=5, decimal_places=2, null=True, blank=True,
                       help_text=_("Impurity level percentage."),
                   )
    grade        = models.CharField(max_length=20, blank=True)

    objects = ProductManager.from_queryset(ProductQuerySet)()

    class Meta(BaseModel.Meta):
        verbose_name        = _("Product")
        verbose_name_plural = _("Products")
        indexes = [
            models.Index(fields=["category",       "is_available"]),
            models.Index(fields=["origin_country",  "category"]),
        ]

    @property
    def stock_status(self) -> str:
        if self.stock_kg <= 0:   return "out_of_stock"
        if self.stock_kg < 100:  return "low_stock"
        return "in_stock"

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


# =============================================================================
# CROP SEASON
# =============================================================================

class CropSeason(BaseTracedModel):
    """
    One seasonal planting/harvest cycle for a farm plot.

    Inherits from BaseTracedModel:
      code (CSN-...) · timestamps · soft-delete · created_by / updated_by.

    Covers all SRD MODULE 2 seasonal data requirements:
      variety, seed source, planting method, seed quantity, fertiliser
      (type/brand/quantity/timing), expected/actual harvest, labour.
    """

    CODE_PREFIX = "CSN"

    farm         = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name="crop_seasons")
    harvest_year = models.PositiveSmallIntegerField(db_index=True)

    # ── Planting ──────────────────────────────────────────────────────────────
    crop_variety    = models.CharField(max_length=100)
    planting_method = models.CharField(
        max_length = 30,
        choices    = [
            ("direct_seeding", _("Direct Seeding")),
            ("transplanting",  _("Transplanting")),
            ("broadcasting",   _("Broadcasting")),
        ],
        blank=True,
    )
    planting_date    = models.DateField(null=True, blank=True)
    seed_source      = models.CharField(max_length=100, blank=True)
    seed_quantity_kg = models.DecimalField(
                           max_digits=8, decimal_places=2, null=True, blank=True,
                       )

    # ── Fertiliser ────────────────────────────────────────────────────────────
    fertilizer_type = models.CharField(
        max_length = 15,
        choices    = [
            ("organic",   _("Organic")),
            ("inorganic", _("Inorganic")),
            ("both",      _("Both")),
            ("none",      _("None")),
        ],
        default="none",
    )
    fertilizer_brand       = models.CharField(max_length=100, blank=True)
    fertilizer_quantity_kg = models.DecimalField(
                                 max_digits=8, decimal_places=2, null=True, blank=True,
                             )
    fertilizer_applied_at  = models.DateField(
                                 null=True, blank=True,
                                 help_text=_("Date fertiliser was applied (SRD: time of application)."),
                             )

    # ── Harvest ───────────────────────────────────────────────────────────────
    expected_harvest_date = models.DateField(
                                null=True, blank=True,
                                help_text=_("Farmer's expected harvest date (SRD: time to harvest)."),
                            )
    actual_harvest_date   = models.DateField(null=True, blank=True)
    expected_yield_kg     = models.DecimalField(
                                max_digits=10, decimal_places=2, null=True, blank=True,
                            )
    actual_yield_kg       = models.DecimalField(
                                max_digits=10, decimal_places=2, null=True, blank=True,
                            )

    # ── Labour ────────────────────────────────────────────────────────────────
    labour_type  = models.CharField(
        max_length = 10,
        choices    = [
            ("family", _("Family")),
            ("hired",  _("Hired")),
            ("both",   _("Both")),
        ],
        blank=True,
    )
    labour_count = models.PositiveSmallIntegerField(
                       null=True, blank=True,
                       help_text=_("Number of labourers used."),
                   )

    # ── Notes ─────────────────────────────────────────────────────────────────
    notes = models.TextField(
                blank=True,
                help_text=_("Additional agronomic observations for this season."),
            )

    class Meta(BaseTracedModel.Meta):
        verbose_name = _("Crop Season")
        ordering     = ["-harvest_year", "-created_at"]
        indexes      = [models.Index(fields=["farm", "harvest_year"])]

    @property
    def yield_variance_pct(self):
        if (self.expected_yield_kg and self.actual_yield_kg
                and self.expected_yield_kg > 0):
            return round(
                (float(self.actual_yield_kg) - float(self.expected_yield_kg))
                / float(self.expected_yield_kg) * 100, 1
            )
        return None

    def __str__(self) -> str:
        return (
            f"{self.code} — {self.farm.code} "
            f"{self.crop_variety} {self.harvest_year}"
        )


# =============================================================================
# FARM VISIT
# =============================================================================

class FarmVisit(BaseModel, GeoModel):
    """
    Field officer site visit to a farm.

    GeoModel records the officer's GPS location at the time of visit —
    proving the officer was physically present (SRD MODULE 3: staff location
    when taking data).

    produce_collected_kg covers the SRD requirement: raw funnel (produce)
    collected from each farmer per farm, displayed on the officer dashboard.
    """

    farm = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name="visits")
    field_officer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete    = models.SET_NULL,
        null=True,
        related_name = "farm_visits",
    )
    visited_at = models.DateTimeField(db_index=True)
    purpose    = models.CharField(
        max_length = 30,
        choices    = [
            ("registration",  _("Initial Registration")),
            ("monitoring",    _("Crop Monitoring")),
            ("harvest",       _("Harvest Collection")),
            ("verification",  _("Data Verification")),
            ("follow_up",     _("Follow-up Visit")),
        ],
        default="monitoring",
    )
    observations         = models.TextField(blank=True)
    produce_collected_kg = models.DecimalField(
        max_digits   = 10,
        decimal_places = 2,
        null=True, blank=True,
        help_text    = _(
            "Kg of raw produce collected during this visit. "
            "Aggregated on the officer dashboard (SRD MODULE 3)."
        ),
    )
    photos = models.JSONField(
                 default=list, blank=True,
                 help_text=_("List of uploaded photo URLs for this visit."),
             )

    class Meta(BaseModel.Meta):
        verbose_name = _("Farm Visit")
        ordering     = ["-visited_at"]
        indexes      = [models.Index(fields=["farm", "visited_at"])]

    def __str__(self) -> str:
        name = getattr(self.field_officer, "get_full_name", lambda: "Unknown")()
        return f"Visit to {self.farm.code} by {name} on {self.visited_at:%Y-%m-%d}"


# =============================================================================
# PRODUCT REVIEW
# =============================================================================

class ProductReview(BaseModel):
    """
    Buyer review of a farm Product.

    Lives in apps.farmers because it reviews a farmers.Product —
    not a buyers-domain concept. The buyer FK is a cross-app reference
    (buyers write reviews of farmers' products).

    Only verified-purchase reviews are allowed: the buyer must have
    a delivered Order containing this product.

    SRD MODULE 7: buyer feedback contributes to farmer and staff
    performance scores.
    """

    from django.core.validators import MinValueValidator, MaxValueValidator

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="reviews",
    )
    buyer = models.ForeignKey(
        "buyers.Buyer",
        on_delete    = models.CASCADE,
        related_name = "product_reviews",
        help_text    = _("Buyer who wrote this review."),
    )
    order = models.ForeignKey(
        "buyers.Order",
        on_delete    = models.SET_NULL,
        null=True, blank=True,
        related_name = "reviews",
        help_text    = _("Order that this review is linked to."),
    )

    # Ratings (1–5)
    rating                = models.PositiveSmallIntegerField(
                                validators=[MinValueValidator(1), MaxValueValidator(5)],
                            )
    product_satisfaction  = models.PositiveSmallIntegerField(
                                null=True, blank=True,
                                validators=[MinValueValidator(1), MaxValueValidator(5)],
                            )
    delivery_satisfaction = models.PositiveSmallIntegerField(
                                null=True, blank=True,
                                validators=[MinValueValidator(1), MaxValueValidator(5)],
                            )

    # Content
    title  = models.CharField(max_length=200, blank=True)
    body   = models.TextField(blank=True)
    photos = models.JSONField(
                 default=list, blank=True,
                 help_text=_("List of review photo URLs."),
             )

    # Status
    is_verified_purchase = models.BooleanField(
                               default=False,
                               help_text=_("True when buyer has a delivered order for this product."),
                           )
    is_published = models.BooleanField(default=True, db_index=True)
    helpful_count = models.PositiveIntegerField(default=0)
    
    objects = ProductReviewQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        verbose_name        = _("Product Review")
        verbose_name_plural = _("Product Reviews")
        unique_together     = [("buyer", "product")]
        ordering            = ["-helpful_count", "-created_at"]
        indexes             = [
            models.Index(fields=["product", "is_published"]),
            models.Index(fields=["buyer",   "product"]),
        ]

    def __str__(self) -> str:
        return f"{self.product.name} — {self.rating}★ by {self.buyer}"


class ReviewHelpful(BaseModel):
    """
    One-per-buyer helpful vote on a ProductReview.
    Unique constraint prevents duplicate votes.
    """

    review = models.ForeignKey(
        ProductReview, on_delete=models.CASCADE, related_name="helpful_votes",
    )
    buyer  = models.ForeignKey(
        "buyers.Buyer", on_delete=models.CASCADE, related_name="helpful_votes",
    )

    class Meta(BaseModel.Meta):
        verbose_name    = _("Review Helpful Vote")
        unique_together = [("review", "buyer")]

    def __str__(self) -> str:
        return f"{self.buyer} → helpful on review {self.review_id}"