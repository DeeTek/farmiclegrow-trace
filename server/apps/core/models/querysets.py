"""
apps/core/querysets.py  —  FarmicleGrow-Trace Platform

QuerySet classes and reusable query helpers for the entire system.

Architecture
────────────
Every domain QuerySet is a class that extends BaseQuerySet (from managers.py).
Each class is attached to its model via a custom Manager. This means callers
can chain methods naturally:

    Farmer.objects.verified().in_region("Ashanti").with_farm_count()[:20]
    Order.objects.pending().high_value(threshold=10_000).with_buyer_name()

Section map
───────────
  1.  Imports & type aliases
  2.  Shared annotation expressions  (reusable Expressions / Value objects)
  3.  BaseQuerySet                   (active, deleted, search, time helpers)
  4.  VerifiableQuerySet             (verified, pending, rejected + transitions)
  5.  FarmerQuerySet                 (region, gender, completeness, geo, batch)
  6.  FarmQuerySet                   (area, GPS, crop type, officer assignment)
  7.  CropSeasonQuerySet             (harvest window, yield, fertilizer type)
  8.  FieldOfficerQuerySet           (performance, assignment, region KPIs)
  9.  TraceabilityQuerySet           (chain lookup, batch, QR, export chain)
 10.  BatchQuerySet                  (farmer batches, warehouse batches, codes)
 11.  OrderQuerySet                  (status workflow, value, buyer, dispatch)
 12.  PaymentQuerySet                (MTD/YTD totals, mobile money, status)
 13.  ProductQuerySet                (availability, category, marketplace)
 14.  ReviewQuerySet                 (rating aggregation, sentiment breakdown)
 15.  NotificationQuerySet           (unread, by type, bulk-mark-read)
 16.  ImpactQuerySet                 (women %, CO2 savings, regional KPIs)
 17.  ReportQuerySet                 (generation status, export scheduling)
 18.  ─── Reusable standalone helpers (kept for backward compatibility) ────────
 19.  Time-series helpers            (get_time_series, compare_periods, MTD/YTD)
 20.  Regional summary helpers       (annotate_region_summary, get_leaderboard)
 21.  Geo proximity helpers          (nearby_query, distance_annotated)
 22.  Traceability chain helpers     (build_chain, resolve_qr_code)
 23.  Dashboard aggregation helpers  (build_kpi_block, multi_model_dashboard)
"""

from __future__ import annotations

import math
from typing import Optional, TYPE_CHECKING

from django.db import models
from django.db.models import (
    Avg, Case, Count, DecimalField, ExpressionWrapper, F, FloatField,
    IntegerField, Max, Min, OuterRef, Q, StdDev, Subquery, Sum, Value, When,
)
from django.db.models.functions import (
    Coalesce, ExtractMonth, ExtractYear, Greatest, Length,
    Now, Round, TruncDate, TruncMonth, TruncQuarter, TruncWeek, TruncYear,
)
from django.utils import timezone

# ─── TYPE ALIASES ────────────────────────────────────────────────────────────

QS = models.QuerySet   # short alias used in return-type annotations


# =============================================================================
# 2.  SHARED ANNOTATION EXPRESSIONS
# =============================================================================

# Reusable conditional expressions used across multiple QuerySets

IS_VERIFIED = Q(verification_status="verified")
IS_PENDING  = Q(verification_status="pending")
IS_REJECTED = Q(verification_status="rejected")
IS_ACTIVE   = Q(is_active=True, deleted_at__isnull=True)

# Conditional count helpers — use as annotation arguments
def _verified_count(field: str = "id") -> Count:
    return Count(field, filter=IS_VERIFIED, distinct=True)

def _pending_count(field: str = "id") -> Count:
    return Count(field, filter=IS_PENDING, distinct=True)

def _active_count(field: str = "id") -> Count:
    return Count(field, filter=IS_ACTIVE, distinct=True)


# =============================================================================
# 3.  BASE QUERYSET
# =============================================================================

class BaseQuerySet(models.QuerySet):
    """
    Root QuerySet for every FarmicleGrow-Trace model.

    Provides:
    ─ Soft-delete awareness    .active() / .deleted() / .with_deleted()
    ─ Bulk state changes       .activate() / .deactivate() / .soft_delete()
    ─ Generic keyword search   .search(q, fields)
    ─ Time-range scoping       .created_between() / .created_today() / .recent()
    ─ Period helpers           .mtd() / .ytd() / .this_week()
    ─ Optimised loading        .slim() / .ids_only()
    ─ Large-dataset iteration  .in_batches()
    ─ Set operations           .union_with() / .intersect_with()
    """

    # ── Soft-delete / active ──────────────────────────────────────────────────

    def active(self) -> QS:
        """Return only live (non-deleted, is_active=True) records."""
        return self.filter(is_active=True, deleted_at__isnull=True)

    def inactive(self) -> QS:
        return self.filter(is_active=False)

    def deleted(self) -> QS:
        return self.filter(deleted_at__isnull=False)

    def with_deleted(self) -> QS:
        """Bypass soft-delete filter — includes deleted records."""
        return self.all()

    # ── Bulk state ────────────────────────────────────────────────────────────

    def activate(self) -> int:
        return self.update(is_active=True, deleted_at=None)

    def deactivate(self) -> int:
        return self.update(is_active=False)

    def soft_delete(self) -> int:
        return self.update(is_active=False, deleted_at=timezone.now())

    # ── Generic search ────────────────────────────────────────────────────────

    def search(self, query: str, fields: list[str]) -> QS:
        """
        Multi-field case-insensitive search.

            qs.search("Kwame", ["first_name__icontains", "farmer_code__icontains"])

        Fields that contain '__' are used as-is.
        Plain field names get '__icontains' appended automatically.
        """
        if not query or len(query.strip()) < 2:
            return self.none()
        q = Q()
        for f in fields:
            lookup = f if "__" in f else f"{f}__icontains"
            q |= Q(**{lookup: query.strip()})
        return self.filter(q).distinct()

    # ── Time-range scoping ────────────────────────────────────────────────────

    def created_between(self, start, end) -> QS:
        return self.filter(created_at__range=(start, end))

    def created_today(self) -> QS:
        return self.filter(created_at__date=timezone.now().date())

    def this_week(self) -> QS:
        start = timezone.now() - timezone.timedelta(days=7)
        return self.filter(created_at__gte=start)

    def mtd(self, date_field: str = "created_at") -> QS:
        """Month-to-date records."""
        now = timezone.now()
        return self.filter(**{
            f"{date_field}__month": now.month,
            f"{date_field}__year":  now.year,
        })

    def ytd(self, date_field: str = "created_at") -> QS:
        """Year-to-date records."""
        return self.filter(**{f"{date_field}__year": timezone.now().year})

    def recent(self, days: int = 30) -> QS:
        cutoff = timezone.now() - timezone.timedelta(days=days)
        return self.filter(created_at__gte=cutoff)

    # ── Optimised loading ─────────────────────────────────────────────────────

    def slim(self, *fields) -> QS:
        """
        Defer all fields except those listed.
        Use on list views where only a subset of columns is needed.
        """
        if not fields:
            return self
        return self.only(*fields)

    def ids_only(self) -> QS:
        """Return a flat QuerySet of PKs only. Efficient for existence checks."""
        return self.values_list("id", flat=True)

    # ── Large-dataset helpers ─────────────────────────────────────────────────

    def in_batches(self, size: int = 500):
        """
        Generator that yields records in chunks of `size`.
        Prevents memory exhaustion on large tables.

            for batch in Farmer.objects.all().in_batches(1000):
                process(batch)
        """
        ids = list(self.values_list("id", flat=True))
        for i in range(0, len(ids), size):
            yield self.model.objects.filter(id__in=ids[i: i + size])

    # ── Set operations ────────────────────────────────────────────────────────

    def union_with(self, other_qs: QS) -> QS:
        return self.union(other_qs)

    def intersect_with(self, other_qs: QS) -> QS:
        return self.intersection(other_qs)

    # ── Pagination convenience ────────────────────────────────────────────────

    def page(self, page_number: int = 1, page_size: int = 20) -> QS:
        offset = (page_number - 1) * page_size
        return self[offset: offset + page_size]


# =============================================================================
# 4.  VERIFIABLE QUERYSET
# =============================================================================

class VerifiableQuerySet(BaseQuerySet):
    """
    Extends BaseQuerySet for models with a verification workflow:
    Farmer, Buyer, FieldOfficer, BuyerDocument.

    Verification states:  pending → verified | rejected | suspended
    """

    def verified(self) -> QS:
        return self.filter(verification_status="verified")

    def pending_verification(self) -> QS:
        return self.filter(verification_status="pending")

    def rejected(self) -> QS:
        return self.filter(verification_status="rejected")

    def suspended(self) -> QS:
        return self.filter(verification_status="suspended")

    def verified_after(self, dt) -> QS:
        return self.filter(verified_at__gte=dt)

    def verified_this_month(self) -> QS:
        now = timezone.now()
        return self.filter(
            verified_at__month=now.month,
            verified_at__year=now.year,
        )

    def with_verification_age(self) -> QS:
        """
        Annotates each record with `days_since_verified` (integer).
        Useful for dashboard ageing reports.
        """
        return self.annotate(
            days_since_verified=ExpressionWrapper(
                (Now() - F("verified_at")) / timezone.timedelta(days=1),
                output_field=IntegerField(),
            )
        )

    def verification_summary(self) -> dict:
        """
        Single-query aggregation of verification states.
        Returns: {verified, pending, rejected, suspended, total}
        """
        return self.aggregate(
            total     = Count("id"),
            verified  = Count("id", filter=IS_VERIFIED),
            pending   = Count("id", filter=IS_PENDING),
            rejected  = Count("id", filter=IS_REJECTED),
            suspended = Count("id", filter=Q(verification_status="suspended")),
        )


# =============================================================================
# 5.  FARMER QUERYSET
# =============================================================================

class FarmerQuerySet(VerifiableQuerySet):
    """
    QuerySet for apps.farmers.Farmer.

    Domain queries:
    ─ Geographic    .in_region() / .in_district() / .in_community()
    ─ Demographics  .by_gender() / .women_only() / .with_age()
    ─ Productivity  .with_farm_count() / .with_total_area() / .with_yield_total()
    ─ Traceability  .with_active_batch() / .by_farmer_code()
    ─ Completeness  .with_profile_score() / .incomplete_profiles()
    ─ Registration  .registered_by_officer() / .recently_registered()
    ─ Cooperative   .in_cooperative()
    ─ Analytics     .gender_breakdown() / .region_leaderboard()
    """

    # ── Geographic filtering ──────────────────────────────────────────────────

    def in_region(self, region: str) -> QS:
        return self.filter(region__iexact=region)

    def in_district(self, district: str) -> QS:
        return self.filter(district__iexact=district)

    def in_community(self, community: str) -> QS:
        return self.filter(community__icontains=community)

    def in_regions(self, regions: list[str]) -> QS:
        return self.filter(region__in=regions)

    # ── Demographics ──────────────────────────────────────────────────────────

    def by_gender(self, gender: str) -> QS:
        """gender: 'male' | 'female' | 'other'"""
        return self.filter(gender=gender)

    def women_only(self) -> QS:
        """Filter female farmers — used for women-empowerment impact metrics."""
        return self.filter(gender="female")

    def with_age(self) -> QS:
        """
        Annotates `age_years` (integer) from date_of_birth.
        Records with null DOB get age_years=None (handled with Coalesce).
        """
        from django.db.models.functions import ExtractYear
        return self.annotate(
            age_years=ExpressionWrapper(
                ExtractYear(Now()) - ExtractYear(F("date_of_birth")),
                output_field=IntegerField(),
            )
        )

    # ── Farm & productivity annotations ───────────────────────────────────────

    def with_farm_count(self) -> QS:
        """Annotates `farm_count` (distinct active farms per farmer)."""
        return self.annotate(
            farm_count=Count(
                "farms",
                filter=Q(farms__is_active=True, farms__deleted_at__isnull=True),
                distinct=True,
            )
        )

    def with_total_area(self) -> QS:
        """
        Annotates `total_area_ha` — sum of all active farm plot areas in hectares.
        Coalesce ensures farmers with no farms return 0.0 instead of None.
        """
        return self.annotate(
            total_area_ha=Coalesce(
                Sum(
                    "farms__area_hectares",
                    filter=Q(farms__is_active=True),
                ),
                Value(0.0),
                output_field=FloatField(),
            )
        )

    def with_yield_total(self, season_year: int = None) -> QS:
        """
        Annotates `total_yield_kg` from CropSeason records.
        Optionally scoped to a specific harvest year.
        """
        season_filter = Q(farms__crop_seasons__is_active=True)
        if season_year:
            season_filter &= Q(farms__crop_seasons__harvest_year=season_year)
        return self.annotate(
            total_yield_kg=Coalesce(
                Sum("farms__crop_seasons__actual_yield_kg", filter=season_filter),
                Value(0.0),
                output_field=FloatField(),
            )
        )

    def with_last_visit_date(self) -> QS:
        """Annotates `last_visit` — most recent farm visit date by any officer."""
        return self.annotate(
            last_visit=Max(
                "farms__visits__visited_at",
                filter=Q(farms__visits__is_active=True),
            )
        )

    # ── Traceability ──────────────────────────────────────────────────────────

    def by_farmer_code(self, code: str) -> QS:
        return self.filter(farmer_code__iexact=code)

    def with_active_batch(self) -> QS:
        """
        Filters to farmers that currently have an active batch assigned.
        Uses Subquery to avoid double-counting via join.
        """
        from django.db.models import Exists
        # Lazy import to avoid circular dependency
        active_batch = Subquery(
            self.model._meta.apps.get_model("traceability", "Batch")
            .objects.filter(
                farmer_id=OuterRef("pk"),
                status="active",
                is_active=True,
            )
            .values("id")[:1]
        )
        return self.annotate(has_active_batch=Exists(active_batch)).filter(
            has_active_batch=True
        )

    # ── Registration ──────────────────────────────────────────────────────────

    def registered_by_officer(self, officer_id) -> QS:
        return self.filter(registered_by_id=officer_id)

    def recently_registered(self, days: int = 30) -> QS:
        return self.recent(days=days)

    def pending_approval(self) -> QS:
        """Farmers registered but not yet approved by admin (if approval flow is enabled)."""
        return self.filter(
            verification_status="pending",
            is_active=True,
        )

    # ── Cooperative ───────────────────────────────────────────────────────────

    def in_cooperative(self, cooperative_name: str) -> QS:
        return self.filter(cooperative_name__icontains=cooperative_name)

    # ── Profile completeness ──────────────────────────────────────────────────

    def with_profile_score(self) -> QS:
        """
        Annotates `profile_score` (0–100) using conditional annotation.

        Scoring:
          first_name + last_name   → 10 each
          national_id              → 20
          phone_number             → 15
          profile_photo            → 10
          community                → 10
          date_of_birth            → 10
          has ≥1 farm              → 15  (via Subquery)

        Total possible = 100.
        Uses Case/When to award points for each filled field.
        """
        return self.annotate(
            profile_score=ExpressionWrapper(
                Case(When(first_name__gt="",      then=Value(10)), default=Value(0), output_field=IntegerField()) +
                Case(When(last_name__gt="",       then=Value(10)), default=Value(0), output_field=IntegerField()) +
                Case(When(national_id__gt="",     then=Value(20)), default=Value(0), output_field=IntegerField()) +
                Case(When(phone_number__gt="",    then=Value(15)), default=Value(0), output_field=IntegerField()) +
                Case(When(profile_photo__gt="",   then=Value(10)), default=Value(0), output_field=IntegerField()) +
                Case(When(community__gt="",       then=Value(10)), default=Value(0), output_field=IntegerField()) +
                Case(When(date_of_birth__isnull=False, then=Value(10)), default=Value(0), output_field=IntegerField()),
                output_field=IntegerField(),
            )
        )

    def incomplete_profiles(self, threshold: int = 60) -> QS:
        """Farmers with profile_score below threshold. Triggers staff follow-up tasks."""
        return self.with_profile_score().filter(profile_score__lt=threshold)

    # ── Analytics aggregations ────────────────────────────────────────────────

    def gender_breakdown(self) -> list[dict]:
        """
        Returns gender distribution for impact dashboard.
        → [{"gender": "female", "count": 340, "pct": 48.5}, ...]
        """
        total = self.count()
        rows  = (
            self.values("gender")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        return [
            {**r, "pct": round(r["count"] / total * 100, 1) if total else 0.0}
            for r in rows
        ]

    def region_leaderboard(self) -> list[dict]:
        """
        Ranked regions by farmer count + verification rate.
        → [{"region": "Ashanti", "total": 450, "verified": 380, "verification_rate_pct": 84.4}, ...]
        """
        return list(
            self.values("region")
            .annotate(
                total    = Count("id"),
                verified = Count("id", filter=IS_VERIFIED),
                female   = Count("id", filter=Q(gender="female")),
            )
            .annotate(
                verification_rate_pct=ExpressionWrapper(
                    F("verified") * 100.0 / Greatest(F("total"), Value(1)),
                    output_field=FloatField(),
                ),
                female_pct=ExpressionWrapper(
                    F("female") * 100.0 / Greatest(F("total"), Value(1)),
                    output_field=FloatField(),
                ),
            )
            .order_by("-total")
        )

    def education_breakdown(self) -> list[dict]:
        return list(
            self.values("education_level")
            .annotate(count=Count("id"))
            .order_by("-count")
        )


# =============================================================================
# 6.  FARM QUERYSET
# =============================================================================

class FarmQuerySet(BaseQuerySet):
    """
    QuerySet for apps.farmers.Farm.

    Domain queries:
    ─ Location     .with_coordinates() / .missing_coordinates() / .near()
    ─ Area         .larger_than() / .smaller_than() / .with_area_category()
    ─ Crop         .by_crop_type() / .with_current_season()
    ─ Officer      .surveyed_by() / .unsurveyed()
    ─ Aggregation  .total_area() / .area_by_region() / .area_by_officer()
    """

    def with_coordinates(self) -> QS:
        return self.filter(latitude__isnull=False, longitude__isnull=False)

    def missing_coordinates(self) -> QS:
        return self.filter(Q(latitude__isnull=True) | Q(longitude__isnull=True))

    def near(self, lat: float, lon: float, radius_km: float = 10) -> QS:
        """
        Bounding-box proximity filter.
        Filters farms within radius_km of (lat, lon).

        For production accuracy, replace with PostGIS ST_DWithin.
        This approach (Haversine bounding box) works for SQLite/PostgreSQL
        without the PostGIS extension.
        """
        deg_per_km = 1 / 111.0
        lat_delta  = radius_km * deg_per_km
        lon_delta  = radius_km * deg_per_km / max(
            math.cos(math.radians(lat)), 1e-6
        )
        return self.filter(
            latitude__range =(lat - lat_delta, lat + lat_delta),
            longitude__range=(lon - lon_delta, lon + lon_delta),
        )

    def with_distance_to(self, lat: float, lon: float) -> QS:
        """
        Annotates `distance_km` — approximate distance to a reference point.
        Uses a flat-earth approximation valid for small distances (< 300 km).
        For full Haversine accuracy use PostGIS.
        """
        return self.with_coordinates().annotate(
            distance_km=ExpressionWrapper(
                (
                    (F("latitude")  - Value(lat))  * Value(111.0)
                ) ** 2
                + (
                    (F("longitude") - Value(lon))  * Value(111.0)
                    * Value(math.cos(math.radians(lat)))
                ) ** 2,
                output_field=FloatField(),
            )
        ).order_by("distance_km")

    def larger_than(self, hectares: float) -> QS:
        return self.filter(area_hectares__gte=hectares)

    def smaller_than(self, hectares: float) -> QS:
        return self.filter(area_hectares__lte=hectares)

    def with_area_category(self) -> QS:
        """
        Annotates `area_category`:
          smallholder  = < 2 ha
          medium       = 2–5 ha
          large        = > 5 ha
        """
        return self.annotate(
            area_category=Case(
                When(area_hectares__lt=2,    then=Value("smallholder")),
                When(area_hectares__lte=5,   then=Value("medium")),
                default=Value("large"),
                output_field=models.CharField(),
            )
        )

    def by_crop_type(self, crop_type: str) -> QS:
        return self.filter(current_crop_type__iexact=crop_type)

    def surveyed_by(self, officer_id) -> QS:
        return self.filter(
            visits__field_officer_id=officer_id,
            visits__is_active=True,
        ).distinct()

    def unsurveyed(self) -> QS:
        """Farms that have never had a field officer visit logged."""
        return self.filter(visits__isnull=True)

    def with_current_season(self) -> QS:
        """Prefetches the current active crop season for each farm."""
        return self.prefetch_related(
            models.Prefetch(
                "crop_seasons",
                queryset=self.model._meta.apps.get_model(
                    "farmers", "CropSeason"
                ).objects.filter(is_active=True).order_by("-created_at"),
                to_attr="active_seasons",
            )
        )

    def with_visit_count(self) -> QS:
        return self.annotate(
            visit_count=Count(
                "visits",
                filter=Q(visits__is_active=True),
                distinct=True,
            )
        )

    # ── Aggregation helpers ───────────────────────────────────────────────────

    def total_area(self) -> float:
        return self.aggregate(total=Coalesce(Sum("area_hectares"), Value(0.0)))["total"]

    def area_by_region(self) -> list[dict]:
        return list(
            self.values("farmer__region")
            .annotate(
                farm_count  = Count("id"),
                total_area  = Sum("area_hectares"),
                avg_area    = Avg("area_hectares"),
                max_area    = Max("area_hectares"),
            )
            .order_by("-total_area")
        )

    def area_by_officer(self) -> list[dict]:
        return list(
            self.values(
                officer_id   = F("visits__field_officer_id"),
                officer_name = F("visits__field_officer__user__first_name"),
            )
            .annotate(
                farms_surveyed = Count("id", distinct=True),
                total_area     = Sum("area_hectares"),
            )
            .filter(officer_id__isnull=False)
            .order_by("-farms_surveyed")
        )


# =============================================================================
# 7.  CROP SEASON QUERYSET
# =============================================================================

class CropSeasonQuerySet(BaseQuerySet):
    """
    QuerySet for apps.farmers.CropSeason.

    Covers seasonal crop data: planting, fertilizer, harvest tracking.
    """

    def for_year(self, year: int) -> QS:
        return self.filter(harvest_year=year)

    def current_year(self) -> QS:
        return self.for_year(timezone.now().year)

    def with_organic_fertilizer(self) -> QS:
        return self.filter(fertilizer_type="organic")

    def with_inorganic_fertilizer(self) -> QS:
        return self.filter(fertilizer_type="inorganic")

    def ready_for_harvest(self) -> QS:
        """Seasons where expected harvest date has arrived or passed."""
        return self.filter(expected_harvest_date__lte=timezone.now().date())

    def overdue_harvest(self) -> QS:
        """Seasons past expected harvest with no actual yield recorded."""
        return self.filter(
            expected_harvest_date__lt=timezone.now().date(),
            actual_yield_kg__isnull=True,
        )

    def with_yield_variance(self) -> QS:
        """
        Annotates `yield_variance_pct` — how far actual yield deviated
        from expected yield as a percentage.
        """
        return self.filter(
            expected_yield_kg__isnull=False,
            actual_yield_kg__isnull=False,
            expected_yield_kg__gt=0,
        ).annotate(
            yield_variance_pct=ExpressionWrapper(
                (F("actual_yield_kg") - F("expected_yield_kg"))
                * 100.0
                / F("expected_yield_kg"),
                output_field=FloatField(),
            )
        )

    def yield_summary_by_crop(self) -> list[dict]:
        return list(
            self.filter(actual_yield_kg__isnull=False)
            .values("crop_variety")
            .annotate(
                total_yield_kg  = Sum("actual_yield_kg"),
                avg_yield_kg    = Avg("actual_yield_kg"),
                max_yield_kg    = Max("actual_yield_kg"),
                season_count    = Count("id"),
                female_farmers  = Count(
                    "farm__farmer__id",
                    filter=Q(farm__farmer__gender="female"),
                    distinct=True,
                ),
            )
            .order_by("-total_yield_kg")
        )

    def seed_source_breakdown(self) -> list[dict]:
        return list(
            self.values("seed_source")
            .annotate(count=Count("id"))
            .order_by("-count")
        )


# =============================================================================
# 8.  FIELD OFFICER QUERYSET
# =============================================================================

class FieldOfficerQuerySet(VerifiableQuerySet):
    """
    QuerySet for apps.field_officers.FieldOfficer (or apps.staffs.StaffMember).

    Performance KPIs, assignment tracking, regional distribution.
    """

    def in_region(self, region: str) -> QS:
        return self.filter(assigned_region__iexact=region)

    def active_officers(self) -> QS:
        return self.filter(
            is_active=True,
            employment_status="active",
            deleted_at__isnull=True,
        )

    def with_farmer_count(self) -> QS:
        """Annotates `farmer_count` — total farmers registered by this officer."""
        return self.annotate(
            farmer_count=Count(
                "registered_farmers",
                filter=Q(registered_farmers__is_active=True),
                distinct=True,
            )
        )

    def with_verified_farmer_count(self) -> QS:
        return self.annotate(
            verified_farmer_count=Count(
                "registered_farmers",
                filter=Q(
                    registered_farmers__is_active=True,
                    registered_farmers__verification_status="verified",
                ),
                distinct=True,
            )
        )

    def with_farm_visit_count(self) -> QS:
        return self.annotate(
            visit_count=Count(
                "farm_visits",
                filter=Q(farm_visits__is_active=True),
                distinct=True,
            )
        )

    def with_produce_collected_kg(self) -> QS:
        """
        Annotates `produce_collected_kg` — total raw produce (kg)
        collected by this officer across all batches.
        """
        return self.annotate(
            produce_collected_kg=Coalesce(
                Sum(
                    "batches__weight_kg",
                    filter=Q(batches__is_active=True),
                ),
                Value(0.0),
                output_field=FloatField(),
            )
        )

    def with_performance_score(self) -> QS:
        """
        Composite KPI score (0–100) derived from:
          - farmer_count      (weight 30 %)
          - visit_count       (weight 30 %)
          - verified_rate     (weight 40 %)

        Normalises against the max values in the current queryset
        using a Subquery so each officer's score is relative to peers.
        """
        from django.db.models import FloatField

        qs_with_counts = (
            self
            .with_farmer_count()
            .with_farm_visit_count()
            .with_verified_farmer_count()
        )

        max_farmers = qs_with_counts.aggregate(m=Max("farmer_count"))["m"] or 1
        max_visits  = qs_with_counts.aggregate(m=Max("visit_count"))["m"] or 1

        return qs_with_counts.annotate(
            performance_score=ExpressionWrapper(
                (F("farmer_count")         / Value(float(max_farmers))) * Value(30.0)
                + (F("visit_count")        / Value(float(max_visits)))  * Value(30.0)
                + ExpressionWrapper(
                    F("verified_farmer_count") * 100.0
                    / Greatest(F("farmer_count"), Value(1)),
                    output_field=FloatField(),
                ) * Value(0.4),
                output_field=FloatField(),
            )
        )

    def leaderboard(self, top_n: int = 10) -> list[dict]:
        """Top-N field officers ranked by farmer_count + visit_count."""
        return list(
            self.active_officers()
            .with_farmer_count()
            .with_farm_visit_count()
            .with_produce_collected_kg()
            .values(
                "id", "officer_code",
                first_name = F("user__first_name"),
                last_name  = F("user__last_name"),
                region     = F("assigned_region"),
            )
            .annotate(
                farmer_count         = F("farmer_count"),
                visit_count          = F("visit_count"),
                produce_collected_kg = F("produce_collected_kg"),
            )
            .order_by("-farmer_count", "-visit_count")[:top_n]
        )


# =============================================================================
# 9.  TRACEABILITY QUERYSET
# =============================================================================

class TraceabilityQuerySet(BaseQuerySet):
    """
    QuerySet for apps.traceability.TraceRecord.

    Covers the full farm-to-buyer chain: batch lookup, QR resolution,
    export filtering, public-safe record scoping.
    """

    def active_chain(self) -> QS:
        """Records in the active supply chain (not cancelled/recalled)."""
        return self.filter(
            status__in=["active", "exported", "in_transit", "delivered"],
            is_active=True,
        )

    def by_trace_code(self, code: str) -> QS:
        return self.filter(trace_code__iexact=code)

    def by_farmer_code(self, code: str) -> QS:
        return self.filter(farmer__farmer_code__iexact=code)

    def by_batch_code(self, code: str) -> QS:
        return self.filter(
            Q(farmer_batch_code__iexact=code)
            | Q(warehouse_batch_code__iexact=code)
            | Q(product_batch_code__iexact=code)
        )

    def by_product(self, product_id) -> QS:
        return self.filter(product_id=product_id)

    def exported(self) -> QS:
        return self.filter(status="exported")

    def for_public_scan(self) -> QS:
        """
        Returns only the fields safe to expose to a buyer scanning a QR code.
        Excludes: national_id, phone_number, exact GPS, internal notes.
        Scopes to active + verified chain only.
        """
        return (
            self.active_chain()
            .filter(farmer__verification_status="verified")
            .select_related(
                "farmer", "farm", "product",
                "warehouse_intake", "field_officer",
            )
            .only(
                "trace_code", "farmer_batch_code", "product_batch_code",
                "status", "export_destination_country", "harvest_date",
                "created_at",
                "farmer__farmer_code", "farmer__first_name", "farmer__region",
                "farmer__district", "farmer__community",
                "farm__farm_code", "farm__area_hectares",
                "product__name", "product__category",
            )
        )

    def with_full_chain(self) -> QS:
        """
        Eager-loads the full traceability chain for admin/internal use.
        Suitable for the admin detail view and CSV export.
        """
        return self.select_related(
            "farmer", "farm", "product", "field_officer",
            "warehouse_intake", "warehouse_intake__warehouse",
        ).prefetch_related(
            "processing_steps",
            "processing_steps__operator",
            "certifications",
        )

    def destination_summary(self) -> list[dict]:
        """Export volumes by destination country."""
        return list(
            self.exported()
            .values("export_destination_country")
            .annotate(
                shipments       = Count("id"),
                total_weight_kg = Coalesce(Sum("weight_kg"), Value(0.0), output_field=FloatField()),
                farmers_count   = Count("farmer", distinct=True),
            )
            .order_by("-shipments")
        )

    def chain_for_qr(self, qr_code: str) -> Optional[QS]:
        """
        Resolve a QR code to its complete traceability record.
        QR codes may encode trace_code, farmer_batch_code, or product_batch_code.
        Returns a queryset (not an instance) so callers can choose serializer.
        """
        return self.filter(
            Q(trace_code__iexact=qr_code)
            | Q(farmer_batch_code__iexact=qr_code)
            | Q(product_batch_code__iexact=qr_code)
        ).select_related("farmer", "farm", "product")

    def status_pipeline(self) -> dict:
        """
        Single-query breakdown of records by status.
        Used by the admin pipeline dashboard.
        """
        statuses = [
            "active", "in_transit", "at_warehouse", "processing",
            "exported", "delivered", "recalled", "cancelled",
        ]
        return self.aggregate(**{
            s: Count("id", filter=Q(status=s)) for s in statuses
        })


# =============================================================================
# 10.  BATCH QUERYSET
# =============================================================================

class BatchQuerySet(BaseQuerySet):
    """
    QuerySet for apps.traceability.Batch (farmer batch / warehouse batch).

    Covers QR assignment, weight aggregation, officer tracking.
    """

    def farmer_batches(self) -> QS:
        return self.filter(batch_type="farmer")

    def warehouse_batches(self) -> QS:
        return self.filter(batch_type="warehouse")

    def product_batches(self) -> QS:
        return self.filter(batch_type="product")

    def active_batches(self) -> QS:
        return self.filter(status="active", is_active=True)

    def by_officer(self, officer_id) -> QS:
        return self.filter(collected_by_id=officer_id)

    def by_farmer(self, farmer_id) -> QS:
        return self.filter(farmer_id=farmer_id)

    def by_code(self, code: str) -> QS:
        return self.filter(batch_code__iexact=code)

    def with_total_weight(self) -> QS:
        return self.annotate(
            total_weight_kg=Coalesce(
                Sum("weight_kg", filter=Q(is_active=True)),
                Value(0.0),
                output_field=FloatField(),
            )
        )

    def weight_by_region(self) -> list[dict]:
        """Total raw produce (kg) collected per region."""
        return list(
            self.farmer_batches()
            .values(region=F("farmer__region"))
            .annotate(
                total_kg     = Coalesce(Sum("weight_kg"), Value(0.0), output_field=FloatField()),
                batch_count  = Count("id"),
                farmer_count = Count("farmer", distinct=True),
            )
            .order_by("-total_kg")
        )

    def weight_by_officer(self) -> list[dict]:
        """Officer dashboard — produce collected per field officer."""
        return list(
            self.farmer_batches()
            .values(
                officer_id   = F("collected_by_id"),
                officer_code = F("collected_by__officer_code"),
                first_name   = F("collected_by__user__first_name"),
                last_name    = F("collected_by__user__last_name"),
            )
            .annotate(
                total_kg    = Coalesce(Sum("weight_kg"), Value(0.0), output_field=FloatField()),
                batch_count = Count("id"),
            )
            .filter(officer_id__isnull=False)
            .order_by("-total_kg")
        )


# =============================================================================
# 11.  ORDER QUERYSET
# =============================================================================

class OrderQuerySet(BaseQuerySet):
    """
    QuerySet for apps.orders.Order.

    Status workflow, value aggregation, buyer scoping.
    """

    # ── Status ────────────────────────────────────────────────────────────────

    def pending(self) -> QS:
        return self.filter(status="pending")

    def confirmed(self) -> QS:
        return self.filter(status="confirmed")

    def dispatched(self) -> QS:
        return self.filter(status="dispatched")

    def delivered(self) -> QS:
        return self.filter(status="delivered")

    def cancelled(self) -> QS:
        return self.filter(status="cancelled")

    def in_progress(self) -> QS:
        """Orders that are active (not yet delivered or cancelled)."""
        return self.filter(
            status__in=["pending", "confirmed", "processing", "dispatched"]
        )

    def overdue(self) -> QS:
        """Confirmed orders past expected delivery date."""
        return self.confirmed().filter(
            expected_delivery_date__lt=timezone.now().date()
        )

    # ── Value ─────────────────────────────────────────────────────────────────

    def high_value(self, threshold: float = 10_000.0) -> QS:
        """Orders above a certain total value (in GHS by default)."""
        return self.filter(total_value__gte=threshold)

    def with_total_value(self) -> QS:
        """Annotates `line_total` — sum of (quantity × unit_price) across order items."""
        return self.annotate(
            line_total=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F("items__quantity") * F("items__unit_price"),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    ),
                    filter=Q(items__is_active=True),
                ),
                Value(0),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )

    # ── Buyer scoping ─────────────────────────────────────────────────────────

    def for_buyer(self, buyer_id) -> QS:
        return self.filter(buyer_id=buyer_id)

    def with_buyer_name(self) -> QS:
        return self.select_related("buyer").annotate(
            buyer_company=F("buyer__company_name"),
            buyer_country=F("buyer__country"),
        )

    # ── Revenue analytics ─────────────────────────────────────────────────────

    def revenue_summary(self) -> dict:
        """
        Full revenue picture — single aggregation query.
        """
        return self.aggregate(
            total_orders     = Count("id"),
            total_revenue    = Coalesce(Sum("total_value"), Value(0)),
            avg_order_value  = Avg("total_value"),
            max_order_value  = Max("total_value"),
            delivered_orders = Count("id", filter=Q(status="delivered")),
            pending_orders   = Count("id", filter=Q(status="pending")),
            cancelled_orders = Count("id", filter=Q(status="cancelled")),
        )

    def revenue_by_product(self) -> list[dict]:
        return list(
            self.delivered()
            .values(product_name=F("items__product__name"))
            .annotate(
                total_kg      = Sum("items__quantity"),
                total_revenue = Sum(
                    ExpressionWrapper(
                        F("items__quantity") * F("items__unit_price"),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                order_count = Count("id", distinct=True),
            )
            .filter(product_name__isnull=False)
            .order_by("-total_revenue")
        )

    def revenue_by_country(self) -> list[dict]:
        return list(
            self.delivered()
            .values(country=F("destination_country"))
            .annotate(
                total_revenue = Coalesce(Sum("total_value"), Value(0)),
                order_count   = Count("id"),
            )
            .order_by("-total_revenue")
        )


# =============================================================================
# 12.  PAYMENT QUERYSET
# =============================================================================

class PaymentQuerySet(BaseQuerySet):
    """
    QuerySet for apps.payments.Payment.

    MTD/YTD totals, mobile money filtering, status pipeline.
    """

    def completed(self) -> QS:
        return self.filter(status="completed")

    def pending(self) -> QS:
        return self.filter(status="pending")

    def failed(self) -> QS:
        return self.filter(status="failed")

    def by_channel(self, channel: str) -> QS:
        """channel: 'mobile_money' | 'card' | 'bank_transfer'"""
        return self.filter(payment_channel=channel)

    def mobile_money(self) -> QS:
        return self.by_channel("mobile_money")

    def for_order(self, order_id) -> QS:
        return self.filter(order_id=order_id)

    def for_buyer(self, buyer_id) -> QS:
        return self.filter(order__buyer_id=buyer_id)

    def with_order_info(self) -> QS:
        return self.select_related("order", "order__buyer")

    # ── Financial aggregations ────────────────────────────────────────────────

    def total_received(self) -> float:
        return self.completed().aggregate(
            total=Coalesce(Sum("amount"), Value(0.0))
        )["total"]

    def mtd_revenue(self) -> float:
        return self.completed().mtd("payment_date").aggregate(
            total=Coalesce(Sum("amount"), Value(0.0))
        )["total"]

    def ytd_revenue(self) -> float:
        return self.completed().ytd("payment_date").aggregate(
            total=Coalesce(Sum("amount"), Value(0.0))
        )["total"]

    def revenue_by_month(self, months: int = 12) -> list[dict]:
        """Monthly completed payment totals for the chart on the admin dashboard."""
        return list(
            self.completed()
            .annotate(month=TruncMonth("payment_date"))
            .values("month")
            .annotate(
                total      = Coalesce(Sum("amount"), Value(0.0), output_field=FloatField()),
                tx_count   = Count("id"),
                avg_amount = Avg("amount"),
            )
            .order_by("-month")[:months]
        )

    def channel_breakdown(self) -> list[dict]:
        """Payment channel distribution — used for finance reporting."""
        total = self.completed().aggregate(t=Coalesce(Sum("amount"), Value(0.0)))["t"] or 1
        rows  = list(
            self.completed()
            .values("payment_channel")
            .annotate(
                total_amount = Coalesce(Sum("amount"), Value(0.0), output_field=FloatField()),
                tx_count     = Count("id"),
            )
            .order_by("-total_amount")
        )
        return [{**r, "pct": round(r["total_amount"] / total * 100, 1)} for r in rows]

    def status_pipeline(self) -> dict:
        return self.aggregate(
            total     = Count("id"),
            completed = Count("id", filter=Q(status="completed")),
            pending   = Count("id", filter=Q(status="pending")),
            failed    = Count("id", filter=Q(status="failed")),
            refunded  = Count("id", filter=Q(status="refunded")),
        )


# =============================================================================
# 13.  PRODUCT QUERYSET
# =============================================================================

class ProductQuerySet(BaseQuerySet):
    """
    QuerySet for apps.products.Product.

    Marketplace filtering, category breakdown, availability.
    """

    def available(self) -> QS:
        return self.filter(is_available=True, stock_kg__gt=0)

    def by_category(self, category: str) -> QS:
        return self.filter(category__iexact=category)

    def by_origin_country(self, country: str) -> QS:
        return self.filter(origin_country__iexact=country)

    def in_price_range(self, min_price: float, max_price: float) -> QS:
        return self.filter(price_per_kg__range=(min_price, max_price))

    def certified(self) -> QS:
        """Products with at least one valid certification."""
        return self.filter(
            certifications__is_active=True,
            certifications__status="approved",
        ).distinct()

    def with_stock_status(self) -> QS:
        """
        Annotates `stock_status`:
          'out_of_stock'  = 0 kg
          'low_stock'     = < 100 kg
          'in_stock'      = ≥ 100 kg
        """
        return self.annotate(
            stock_status=Case(
                When(stock_kg__lte=0,     then=Value("out_of_stock")),
                When(stock_kg__lt=100,    then=Value("low_stock")),
                default=Value("in_stock"),
                output_field=models.CharField(),
            )
        )

    def with_review_stats(self) -> QS:
        """Annotates `avg_rating` and `review_count` from the reviews app."""
        return self.annotate(
            avg_rating   = Coalesce(
                Avg("reviews__rating", filter=Q(reviews__is_active=True)),
                Value(0.0),
                output_field=FloatField(),
            ),
            review_count = Count(
                "reviews",
                filter=Q(reviews__is_active=True),
                distinct=True,
            ),
        )

    def marketplace_listing(self) -> QS:
        """
        Full optimised queryset for the buyer marketplace page.
        One query with all data needed to render product cards.
        """
        return (
            self.available()
            .with_stock_status()
            .with_review_stats()
            .select_related("origin_farmer", "origin_farm")
            .prefetch_related("certifications", "photos")
            .order_by("-created_at")
        )

    def category_summary(self) -> list[dict]:
        return list(
            self.values("category")
            .annotate(
                count       = Count("id"),
                total_stock = Coalesce(Sum("stock_kg"), Value(0.0), output_field=FloatField()),
                avg_price   = Avg("price_per_kg"),
            )
            .order_by("-count")
        )


# =============================================================================
# 14.  REVIEW QUERYSET
# =============================================================================

class ReviewQuerySet(BaseQuerySet):
    """
    QuerySet for apps.reviews.Review (buyer feedback on products/orders).
    """

    def for_product(self, product_id) -> QS:
        return self.filter(product_id=product_id)

    def for_farmer(self, farmer_id) -> QS:
        return self.filter(product__origin_farmer_id=farmer_id)

    def for_buyer(self, buyer_id) -> QS:
        return self.filter(buyer_id=buyer_id)

    def by_rating(self, stars: int) -> QS:
        return self.filter(rating=stars)

    def high_rated(self, threshold: int = 4) -> QS:
        return self.filter(rating__gte=threshold)

    def low_rated(self, threshold: int = 2) -> QS:
        return self.filter(rating__lte=threshold)

    def with_buyer_info(self) -> QS:
        return self.select_related("buyer", "product")

    def rating_summary(self) -> dict:
        """Single-query rating breakdown."""
        result = self.aggregate(
            total         = Count("id"),
            avg_rating    = Coalesce(Avg("rating"), Value(0.0), output_field=FloatField()),
            five_star     = Count("id", filter=Q(rating=5)),
            four_star     = Count("id", filter=Q(rating=4)),
            three_star    = Count("id", filter=Q(rating=3)),
            two_star      = Count("id", filter=Q(rating=2)),
            one_star      = Count("id", filter=Q(rating=1)),
            avg_product   = Coalesce(Avg("product_satisfaction"), Value(0.0), output_field=FloatField()),
            avg_delivery  = Coalesce(Avg("delivery_satisfaction"), Value(0.0), output_field=FloatField()),
        )
        total = result["total"] or 1
        result["five_star_pct"]  = round(result["five_star"]  / total * 100, 1)
        result["four_star_pct"]  = round(result["four_star"]  / total * 100, 1)
        result["three_star_pct"] = round(result["three_star"] / total * 100, 1)
        result["two_star_pct"]   = round(result["two_star"]   / total * 100, 1)
        result["one_star_pct"]   = round(result["one_star"]   / total * 100, 1)
        return result

    def monthly_rating_trend(self, months: int = 6) -> list[dict]:
        return list(
            self.annotate(month=TruncMonth("created_at"))
            .values("month")
            .annotate(
                avg_rating   = Avg("rating"),
                review_count = Count("id"),
            )
            .order_by("-month")[:months]
        )


# =============================================================================
# 15.  NOTIFICATION QUERYSET
# =============================================================================

class NotificationQuerySet(BaseQuerySet):
    """
    QuerySet for apps.notifications.Notification.
    """

    def for_user(self, user_id) -> QS:
        return self.filter(recipient_id=user_id)

    def unread(self) -> QS:
        return self.filter(is_read=False)

    def read(self) -> QS:
        return self.filter(is_read=True)

    def by_type(self, notification_type: str) -> QS:
        return self.filter(notification_type=notification_type)

    def urgent(self) -> QS:
        return self.filter(priority="high")

    def for_user_unread(self, user_id) -> QS:
        return self.for_user(user_id).unread()

    def mark_all_read(self, user_id) -> int:
        """Bulk-marks all unread notifications for a user as read."""
        return self.for_user_unread(user_id).update(
            is_read=True,
            read_at=timezone.now(),
        )

    def unread_count(self, user_id) -> int:
        return self.for_user_unread(user_id).count()

    def type_breakdown(self, user_id) -> list[dict]:
        return list(
            self.for_user(user_id)
            .values("notification_type")
            .annotate(
                total  = Count("id"),
                unread = Count("id", filter=Q(is_read=False)),
            )
            .order_by("-total")
        )


# =============================================================================
# 16.  IMPACT QUERYSET
# =============================================================================

class ImpactQuerySet(BaseQuerySet):
    """
    QuerySet for apps.impact.ImpactMetric (or computed from Farmer/Farm/Order).

    Women empowerment %, CO2 savings, regional KPIs for the public dashboard.
    """

    def women_empowerment_pct(self) -> float:
        """Percentage of registered farmers who are female — global KPI."""
        # This method is designed to be called on a FarmerQuerySet instance
        # but lives here for reuse by ImpactMetric models.
        total  = self.count()
        female = self.filter(gender="female").count()
        return round(female / total * 100, 1) if total else 0.0

    def co2_savings_by_region(self) -> list[dict]:
        """Sum of logged CO2 savings per region — used on the impact dashboard."""
        return list(
            self.values("region")
            .annotate(
                total_co2_saved_kg = Coalesce(
                    Sum("co2_saved_kg"),
                    Value(0.0),
                    output_field=FloatField(),
                ),
                contributing_farms = Count("farm", distinct=True),
            )
            .order_by("-total_co2_saved_kg")
        )

    def impact_summary(self) -> dict:
        return self.aggregate(
            total_farmers          = Count("id"),
            total_farms            = Count("farm", distinct=True),
            total_area_ha          = Coalesce(Sum("farm__area_hectares"), Value(0.0), output_field=FloatField()),
            female_farmers         = Count("id", filter=Q(gender="female")),
            verified_farmers       = Count("id", filter=IS_VERIFIED),
            total_yield_kg         = Coalesce(Sum("farm__crop_seasons__actual_yield_kg"), Value(0.0), output_field=FloatField()),
        )


# =============================================================================
# 17.  REPORT QUERYSET
# =============================================================================

class ReportQuerySet(BaseQuerySet):
    """
    QuerySet for apps.reports.Report (scheduled / on-demand report generation).
    """

    def pending_generation(self) -> QS:
        return self.filter(status="queued")

    def generating(self) -> QS:
        return self.filter(status="generating")

    def ready(self) -> QS:
        return self.filter(status="ready")

    def failed_generation(self) -> QS:
        return self.filter(status="failed")

    def for_user(self, user_id) -> QS:
        return self.filter(requested_by_id=user_id)

    def by_type(self, report_type: str) -> QS:
        return self.filter(report_type=report_type)

    def stale(self, older_than_days: int = 7) -> QS:
        """Ready reports older than N days — candidates for cleanup."""
        cutoff = timezone.now() - timezone.timedelta(days=older_than_days)
        return self.ready().filter(created_at__lte=cutoff)

    def generation_stats(self) -> dict:
        return self.aggregate(
            total      = Count("id"),
            queued     = Count("id", filter=Q(status="queued")),
            generating = Count("id", filter=Q(status="generating")),
            ready      = Count("id", filter=Q(status="ready")),
            failed     = Count("id", filter=Q(status="failed")),
        )


# =============================================================================
# 17b.  CART QUERYSET  (missing — added)
# =============================================================================

class CartQuerySet(BaseQuerySet):
    """
    QuerySet for apps.buyers.Cart.

    Covers active cart lookup, abandonment detection, and expiry cleanup.
    """

    def active(self) -> QS:
        return self.filter(status="active", is_active=True, deleted_at__isnull=True)

    def abandoned(self) -> QS:
        return self.filter(status="abandoned")

    def for_buyer(self, buyer_id) -> QS:
        return self.filter(buyer_id=buyer_id)

    def active_for_buyer(self, buyer_id) -> QS:
        """Return the single active cart for a buyer, or None."""
        return self.active().filter(buyer_id=buyer_id).first()

    def expired(self) -> QS:
        """Active carts whose expires_at timestamp has passed."""
        return self.filter(status="active", expires_at__lt=timezone.now())

    def mark_expired(self) -> int:
        """Bulk-abandon expired carts. Safe to call from a Celery beat task."""
        return self.expired().update(status="abandoned")

    def with_item_count(self) -> QS:
        return self.annotate(
            item_count=Count("items", filter=Q(items__is_active=True), distinct=True)
        )

    def with_total_value(self) -> QS:
        from django.db.models import DecimalField
        return self.annotate(
            cart_total=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F("items__quantity") * F("items__unit_price"),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    ),
                    filter=Q(items__is_active=True),
                ),
                Value(0),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )

    def cart_summary(self) -> dict:
        return self.aggregate(
            total     = Count("id"),
            active    = Count("id", filter=Q(status="active")),
            abandoned = Count("id", filter=Q(status="abandoned")),
        )


# =============================================================================
# 17c.  PRODUCT REVIEW QUERYSET  (alias for ReviewQuerySet — added for import compatibility)
# =============================================================================

class ProductReviewQuerySet(ReviewQuerySet):
    """
    Alias QuerySet for apps.buyers.ProductReview.

    Identical to ReviewQuerySet — exists so that the buyers app can import
    `ProductReviewQuerySet` from apps.core.querysets while still sharing
    all the rating/sentiment query methods.

    Extra method: `for_order()` — scope reviews by order rather than product.
    """

    def for_order(self, order_id) -> QS:
        return self.filter(order_id=order_id)

    def verified_purchase_only(self) -> QS:
        """Only reviews attached to a confirmed/delivered order."""
        return self.filter(order__status__in=["confirmed", "delivered"])

    def featured(self) -> QS:
        """High-rated reviews flagged for public display."""
        return self.filter(is_featured=True, rating__gte=4)


# =============================================================================
# 17d.  COUPON QUERYSET  (missing — added)
# =============================================================================

class CouponQuerySet(BaseQuerySet):
    """
    QuerySet for apps.buyers.Coupon.

    Covers validity checks, usage tracking, and discount analytics.
    """

    def active_coupons(self) -> QS:
        now = timezone.now()
        return self.filter(
            is_active=True,
            valid_from__lte=now,
            valid_until__gte=now,
            deleted_at__isnull=True,
        )

    def by_code(self, code: str) -> QS:
        return self.filter(code__iexact=code.strip())

    def get_valid(self, code: str):
        """Return a single valid, non-exhausted coupon or None."""
        return (
            self.active_coupons()
            .filter(code__iexact=code.strip())
            .exclude(max_uses__isnull=False, times_used__gte=F("max_uses"))
            .first()
        )

    def expired(self) -> QS:
        return self.filter(valid_until__lt=timezone.now())

    def not_yet_started(self) -> QS:
        return self.filter(valid_from__gt=timezone.now())

    def exhausted(self) -> QS:
        """Coupons that have reached their maximum usage limit."""
        return self.filter(
            max_uses__isnull=False,
            times_used__gte=F("max_uses"),
        )

    def by_discount_type(self, discount_type: str) -> QS:
        """discount_type: 'percentage' | 'fixed'"""
        return self.filter(discount_type=discount_type)

    def usage_summary(self) -> dict:
        return self.aggregate(
            total     = Count("id"),
            active    = Count("id", filter=Q(is_active=True)),
            expired   = Count("id", filter=Q(valid_until__lt=timezone.now())),
            exhausted = Count("id", filter=Q(times_used__gte=F("max_uses"))),
            total_uses = Coalesce(Sum("times_used"), Value(0)),
        )


# =============================================================================
# 17e.  WAREHOUSE MANAGER QUERYSET  (missing — added)
# =============================================================================

class WarehouseManagerQuerySet(VerifiableQuerySet):
    """
    QuerySet for apps.staff.WarehouseManager.

    Covers warehouse assignment, employment status, and intake capacity.
    """

    def active_managers(self) -> QS:
        return self.filter(
            is_active=True,
            employment_status="active",
            deleted_at__isnull=True,
        )

    def by_warehouse(self, warehouse_name: str) -> QS:
        return self.filter(warehouse_name__icontains=warehouse_name)

    def in_region(self, region: str) -> QS:
        return self.filter(assigned_region__iexact=region)

    def pending_approval(self) -> QS:
        return self.filter(verification_status="pending", is_active=True)

    def with_intake_count(self) -> QS:
        """Annotates `intake_count` — total warehouse intakes handled."""
        return self.annotate(
            intake_count=Count(
                "warehouse_intakes",
                filter=Q(warehouse_intakes__is_active=True),
                distinct=True,
            )
        )

    def with_total_weight_received(self) -> QS:
        """Annotates `total_weight_kg` — cumulative produce received."""
        return self.annotate(
            total_weight_kg=Coalesce(
                Sum(
                    "warehouse_intakes__net_weight_kg",
                    filter=Q(warehouse_intakes__is_active=True),
                ),
                Value(0.0),
                output_field=FloatField(),
            )
        )

    def performance_summary(self) -> list[dict]:
        """Ranked warehouse managers by intake volume."""
        return list(
            self.active_managers()
            .with_intake_count()
            .with_total_weight_received()
            .values(
                "id",
                "officer_code",
                "warehouse_name",
                first_name=F("user__first_name"),
                last_name=F("user__last_name"),
            )
            .annotate(
                intake_count     = F("intake_count"),
                total_weight_kg  = F("total_weight_kg"),
            )
            .order_by("-total_weight_kg")
        )


# =============================================================================
# 18–22.  REUSABLE STANDALONE HELPERS
#         Kept for backward compatibility with apps that imported these as
#         functions rather than QuerySet methods.
# =============================================================================

# ─── TIME-SERIES ─────────────────────────────────────────────────────────────

def get_time_series(
    model_class,
    trunc_fn=TruncMonth,
    date_field: str = "created_at",
    value_field: str = "id",
    agg_fn=Count,
    annotation_name: str = "count",
    filter_q: Optional[Q] = None,
    limit: int = 12,
) -> list[dict]:
    """
    Generic time-series aggregation against any model.

    Examples:
        # Monthly farmer registrations
        get_time_series(Farmer, TruncMonth, limit=12)

        # Weekly harvest totals
        get_time_series(
            CropSeason, TruncWeek,
            value_field="actual_yield_kg",
            agg_fn=Sum,
            annotation_name="total_kg",
        )

        # Quarterly order revenue
        get_time_series(
            Order, TruncQuarter,
            value_field="total_value",
            agg_fn=Sum,
            annotation_name="revenue",
            filter_q=Q(status="delivered"),
        )
    """
    qs = model_class.objects.all()
    if filter_q:
        qs = qs.filter(filter_q)
    return list(
        qs
        .annotate(period=trunc_fn(date_field))
        .values("period")
        .annotate(**{annotation_name: agg_fn(value_field)})
        .order_by("-period")[:limit]
    )


def get_monthly_counts(
    model_class,
    months: int = 12,
    filter_q: Q = None,
) -> list[dict]:
    return get_time_series(model_class, TruncMonth, limit=months, filter_q=filter_q)


def get_weekly_counts(
    model_class,
    weeks: int = 8,
    filter_q: Q = None,
) -> list[dict]:
    return get_time_series(model_class, TruncWeek, limit=weeks, filter_q=filter_q)


def compare_periods(
    model_class,
    current_start,
    current_end,
    previous_start,
    previous_end,
    date_field: str = "created_at",
    filter_q: Q = None,
) -> dict:
    """
    Period-over-period comparison for dashboard KPI cards.

    Returns:
        {current, previous, change, change_pct, improved, trend_label}
    """
    base = model_class.objects.all()
    if filter_q:
        base = base.filter(filter_q)

    current  = base.filter(**{f"{date_field}__range": (current_start, current_end)}).count()
    previous = base.filter(**{f"{date_field}__range": (previous_start, previous_end)}).count()

    change_pct  = round(((current - previous) / previous) * 100, 1) if previous else 0.0
    trend_label = "up" if current > previous else ("down" if current < previous else "flat")

    return {
        "current":     current,
        "previous":    previous,
        "change":      current - previous,
        "change_pct":  change_pct,
        "improved":    current >= previous,
        "trend_label": trend_label,
    }


def get_mtd_count(
    model_class,
    date_field: str = "created_at",
    filter_q: Q = None,
) -> int:
    now = timezone.now()
    qs  = model_class.objects.filter(**{
        f"{date_field}__month": now.month,
        f"{date_field}__year":  now.year,
    })
    if filter_q:
        qs = qs.filter(filter_q)
    return qs.count()


def get_ytd_count(
    model_class,
    date_field: str = "created_at",
    filter_q: Q = None,
) -> int:
    qs = model_class.objects.filter(**{f"{date_field}__year": timezone.now().year})
    if filter_q:
        qs = qs.filter(filter_q)
    return qs.count()


def get_mtd_sum(
    model_class,
    sum_field: str,
    date_field: str = "created_at",
    filter_q: Q = None,
) -> float:
    """Month-to-date sum of a numeric field (e.g. revenue, weight_kg)."""
    now = timezone.now()
    qs  = model_class.objects.filter(**{
        f"{date_field}__month": now.month,
        f"{date_field}__year":  now.year,
    })
    if filter_q:
        qs = qs.filter(filter_q)
    return qs.aggregate(total=Coalesce(Sum(sum_field), Value(0.0)))["total"]


def get_ytd_sum(
    model_class,
    sum_field: str,
    date_field: str = "created_at",
    filter_q: Q = None,
) -> float:
    qs = model_class.objects.filter(**{f"{date_field}__year": timezone.now().year})
    if filter_q:
        qs = qs.filter(filter_q)
    return qs.aggregate(total=Coalesce(Sum(sum_field), Value(0.0)))["total"]


# ─── REGIONAL SUMMARY HELPERS ─────────────────────────────────────────────────

def annotate_region_summary(queryset: QS, verified_field: str = "verification_status") -> QS:
    """
    Groups a queryset by region + district and annotates with:
      total, verified, pending, female (if gender field exists).

    Compatible with Farmer, FieldOfficer, Farm, TraceRecord.
    """
    annotations = {
        "total":    Count("id"),
        "verified": Count("id", filter=Q(**{verified_field: "verified"})),
        "pending":  Count("id", filter=Q(**{verified_field: "pending"})),
    }
    return (
        queryset
        .values("region", "district")
        .annotate(**annotations)
        .order_by("region", "district")
    )


def annotate_with_counts(queryset: QS, related_fields: list[str]) -> QS:
    """
    Annotates a queryset with `<field>__count` for each related field.

        annotate_with_counts(qs, ["farms", "crop_seasons", "visits"])
    """
    for field in related_fields:
        queryset = queryset.annotate(
            **{f"{field}__count": Count(field, distinct=True)}
        )
    return queryset


def annotate_with_sum(
    queryset: QS,
    field: str,
    annotation_name: str = None,
    filter_q: Q = None,
) -> QS:
    name   = annotation_name or f"{field}__sum"
    kwargs = {name: Sum(field, filter=filter_q) if filter_q else Sum(field)}
    return queryset.annotate(**kwargs)


def get_summary_by_field(
    model_class,
    group_by_field: str,
    count_field: str = "id",
    extra_annotations: dict = None,
    filter_q: Q = None,
    order_by: str = "-count",
) -> list[dict]:
    """
    Group records by a single field with count + optional extra annotations.

        get_summary_by_field(Farmer, "region")
        get_summary_by_field(Farm, "region", extra_annotations={"total_area": Sum("area_hectares")})
    """
    qs = model_class.objects.all()
    if filter_q:
        qs = qs.filter(filter_q)
    annotations = {"count": Count(count_field)}
    if extra_annotations:
        annotations.update(extra_annotations)
    return list(
        qs.values(group_by_field)
        .annotate(**annotations)
        .order_by(order_by)
    )


def get_leaderboard(
    model_class,
    score_field: str,
    name_fields: list[str],
    top_n: int = 10,
    filter_q: Q = None,
) -> list[dict]:
    """
    Generic top-N leaderboard by a numeric field.

        get_leaderboard(FieldOfficer, "farmer_count", ["user__first_name", "officer_code"])
    """
    qs = model_class.objects.all()
    if filter_q:
        qs = qs.filter(filter_q)
    return list(
        qs.values(*name_fields)
        .annotate(score=Sum(score_field))
        .order_by("-score")[:top_n]
    )


# ─── GEO PROXIMITY HELPERS ────────────────────────────────────────────────────

def nearby_query(
    model_class,
    lat: float,
    lon: float,
    radius_km: float = 10.0,
    lat_field: str = "latitude",
    lon_field: str = "longitude",
) -> QS:
    """
    Bounding-box geo filter for any model with latitude/longitude fields.
    Returns a queryset — does NOT compute exact Haversine distance.

    For exact distances, follow with distance_annotated().

        nearby_query(Farm, 6.5, -1.5, radius_km=5)
    """
    deg_per_km = 1 / 111.0
    lat_delta  = radius_km * deg_per_km
    lon_delta  = radius_km * deg_per_km / max(math.cos(math.radians(lat)), 1e-6)
    return model_class.objects.filter(**{
        f"{lat_field}__range":  (lat - lat_delta,  lat + lat_delta),
        f"{lon_field}__range":  (lon - lon_delta,  lon + lon_delta),
    })


def distance_annotated(
    queryset: QS,
    lat: float,
    lon: float,
    lat_field: str = "latitude",
    lon_field: str = "longitude",
) -> QS:
    """
    Annotates `distance_km` on a queryset using flat-earth approximation.
    Suitable for farm/community proximity sorting on the map view.

        qs = distance_annotated(Farm.objects.all(), 6.5, -1.5)
        qs.order_by("distance_km")[:20]
    """
    return queryset.annotate(
        distance_km=ExpressionWrapper(
            (
                (F(lat_field) - Value(lat)) * Value(111.0)
            ) ** 2
            + (
                (F(lon_field) - Value(lon)) * Value(111.0) * Value(math.cos(math.radians(lat)))
            ) ** 2,
            output_field=FloatField(),
        )
    ).order_by("distance_km")


# ─── TRACEABILITY CHAIN HELPERS ───────────────────────────────────────────────

def build_chain(trace_record) -> dict:
    """
    Build a structured traceability chain dict from a TraceRecord instance.
    Useful for QR code scan API responses and PDF certificate generation.

    Returns a dict with nested sections:
        {
          "farmer":     {...},
          "farm":       {...},
          "batch":      {...},
          "warehouse":  {...},
          "processing": [...],
          "product":    {...},
          "certifications": [...],
        }
    """
    chain = {
        "trace_code":    getattr(trace_record, "trace_code", ""),
        "scan_url":      f"/api/v1/trace/{getattr(trace_record, 'trace_code', '')}/",
        "farmer": {
            "code":       getattr(trace_record.farmer, "farmer_code", ""),
            "name":       getattr(trace_record.farmer, "full_name", ""),
            "region":     getattr(trace_record.farmer, "region", ""),
            "district":   getattr(trace_record.farmer, "district", ""),
            "community":  getattr(trace_record.farmer, "community", ""),
            "gender":     getattr(trace_record.farmer, "gender", ""),
        } if hasattr(trace_record, "farmer") and trace_record.farmer else None,
        "farm": {
            "code":       getattr(trace_record.farm, "farm_code", ""),
            "area_ha":    getattr(trace_record.farm, "area_hectares", None),
            "crop_type":  getattr(trace_record.farm, "current_crop_type", ""),
            "latitude":   float(trace_record.farm.latitude)  if getattr(trace_record.farm, "latitude",  None) else None,
            "longitude":  float(trace_record.farm.longitude) if getattr(trace_record.farm, "longitude", None) else None,
        } if hasattr(trace_record, "farm") and trace_record.farm else None,
        "batch": {
            "farmer_batch_code":    getattr(trace_record, "farmer_batch_code", ""),
            "warehouse_batch_code": getattr(trace_record, "warehouse_batch_code", ""),
            "product_batch_code":   getattr(trace_record, "product_batch_code", ""),
            "weight_kg":            getattr(trace_record, "weight_kg", None),
            "harvest_date":         str(trace_record.harvest_date) if getattr(trace_record, "harvest_date", None) else None,
        },
        "product": {
            "name":     getattr(trace_record.product, "name", "") if hasattr(trace_record, "product") and trace_record.product else "",
            "category": getattr(trace_record.product, "category", "") if hasattr(trace_record, "product") and trace_record.product else "",
        },
        "status":       getattr(trace_record, "status", ""),
        "generated_at": timezone.now().isoformat(),
    }
    return chain


def resolve_qr_code(qr_code: str, model_class) -> Optional[object]:
    """
    Resolve a QR code string to its TraceRecord instance.
    Tries trace_code, farmer_batch_code, and product_batch_code in order.
    Returns None if not found.

        record = resolve_qr_code("TRC-GH-2025-83920", TraceRecord)
    """
    try:
        return model_class.objects.select_related(
            "farmer", "farm", "product"
        ).get(
            Q(trace_code__iexact=qr_code)
            | Q(farmer_batch_code__iexact=qr_code)
            | Q(product_batch_code__iexact=qr_code),
            is_active=True,
        )
    except (model_class.DoesNotExist, model_class.MultipleObjectsReturned):
        return None


# ─── DASHBOARD AGGREGATION HELPERS ───────────────────────────────────────────

def build_kpi_block(
    model_class,
    label: str,
    filter_q: Q = None,
    include_mtd: bool = True,
    include_ytd: bool = True,
    include_trend: bool = True,
) -> dict:
    """
    Build a standardised KPI block for a dashboard card.

    Returns:
        {
          "label":      "Total Farmers",
          "total":      1250,
          "mtd":        48,
          "ytd":        320,
          "trend": {"current": 48, "previous": 35, "change_pct": 37.1, "trend_label": "up"}
        }
    """
    from datetime import timedelta

    qs    = model_class.objects.all()
    if filter_q:
        qs = qs.filter(filter_q)

    block: dict = {"label": label, "total": qs.count()}

    if include_mtd:
        block["mtd"] = get_mtd_count(model_class, filter_q=filter_q)

    if include_ytd:
        block["ytd"] = get_ytd_count(model_class, filter_q=filter_q)

    if include_trend:
        now               = timezone.now()
        current_month_start  = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        previous_month_end   = current_month_start - timedelta(seconds=1)
        previous_month_start = previous_month_end.replace(day=1)
        block["trend"] = compare_periods(
            model_class,
            current_start  = current_month_start,
            current_end    = now,
            previous_start = previous_month_start,
            previous_end   = previous_month_end,
            filter_q       = filter_q,
        )

    return block


def multi_model_dashboard(model_configs: list[dict]) -> dict:
    """
    Build a complete dashboard stats block from multiple models in a single call.

    model_configs: list of dicts, each with:
        {"key": "farmers", "model": Farmer, "label": "Total Farmers", "filter_q": Q(verification_status="verified")}

    Returns:
        {
          "farmers":  {"label": "...", "total": ..., "mtd": ..., "ytd": ..., "trend": {...}},
          "orders":   {...},
          "payments": {...},
          ...
        }

    Usage (in a view):
        stats = multi_model_dashboard([
            {"key": "farmers",  "model": Farmer, "label": "Verified Farmers", "filter_q": Q(verification_status="verified")},
            {"key": "orders",   "model": Order,  "label": "Active Orders",    "filter_q": Q(status__in=["pending","confirmed"])},
            {"key": "payments", "model": Payment,"label": "Payments This Month"},
        ])
    """
    result = {}
    for cfg in model_configs:
        key      = cfg["key"]
        model    = cfg["model"]
        label    = cfg.get("label", key.title())
        filter_q = cfg.get("filter_q")
        result[key] = build_kpi_block(model, label=label, filter_q=filter_q)
    return result