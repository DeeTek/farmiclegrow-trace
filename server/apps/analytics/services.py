"""
apps/analytics/services.py  —  FarmicleGrow-Trace Platform

Aggregation services for the analytics app.

Functions:
  compute_platform_snapshot()     — full platform KPI dict (used by PlatformSnapshot.refresh)
  compute_regional_summary()      — one region's monthly KPI dict
  build_regional_summaries()      — create/update all RegionalSummary rows for current month
  get_farmer_trend()              — monthly farmer registration time series
  get_supply_chain_trend()        — monthly batch/weight time series
  get_revenue_trend()             — monthly payment totals
  get_staff_performance_ranking() — field officer leaderboard
  get_export_destination_map()    — GeoJSON-ready export destination data
  get_crop_yield_summary()        — crop variety performance by region
  get_quality_metrics()           — moisture/impurity averages across batches
  get_buyer_engagement()          — order frequency, review scores, repeat buyers

All functions return plain dicts or lists of dicts — no model instances.
Views and tasks call these functions directly.
"""
from __future__ import annotations

import logging
from typing import Any

from django.db.models import (
    Avg, Case, Count, DecimalField, ExpressionWrapper,
    F, FloatField, IntegerField, Max, Min, Q, Sum, Value, When,
)
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

logger = logging.getLogger("apps.analytics")


# =============================================================================
# PLATFORM SNAPSHOT
# =============================================================================

def compute_platform_snapshot() -> dict[str, Any]:
    """
    Compute all platform-wide KPIs in as few queries as possible.
    Returns a dict keyed by PlatformSnapshot field names.
    Called by PlatformSnapshot.refresh() and the Celery beat task.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    now  = timezone.now()

    result: dict[str, Any] = {}

    # ── Farmers ───────────────────────────────────────────────────────────────
    try:
        from apps.farmers.models import Farmer
        f = Farmer.objects.filter(is_active=True).aggregate(
            total    = Count("id"),
            verified = Count("id", filter=Q(verification_status="verified")),
            female   = Count("id", filter=Q(gender="female")),
            mtd      = Count("id", filter=Q(
                created_at__month=now.month,
                created_at__year=now.year,
            )),
        )
        total_f = f["total"] or 1
        result.update({
            "total_farmers":         f["total"],
            "verified_farmers":      f["verified"],
            "female_farmers":        f["female"],
            "farmers_this_month":    f["mtd"],
            "verification_rate_pct": round(f["verified"] / total_f * 100, 1),
            "women_empowerment_pct": round(f["female"]   / total_f * 100, 1),
        })
    except Exception as exc:
        logger.warning("compute_platform_snapshot | farmers failed: %s", exc)
        result.update({k: 0 for k in (
            "total_farmers", "verified_farmers", "female_farmers",
            "farmers_this_month", "verification_rate_pct", "women_empowerment_pct",
        )})

    # ── Farms ─────────────────────────────────────────────────────────────────
    try:
        from apps.farmers.models import Farm
        fm = Farm.objects.filter(is_active=True).aggregate(
            total    = Count("id"),
            total_ha = Coalesce(Sum("area_hectares"), Value(0.0), output_field=FloatField()),
        )
        total_fm = fm["total"] or 1
        result.update({
            "total_farms":     fm["total"],
            "total_area_ha":   round(float(fm["total_ha"]), 2),
            "avg_farm_area_ha": round(float(fm["total_ha"]) / total_fm, 2),
        })
    except Exception as exc:
        logger.warning("compute_platform_snapshot | farms failed: %s", exc)
        result.update({"total_farms": 0, "total_area_ha": 0, "avg_farm_area_ha": 0})

    # ── Farm visits + produce ─────────────────────────────────────────────────
    try:
        from apps.farmers.models import FarmVisit
        fv = FarmVisit.objects.filter(is_active=True).aggregate(
            visit_count  = Count("id"),
            total_produce = Coalesce(
                Sum("produce_collected_kg"), Value(0.0), output_field=FloatField()
            ),
        )
        result.update({
            "total_farm_visits": fv["visit_count"],
            "total_produce_kg":  round(float(fv["total_produce"]), 2),
        })
    except Exception as exc:
        logger.warning("compute_platform_snapshot | farm visits failed: %s", exc)
        result.update({"total_farm_visits": 0, "total_produce_kg": 0})

    # ── Field officers — counted via User.role ─────────────────────────────────
    try:
        result["active_field_officers"] = User.objects.filter(
            role=User.Role.FIELD_OFFICER,
            is_active=True,
        ).count()
    except Exception as exc:
        logger.warning("compute_platform_snapshot | officers failed: %s", exc)
        result["active_field_officers"] = 0

    # ── Batches + supply chain ────────────────────────────────────────────────
    try:
        from apps.traceability.models import Batch, TraceRecord
        b = Batch.objects.filter(is_active=True).aggregate(
            total    = Count("id"),
            total_kg = Coalesce(Sum("weight_kg"), Value(0.0), output_field=FloatField()),
        )
        t = TraceRecord.objects.filter(is_active=True).aggregate(
            total    = Count("id"),
            exported = Count("id", filter=Q(status="exported")),
            countries = Count(
                "export_destination_country", distinct=True,
                filter=Q(export_destination_country__gt=""),
            ),
        )
        result.update({
            "total_batches":         b["total"],
            "total_weight_kg":       round(float(b["total_kg"]), 2),
            "total_trace_records":   t["total"],
            "exported_shipments":    t["exported"],
            "destination_countries": t["countries"],
        })
    except Exception as exc:
        logger.warning("compute_platform_snapshot | supply chain failed: %s", exc)
        result.update({k: 0 for k in (
            "total_batches", "total_weight_kg", "total_trace_records",
            "exported_shipments", "destination_countries",
        )})

    # ── Orders + payments ─────────────────────────────────────────────────────
    try:
        from apps.buyers.models import Order, Payment, Buyer
        o = Order.objects.filter(is_active=True).aggregate(
            total = Count("id"),
            mtd   = Count("id", filter=Q(
                created_at__month=now.month, created_at__year=now.year,
            )),
        )
        p = Payment.objects.filter(is_active=True, status="completed").aggregate(
            total    = Coalesce(Sum("amount"), Value(0.0), output_field=FloatField()),
            mtd      = Coalesce(
                Sum("amount", filter=Q(
                    created_at__month=now.month, created_at__year=now.year,
                )),
                Value(0.0), output_field=FloatField(),
            ),
            avg_val  = Coalesce(Avg("amount"), Value(0.0), output_field=FloatField()),
        )
        result.update({
            "total_orders":        o["total"],
            "orders_this_month":   o["mtd"],
            "total_revenue_ghs":   round(float(p["total"]), 2),
            "revenue_this_month":  round(float(p["mtd"]), 2),
            "avg_order_value_ghs": round(float(p["avg_val"]), 2),
            "total_buyers":        Buyer.objects.filter(is_active=True).count(),
        })
    except Exception as exc:
        logger.warning("compute_platform_snapshot | commerce failed: %s", exc)
        result.update({k: 0 for k in (
            "total_orders", "orders_this_month", "total_revenue_ghs",
            "revenue_this_month", "avg_order_value_ghs", "total_buyers",
        )})

    result["last_refreshed_at"] = timezone.now()
    return result


# =============================================================================
# REGIONAL SUMMARIES
# =============================================================================

def compute_regional_summary(region: str, year: int, month: int) -> dict[str, Any]:
    """Return a KPI dict for one region + month — used to build RegionalSummary rows."""
    from django.contrib.auth import get_user_model
    User = get_user_model()

    result: dict[str, Any] = {"region": region, "year": year, "month": month}

    try:
        from apps.farmers.models import Farmer, Farm, FarmVisit
        from apps.traceability.models import Batch, TraceRecord

        f = Farmer.objects.filter(is_active=True, region__iexact=region).aggregate(
            total    = Count("id"),
            verified = Count("id", filter=Q(verification_status="verified")),
            female   = Count("id", filter=Q(gender="female")),
            new_mtd  = Count("id", filter=Q(created_at__month=month, created_at__year=year)),
        )
        fm = Farm.objects.filter(
            is_active=True, farmer__region__iexact=region
        ).aggregate(
            total    = Count("id"),
            total_ha = Coalesce(Sum("area_hectares"), Value(0.0), output_field=FloatField()),
        )
        fv = FarmVisit.objects.filter(
            is_active=True, farm__farmer__region__iexact=region
        ).aggregate(
            visits   = Count("id"),
            produce  = Coalesce(Sum("produce_collected_kg"), Value(0.0), output_field=FloatField()),
        )
        b = Batch.objects.filter(
            is_active=True, farmer__region__iexact=region
        ).aggregate(
            count    = Count("id"),
            total_kg = Coalesce(Sum("weight_kg"), Value(0.0), output_field=FloatField()),
        )
        t = TraceRecord.objects.filter(
            is_active=True, farmer__region__iexact=region
        ).aggregate(
            total    = Count("id"),
            exported = Count("id", filter=Q(status="exported")),
        )
        officers = User.objects.filter(
            role=User.Role.FIELD_OFFICER, is_active=True, region__iexact=region
        ).count()

        result.update({
            "farmer_count":   f["total"],
            "verified_count": f["verified"],
            "female_count":   f["female"],
            "new_farmers_mtd": f["new_mtd"],
            "farm_count":     fm["total"],
            "total_area_ha":  round(float(fm["total_ha"]), 2),
            "visit_count":    fv["visits"],
            "produce_kg":     round(float(fv["produce"]), 2),
            "batch_count":    b["count"],
            "total_weight_kg": round(float(b["total_kg"]), 2),
            "trace_records":  t["total"],
            "exported_count": t["exported"],
            "officer_count":  officers,
            "order_count":    0,  # order region lookup requires buyer address — skip
            "revenue_ghs":    0,
        })
    except Exception as exc:
        logger.warning("compute_regional_summary | region=%s | error=%s", region, exc)

    return result


def build_regional_summaries(year: int = None, month: int = None) -> int:
    """
    Build or update RegionalSummary rows for all known regions for a given month.
    Returns the count of rows created or updated.
    Called by the Celery beat task after refreshing PlatformSnapshot.
    """
    from apps.farmers.models import Farmer
    from apps.analytics.models import RegionalSummary

    now   = timezone.now()
    year  = year  or now.year
    month = month or now.month

    regions = (
        Farmer.objects.filter(is_active=True)
        .values_list("region", flat=True)
        .distinct()
        .order_by("region")
    )

    count = 0
    for region in regions:
        data = compute_regional_summary(region, year, month)
        RegionalSummary.objects.update_or_create(
            region=region, year=year, month=month,
            defaults={k: v for k, v in data.items() if k not in ("region", "year", "month")},
        )
        count += 1

    logger.info("build_regional_summaries | year=%s | month=%s | regions=%s", year, month, count)
    return count


# =============================================================================
# TIME-SERIES  (for chart data)
# =============================================================================

def get_farmer_trend(months: int = 12, region: str = None) -> list[dict]:
    """Monthly new farmer registrations — for the registration growth chart."""
    from apps.farmers.models import Farmer
    qs = Farmer.objects.filter(is_active=True)
    if region:
        qs = qs.filter(region__iexact=region)
    return list(
        qs
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(
            new_farmers   = Count("id"),
            verified      = Count("id", filter=Q(verification_status="verified")),
            female        = Count("id", filter=Q(gender="female")),
        )
        .order_by("-month")[:months]
    )


def get_supply_chain_trend(months: int = 12) -> list[dict]:
    """Monthly batch volume and weight — for the supply chain flow chart."""
    from apps.traceability.models import Batch
    return list(
        Batch.objects.filter(is_active=True)
        .annotate(month=TruncMonth("collection_date"))
        .values("month")
        .annotate(
            batch_count = Count("id"),
            total_kg    = Coalesce(Sum("weight_kg"), Value(0.0), output_field=FloatField()),
            farmer_batches    = Count("id", filter=Q(batch_type="farmer")),
            warehouse_batches = Count("id", filter=Q(batch_type="warehouse")),
            product_batches   = Count("id", filter=Q(batch_type="product")),
        )
        .filter(month__isnull=False)
        .order_by("-month")[:months]
    )


def get_revenue_trend(months: int = 12) -> list[dict]:
    """Monthly revenue from completed payments — for the revenue trend chart."""
    try:
        from apps.buyers.models import Payment
        return list(
            Payment.objects.filter(is_active=True, status="completed")
            .annotate(month=TruncMonth("created_at"))
            .values("month")
            .annotate(
                total_revenue   = Coalesce(Sum("amount"), Value(0.0), output_field=FloatField()),
                order_count     = Count("id", distinct=True),
                avg_order_value = Coalesce(Avg("amount"), Value(0.0), output_field=FloatField()),
            )
            .order_by("-month")[:months]
        )
    except Exception:
        return []


def get_trace_status_trend(months: int = 12) -> list[dict]:
    """Monthly trace record creation and export counts."""
    from apps.traceability.models import TraceRecord
    return list(
        TraceRecord.objects.filter(is_active=True)
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(
            created  = Count("id"),
            exported = Count("id", filter=Q(status="exported")),
            recalled = Count("id", filter=Q(status="recalled")),
        )
        .order_by("-month")[:months]
    )


# =============================================================================
# STAFF ANALYTICS
# =============================================================================

def get_staff_performance_ranking(top_n: int = 20, region: str = None) -> list[dict]:
    """
    Field officer leaderboard — farmers registered, visits, produce collected.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()

    qs = User.objects.filter(role=User.Role.FIELD_OFFICER, is_active=True)
    if region:
        qs = qs.filter(region__iexact=region)

    return list(
        qs.annotate(
            farmer_count = Count("registered_farmers", distinct=True,
                                 filter=Q(registered_farmers__is_active=True)),
            verified_count = Count("registered_farmers", distinct=True,
                                   filter=Q(
                                       registered_farmers__is_active=True,
                                       registered_farmers__verification_status="verified",
                                   )),
            visit_count  = Count("farm_visits", distinct=True,
                                 filter=Q(farm_visits__is_active=True)),
            produce_kg   = Coalesce(
                Sum("farm_visits__produce_collected_kg",
                    filter=Q(farm_visits__is_active=True)),
                Value(0.0), output_field=FloatField(),
            ),
        )
        .values(
            "id", "first_name", "last_name", "region",
            "farmer_count", "verified_count", "visit_count", "produce_kg",
        )
        .order_by("-farmer_count")[:top_n]
    )


# =============================================================================
# EXPORT & GEOGRAPHY
# =============================================================================

def get_export_destination_map() -> list[dict]:
    """
    Export volumes by destination country — for the world map chart.
    Returns GeoJSON-ready list with country, shipment count, and weight.
    """
    from apps.traceability.models import TraceRecord
    return list(
        TraceRecord.objects.filter(
            is_active=True, status="exported",
            export_destination_country__gt="",
        )
        .values("export_destination_country")
        .annotate(
            shipments  = Count("id"),
            total_kg   = Coalesce(Sum("weight_kg"), Value(0.0), output_field=FloatField()),
            farmers    = Count("farmer", distinct=True),
            products   = Count("product", distinct=True),
        )
        .order_by("-shipments")
    )


def get_farm_geo_summary() -> list[dict]:
    """Farm location summary by region and district for the map heatmap."""
    from apps.farmers.models import Farm
    return list(
        Farm.objects.filter(
            is_active=True,
            latitude__isnull=False,
            longitude__isnull=False,
        )
        .values("farmer__region", "farmer__district")
        .annotate(
            farm_count  = Count("id"),
            total_area  = Coalesce(Sum("area_hectares"), Value(0.0), output_field=FloatField()),
            avg_lat     = Avg("latitude"),
            avg_lon     = Avg("longitude"),
        )
        .order_by("farmer__region", "farmer__district")
    )


# =============================================================================
# PRODUCT & QUALITY
# =============================================================================

def get_crop_yield_summary(year: int = None) -> list[dict]:
    """
    Crop variety performance — expected vs actual yield, by variety and region.
    """
    from apps.farmers.models import CropSeason
    qs = CropSeason.objects.filter(is_active=True, actual_yield_kg__isnull=False)
    if year:
        qs = qs.filter(harvest_year=year)
    return list(
        qs.values("crop_variety", "farm__farmer__region")
        .annotate(
            total_expected_kg = Coalesce(Sum("expected_yield_kg"), Value(0.0), output_field=FloatField()),
            total_actual_kg   = Coalesce(Sum("actual_yield_kg"),   Value(0.0), output_field=FloatField()),
            avg_actual_kg     = Avg("actual_yield_kg"),
            season_count      = Count("id"),
            female_farmer_pct = ExpressionWrapper(
                Count("farm__farmer", filter=Q(farm__farmer__gender="female"), distinct=True)
                * 100.0
                / Count("farm__farmer", distinct=True),
                output_field=FloatField(),
            ),
        )
        .order_by("-total_actual_kg")
    )


def get_quality_metrics() -> dict:
    """
    Average moisture, impurity, and grade distribution across farmer batches.
    """
    from apps.traceability.models import Batch
    return Batch.objects.filter(
        is_active=True, batch_type="farmer",
        moisture_pct__isnull=False,
    ).aggregate(
        avg_moisture  = Coalesce(Avg("moisture_pct"), Value(0.0), output_field=FloatField()),
        avg_impurity  = Coalesce(Avg("impurity_pct"), Value(0.0), output_field=FloatField()),
        min_moisture  = Min("moisture_pct"),
        max_moisture  = Max("moisture_pct"),
        batch_count   = Count("id"),
        total_kg      = Coalesce(Sum("weight_kg"), Value(0.0), output_field=FloatField()),
    )


# =============================================================================
# BUYER ENGAGEMENT
# =============================================================================

def get_buyer_engagement() -> dict:
    """Buyer activity metrics — order frequency, repeat buyers, review scores."""
    try:
        from apps.buyers.models import Order, ProductReview, Buyer
        from django.db.models import Count, Avg

        total_buyers = Buyer.objects.filter(is_active=True).count()
        repeat_buyers = (
            Order.objects.filter(is_active=True, status="delivered")
            .values("buyer")
            .annotate(order_count=Count("id"))
            .filter(order_count__gte=2)
            .count()
        )
        review_stats = ProductReview.objects.filter(is_active=True).aggregate(
            total_reviews = Count("id"),
            avg_rating    = Coalesce(Avg("rating"), Value(0.0), output_field=FloatField()),
        )
        return {
            "total_buyers":   total_buyers,
            "repeat_buyers":  repeat_buyers,
            "repeat_rate_pct": round(repeat_buyers / total_buyers * 100, 1) if total_buyers else 0,
            "total_reviews":  review_stats["total_reviews"],
            "avg_rating":     round(float(review_stats["avg_rating"]), 2),
        }
    except Exception as exc:
        logger.warning("get_buyer_engagement | error: %s", exc)
        return {}