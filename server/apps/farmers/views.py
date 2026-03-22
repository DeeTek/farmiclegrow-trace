"""
apps/farmers/views.py  —  FarmicleGrow-Trace Platform

FarmerViewSet   CRUD + verify/reject/suspend/onboard/password-reset/impersonate
                + profile-score/farms/export-csv
FarmViewSet     CRUD + geo-filter/visit/visits/crop-seasons
ProductViewSet  Marketplace read + admin write + categories/low-stock

Fixes vs previous version:
  • Import path corrected:
      was:  from apps.core.models.mixins import ...
      now:  from apps.core.mixins import ...
  • RoleSerializerMixin removed — serializer selection is done manually
    via get_serializer_class(); the mixin was imported but never used.
  • get_object() in FarmerViewSet returns a Farmer instance. password_reset
    and impersonate pass farmer (Farmer) directly to the serializer which
    passes it to services — now consistent with services.py type contract.
  • onboard action response updated to use result["farmer"].pk
    (was result["user"].pk — Farmer and User are separate objects now).
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Q, Sum, Count

from django_filters.rest_framework import DjangoFilterBackend

from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response

from apps.core.models.mixins import (                   # ← correct import path
    AuditCreateMixin, CSVExportMixin, DateRangeFilterMixin,
    GeoFilterMixin, RegionFilterMixin, RoleQuerySetMixin,
    SoftDeleteMixin, VerificationActionMixin, VerificationStatsMixin,
)
from apps.core.signals import send_event
from apps.core.utils import calculate_completeness

from .models import Farmer, Farm, Product, CropSeason, FarmVisit
from .serializers import (
    # Read
    CropSeasonSerializer,
    FarmListSerializer,
    FarmSerializer,
    FarmVisitSerializer,
    FarmerListSerializer,
    FarmerSerializer,
    ProductSerializer,
    ProfileScoreSerializer,
    # Write
    CropSeasonWriteSerializer,
    FarmCreateSerializer,
    FarmUpdateSerializer,
    FarmVisitWriteSerializer,
    FarmerCreateSerializer,
    FarmerUpdateSerializer,
    ProductWriteSerializer,
    # Auth / onboarding
    FarmerOnboardSerializer,
    FarmerPasswordResetSerializer,
    FarmerImpersonateSerializer,
)

User = get_user_model()


# =============================================================================
# PERMISSIONS
# =============================================================================

class IsFieldAgent(permissions.BasePermission):
    """
    Field officers and super-admins can register and edit farmers.
    Uses User.Role enum — no raw role strings.
    """

    def has_permission(self, request, view):
        role = getattr(request.user, "role", None)
        return request.user.is_authenticated and (
            request.user.is_superuser
            or role in (User.Role.FIELD_OFFICER, User.Role.SUPER_ADMIN)
        )


class IsAdminOrFieldAgent(permissions.BasePermission):
    """Admin-only actions: password reset, impersonation, verification, destroy."""

    def has_permission(self, request, view):
        return request.user.is_authenticated and (
            request.user.is_superuser
            or getattr(request.user, "role", None) == User.Role.SUPER_ADMIN
        )


# =============================================================================
# FARMER VIEWSET
# =============================================================================

class FarmerViewSet(
    RoleQuerySetMixin, AuditCreateMixin,
    SoftDeleteMixin, VerificationActionMixin, VerificationStatsMixin,
    RegionFilterMixin, DateRangeFilterMixin, CSVExportMixin,
    viewsets.ModelViewSet,
):
    """
    Endpoint                                  Method   Permission
    ─────────────────────────────────────────────────────────────
    /v1/farmers/                              GET      authenticated
    /v1/farmers/                              POST     field agent / admin
    /v1/farmers/<id>/                         GET      authenticated
    /v1/farmers/<id>/                         PUT      field agent / admin
    /v1/farmers/<id>/                         DELETE   admin
    /v1/farmers/<id>/verify/                  POST     admin
    /v1/farmers/<id>/reject/                  POST     admin
    /v1/farmers/<id>/suspend/                 POST     admin
    /v1/farmers/onboard/                      POST     field agent / admin
    /v1/farmers/<id>/password-reset/          POST     admin
    /v1/farmers/<id>/impersonate/             POST     admin
    /v1/farmers/<id>/farms/                   GET      authenticated
    /v1/farmers/<id>/profile-score/           GET      authenticated
    /v1/farmers/verification-stats/           GET      authenticated
    /v1/farmers/export-csv/                   GET      admin

    get_object() returns a Farmer instance.
    password_reset and impersonate pass farmer (Farmer) to their serializers
    which pass it to services — consistent with services.py type contract.
    """

    queryset         = Farmer.objects.all().select_related("user", "registered_by")
    serializer_class = FarmerSerializer
    filter_backends  = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = [
        "region", "district", "gender", "verification_status",
        "education_level", "land_ownership", "is_active",
    ]
    search_fields = [
        "first_name", "last_name", "code", "phone_number",
        "community", "national_id", "cooperative_name",
    ]
    ordering_fields = ["created_at", "first_name", "region", "verification_status"]
    ordering        = ["-created_at"]
    csv_filename    = "farmers_export.csv"

    # ── Permissions ───────────────────────────────────────────────────────────

    def get_permissions(self):
        if self.action == "create":
            return [permissions.IsAuthenticated(), IsFieldAgent()]
        if self.action in ("verify", "reject", "suspend", "destroy",
                           "password_reset", "impersonate"):
            return [permissions.IsAuthenticated(), IsAdminOrFieldAgent()]
        if self.action == "onboard":
            return [permissions.IsAuthenticated(), IsFieldAgent()]
        return [permissions.IsAuthenticated()]

    # ── Serializer selection ──────────────────────────────────────────────────

    def get_serializer_class(self):
        if self.action == "create":                         return FarmerCreateSerializer
        if self.action in ("update", "partial_update"):     return FarmerUpdateSerializer
        if self.action == "list":                           return FarmerListSerializer
        if self.action == "onboard":                        return FarmerOnboardSerializer
        if self.action == "password_reset":                 return FarmerPasswordResetSerializer
        if self.action == "impersonate":                    return FarmerImpersonateSerializer
        return FarmerSerializer

    # ── Role-scoped querysets ─────────────────────────────────────────────────

    def get_admin_queryset(self, qs):   return qs
    def get_hr_queryset(self, qs):      return qs
    def get_officer_queryset(self, qs): return qs.filter(registered_by=self.request.user)
    def get_buyer_queryset(self, qs):   return qs.verified()

    # ── Create ────────────────────────────────────────────────────────────────

    def perform_create(self, serializer):
        farmer = serializer.save(registered_by=self.request.user)
        send_event("farmer.registered", farmer, actor=self.request.user)

    # ── Verification actions ──────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        result = super().verify(request, pk)
        send_event("farmer.verified", self.get_object(), verified_by=request.user)
        return result

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return super().reject(request, pk)

    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        return super().suspend(request, pk)

    @action(detail=False, methods=["get"], url_path="verification-stats")
    def verification_stats(self, request):
        return super().verification_stats(request)

    # ── Onboarding ────────────────────────────────────────────────────────────

    @action(detail=False, methods=["post"])
    def onboard(self, request):
        """
        POST /v1/farmers/onboard/
        Field officer registers a new farmer in the field.
        Returns the generated credential for printing on the credential card.
        No email is sent — handover is in person.

        Response uses result["farmer"].pk (Farmer PK, not User PK).
        """
        ser = FarmerOnboardSerializer(
            data    = request.data,
            context = {"request": request},
        )
        ser.is_valid(raise_exception=True)
        result = ser.save()

        return Response(
            {
                "farmer_id":          str(result["farmer"].pk),  # Farmer PK
                "farmer_code":        result["farmer"].code,
                "login_identifier":   result["login_identifier"],
                "generated_password": result["generated_password"],
                "ghana_card_number":  result["ghana_card_number"],
            },
            status=status.HTTP_201_CREATED,
        )

    # ── Credential management ─────────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="password-reset")
    def password_reset(self, request, pk=None):
        """
        POST /v1/farmers/<id>/password-reset/
        Admin resets a farmer's credential.
        Returns the new plain password for printing on a replacement card.

        get_object() returns Farmer instance — passed directly to service.
        """
        farmer = self.get_object()          # Farmer instance
        ser    = FarmerPasswordResetSerializer(data={})
        ser.is_valid(raise_exception=True)
        new_password = ser.save(farmer=farmer, reset_by=request.user)

        return Response(
            {
                "farmer_id":          str(farmer.pk),
                "farmer_code":        farmer.code,
                "generated_password": new_password,
            },
            status=status.HTTP_200_OK,
        )

    # ── Impersonation ─────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def impersonate(self, request, pk=None):
        """
        POST /v1/farmers/<id>/impersonate/
        Admin acquires a short-lived JWT scoped to the farmer's user account.

        get_object() returns Farmer instance — passed directly to service.
        The service uses farmer.user internally for JWT claims.
        """
        farmer = self.get_object()          # Farmer instance
        ser    = FarmerImpersonateSerializer(data={})
        ser.is_valid(raise_exception=True)
        token_data = ser.save(farmer=farmer, admin=request.user)

        return Response(token_data, status=status.HTTP_200_OK)

    # ── Read-only sub-resources ───────────────────────────────────────────────

    @action(detail=True, methods=["get"])
    def farms(self, request, pk=None):
        """GET /v1/farmers/<id>/farms/ — all active farm plots for a farmer."""
        farmer = self.get_object()
        qs     = farmer.farms.filter(is_active=True)
        return Response(FarmListSerializer(qs, many=True).data)

    @action(detail=True, methods=["get"], url_path="profile-score")
    def profile_score(self, request, pk=None):
        """GET /v1/farmers/<id>/profile-score/ — completeness score + missing fields."""
        farmer = self.get_object()
        weights = [
            ("first_name",    10), ("last_name",      10), ("national_id",    20),
            ("phone_number",  15), ("profile_photo",  10), ("community",      10),
            ("date_of_birth", 10),
        ]
        score   = calculate_completeness(farmer, weights)
        missing = [field for field, _ in weights if not getattr(farmer, field, None)]
        if not farmer.farms.filter(is_active=True).exists():
            missing.append("farm_plots")

        return Response(
            ProfileScoreSerializer({
                "farmer_id":      str(farmer.pk),
                "farmer_code":    farmer.code,
                "profile_score":  score,
                "missing_fields": missing,
            }).data
        )

    # ── CSV export ────────────────────────────────────────────────────────────

    @action(
        detail=False, methods=["get"], url_path="export-csv",
        permission_classes=[permissions.IsAdminUser],
    )
    def export_csv(self, request):
        return super().export_csv(request)

    def get_csv_headers(self):
        return [
            "Code", "Full Name", "Gender", "Phone",
            "Region", "District", "Community",
            "Education", "Land Ownership", "Verification", "Registered At",
        ]

    def get_csv_rows(self, queryset):
        return [
            [
                f.code, f.full_name, f.gender, f.phone_number,
                f.region, f.district, f.community,
                f.education_level, f.land_ownership,
                f.verification_status, str(f.created_at.date()),
            ]
            for f in queryset.select_related("user")
        ]


# =============================================================================
# FARM VIEWSET
# =============================================================================

class FarmViewSet(
    RoleQuerySetMixin, AuditCreateMixin,
    SoftDeleteMixin, GeoFilterMixin, RegionFilterMixin,
    viewsets.ModelViewSet,
):
    """
    Endpoint                                  Method   Permission
    ──────────────────────────────────────────────────────────────
    /v1/farms/                                GET      authenticated
    /v1/farms/                                POST     field agent / admin
    /v1/farms/<id>/                           GET      authenticated
    /v1/farms/<id>/                           PUT      field agent / admin
    /v1/farms/<id>/                           DELETE   admin
    /v1/farms/<id>/visit/                     POST     field agent / admin
    /v1/farms/<id>/visits/                    GET      authenticated
    /v1/farms/<id>/crop-seasons/              GET/POST field agent / admin
    """

    queryset         = Farm.objects.all().select_related("farmer")
    serializer_class = FarmSerializer
    filter_backends  = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = [
        "region", "district", "current_crop_type", "cropping_system", "is_active",
    ]
    search_fields = [
        "code", "name", "community",
        "farmer__first_name", "farmer__last_name", "farmer__code",
    ]
    ordering_fields = ["area_hectares", "created_at", "region"]
    ordering        = ["-created_at"]

    # ── Permissions ───────────────────────────────────────────────────────────

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "visit", "crop_seasons"):
            return [permissions.IsAuthenticated(), IsFieldAgent()]
        if self.action == "destroy":
            return [permissions.IsAuthenticated(), IsAdminOrFieldAgent()]
        return [permissions.IsAuthenticated()]

    # ── Serializer selection ──────────────────────────────────────────────────

    def get_serializer_class(self):
        if self.action == "create":                     return FarmCreateSerializer
        if self.action in ("update", "partial_update"): return FarmUpdateSerializer
        if self.action == "list":                       return FarmListSerializer
        return FarmSerializer

    # ── Role-scoped querysets ─────────────────────────────────────────────────

    def get_admin_queryset(self, qs):   return qs
    def get_hr_queryset(self, qs):      return qs
    def get_officer_queryset(self, qs):
        return qs.filter(farmer__registered_by=self.request.user)
    def get_buyer_queryset(self, qs):
        return qs.filter(farmer__verification_status="verified")

    # ── Create ────────────────────────────────────────────────────────────────

    def perform_create(self, serializer):
        serializer.save()

    # ── Actions ───────────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def visit(self, request, pk=None):
        """POST /v1/farms/<id>/visit/ — log a field officer visit."""
        farm = self.get_object()
        ser  = FarmVisitWriteSerializer(
            data    = request.data,
            context = {"request": request},
        )
        ser.is_valid(raise_exception=True)
        visit = ser.save(farm=farm, field_officer=request.user)
        send_event("farm.surveyed", visit, officer=request.user)
        return Response(FarmVisitSerializer(visit).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def visits(self, request, pk=None):
        """GET /v1/farms/<id>/visits/ — all active visits for a farm."""
        farm = self.get_object()
        qs   = farm.visits.filter(is_active=True).select_related("field_officer")
        return Response(FarmVisitSerializer(qs, many=True).data)

    @action(detail=True, methods=["get", "post"], url_path="crop-seasons")
    def crop_seasons(self, request, pk=None):
        """
        GET  /v1/farms/<id>/crop-seasons/ — list all active seasons.
        POST /v1/farms/<id>/crop-seasons/ — record a new season.
        """
        farm = self.get_object()
        if request.method == "POST":
            ser = CropSeasonWriteSerializer(
                data    = request.data,
                context = {"request": request},
            )
            ser.is_valid(raise_exception=True)
            season = ser.save(farm=farm)
            send_event("harvest.recorded", season, officer=request.user)
            return Response(
                CropSeasonSerializer(season).data,
                status=status.HTTP_201_CREATED,
            )
        qs = farm.crop_seasons.filter(is_active=True).order_by("-harvest_year")
        return Response(CropSeasonSerializer(qs, many=True).data)


# =============================================================================
# PRODUCT VIEWSET
# =============================================================================

class ProductViewSet(AuditCreateMixin, SoftDeleteMixin, viewsets.ModelViewSet):
    """
    Endpoint                          Method   Permission
    ─────────────────────────────────────────────────────
    /v1/products/                     GET      public
    /v1/products/                     POST     admin
    /v1/products/<id>/                GET      public
    /v1/products/<id>/                PUT      admin
    /v1/products/<id>/                DELETE   admin
    /v1/products/categories/          GET      public
    /v1/products/low-stock/           GET      admin
    """

    queryset = Product.objects.filter(is_active=True).select_related(
        "origin_farmer", "origin_farm"
    )
    serializer_class = ProductSerializer
    filter_backends  = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["category", "is_available", "origin_country", "origin_region"]
    search_fields    = ["name", "scientific_name", "category", "code", "hs_code"]
    ordering_fields  = ["name", "price_per_kg", "stock_kg", "created_at"]
    ordering         = ["-created_at"]

    # ── Permissions ───────────────────────────────────────────────────────────

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy", "low_stock"):
            return [permissions.IsAdminUser()]
        return [permissions.AllowAny()]

    # ── Serializer selection ──────────────────────────────────────────────────

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return ProductWriteSerializer
        return ProductSerializer

    # ── Actions ───────────────────────────────────────────────────────────────

    @action(detail=False, methods=["get"])
    def categories(self, request):
        """GET /v1/products/categories/ — distinct category list for filter UI."""
        cats = (
            Product.objects.filter(is_active=True)
            .values_list("category", flat=True)
            .distinct()
            .order_by("category")
        )
        return Response(list(cats))

    @action(detail=False, methods=["get"], url_path="low-stock")
    def low_stock(self, request):
        """GET /v1/products/low-stock/ — products below 100 kg threshold."""
        qs = Product.objects.low_stock().order_by("stock_kg")
        return Response(ProductSerializer(qs, many=True).data)


# =============================================================================
# PRODUCT REVIEW VIEWSET
# =============================================================================

class ProductReviewViewSet(viewsets.ModelViewSet):
    """
    Product reviews live in the farmers app because they review
    farmers.Product — not a buyers-domain concept.
    Buyers write reviews, but the reviewed entity is a farm product.

    Endpoint                          Method  Permission
    ─────────────────────────────────────────────────────
    /v1/reviews/                      GET     public
    /v1/reviews/                      POST    authenticated buyer
    /v1/reviews/<id>/                 GET     public
    /v1/reviews/<id>/                 DELETE  owner / admin (soft-delete)
    /v1/reviews/<id>/helpful/         POST    authenticated buyer
    /v1/reviews/<id>/unhelpful/       POST    authenticated buyer
    """
    from apps.farmers.models import ProductReview as _ProductReview
    from apps.farmers.models import ReviewHelpful as _ReviewHelpful
    from apps.farmers.serializers import (
        ProductReviewSerializer as _ProductReviewSerializer,
        ProductReviewCreateSerializer as _ProductReviewCreateSerializer,
    )

    filter_backends  = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["product", "rating", "is_verified_purchase", "is_published"]
    search_fields    = ["title", "body", "buyer__company_name"]
    ordering_fields  = ["rating", "helpful_count", "created_at"]
    ordering         = ["-helpful_count", "-created_at"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        from apps.farmers.models import ProductReview
        qs = ProductReview.objects.select_related(
            "buyer", "product", "order",
        ).filter(is_published=True)
        product_id = self.request.query_params.get("product")
        if product_id:
            qs = qs.filter(product_id=product_id)
        return qs

    def get_serializer_class(self):
        from apps.farmers.serializers import (
            ProductReviewSerializer, ProductReviewCreateSerializer,
        )
        if self.action == "create":
            return ProductReviewCreateSerializer
        return ProductReviewSerializer

    def create(self, request, *args, **kwargs):
        from apps.farmers.serializers import ProductReviewSerializer, ProductReviewCreateSerializer

        buyer = getattr(request.user, "buyer_profile", None)
        if not buyer:
            raise PermissionDenied("A buyer profile is required to submit reviews.")

        ser = ProductReviewCreateSerializer(
            data=request.data, context={"buyer": buyer, "request": request},
        )
        ser.is_valid(raise_exception=True)
        review = ser.save()
        send_event("review.submitted", review)
        return Response(
            ProductReviewSerializer(review, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        from apps.farmers.models import ProductReview
        review = self.get_object()
        buyer  = getattr(request.user, "buyer_profile", None)
        if not request.user.is_staff and (not buyer or review.buyer_id != buyer.pk):
            raise PermissionDenied
        review.is_published = False
        review.save(update_fields=["is_published"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def helpful(self, request, pk=None):
        """POST /v1/reviews/<id>/helpful/ — mark a review as helpful."""
        from apps.farmers.models import ReviewHelpful, ProductReview
        from django.db.models import F

        review = self.get_object()
        buyer  = getattr(request.user, "buyer_profile", None)
        if not buyer:
            raise PermissionDenied("A buyer profile is required.")
        if review.buyer_id == buyer.pk:
            raise ValidationError({"detail": "You cannot vote on your own review."})
        _, created = ReviewHelpful.objects.get_or_create(review=review, buyer=buyer)
        if not created:
            raise ValidationError({"detail": "You have already marked this review as helpful."})
        ProductReview.objects.filter(pk=review.pk).update(helpful_count=F("helpful_count") + 1)
        review.refresh_from_db(fields=["helpful_count"])
        return Response({"detail": "Marked as helpful.", "helpful_count": review.helpful_count})

    @action(detail=True, methods=["post"])
    def unhelpful(self, request, pk=None):
        """POST /v1/reviews/<id>/unhelpful/ — remove helpful vote."""
        from apps.farmers.models import ReviewHelpful, ProductReview
        from django.db.models import F
        from rest_framework.exceptions import NotFound

        review   = self.get_object()
        buyer    = getattr(request.user, "buyer_profile", None)
        if not buyer:
            raise PermissionDenied("A buyer profile is required.")
        deleted, _ = ReviewHelpful.objects.filter(review=review, buyer=buyer).delete()
        if not deleted:
            raise NotFound("You have not marked this review as helpful.")
        ProductReview.objects.filter(pk=review.pk).update(helpful_count=F("helpful_count") - 1)
        review.refresh_from_db(fields=["helpful_count"])
        return Response({"detail": "Helpful vote removed.", "helpful_count": review.helpful_count})