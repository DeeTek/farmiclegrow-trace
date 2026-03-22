"""
apps/analytics/views.py  —  FarmicleGrow-Trace Platform

Single AnalyticsViewSet registered on DefaultRouter.

All analytics endpoints are read-only list-level @action methods (no detail
actions, no CRUD). The ViewSet has no queryset or model — it's a pure
service-aggregation ViewSet.

DefaultRouter generates these URLs when registered as r"analytics":

  GET  /api/v1/analytics/dashboard/
  GET  /api/v1/analytics/farmer-trend/
  GET  /api/v1/analytics/farmer-breakdown/
  GET  /api/v1/analytics/supply-chain-trend/
  GET  /api/v1/analytics/quality-metrics/
  GET  /api/v1/analytics/trace-status/
  GET  /api/v1/analytics/export-map/
  GET  /api/v1/analytics/revenue-trend/
  GET  /api/v1/analytics/staff-leaderboard/
  GET  /api/v1/analytics/regional-kpi/
  GET  /api/v1/analytics/buyer-engagement/
  GET  /api/v1/analytics/crop-yield/
  GET  /api/v1/analytics/impact/
  POST /api/v1/analytics/snapshot-refresh/

Permission matrix:
  dashboard             → IsAuthenticated
  farmer-trend          → IsAuthenticated
  farmer-breakdown      → IsAuthenticated
  supply-chain-trend    → IsAuthenticated
  quality-metrics       → IsAuthenticated
  trace-status          → IsAuthenticated
  export-map            → IsAuthenticated
  revenue-trend         → IsAdminUser
  staff-leaderboard     → IsAdminUser
  regional-kpi          → IsAuthenticated
  buyer-engagement      → IsAdminUser
  crop-yield            → IsAuthenticated
  impact                → AllowAny  (public website)
  snapshot-refresh      → IsAdminUser
"""
from __future__ import annotations

import logging

from django.core.cache import cache
from django.utils import timezone

from rest_framework import permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from .models import PlatformSnapshot, RegionalSummary
from .serializers import (
    BuyerEngagementSerializer,
    ExportDestinationSerializer,
    FarmerTrendSerializer,
    PlatformSnapshotSerializer,
    QualityMetricsSerializer,
    RegionalSummarySerializer,
    RevenueTrendSerializer,
    StaffPerformanceSerializer,
    SupplyChainTrendSerializer,
)

logger   = logging.getLogger("apps.analytics")
_TTL     = 60 * 5   # 5-minute default cache TTL


# =============================================================================
# HELPERS
# =============================================================================

def _cache_get_or_set(key: str, fn, ttl: int = _TTL):
    """Return (data, from_cache). Calls fn() on miss and stores the result."""
    cached = cache.get(key)
    if cached is not None:
        return cached, True
    result = fn()
    cache.set(key, result, timeout=ttl)
    return result, False


def _ok(data, *, from_cache: bool = False, extra: dict = None) -> Response:
    """Standard analytics response envelope."""
    payload = {
        "data":         data,
        "cached":       from_cache,
        "generated_at": timezone.now().isoformat(),
    }
    if extra:
        payload.update(extra)
    return Response(payload)


def _cache_key(*parts) -> str:
    return "analytics:" + ":".join(str(p) for p in parts)


# =============================================================================
# VIEWSET
# =============================================================================

class AnalyticsViewSet(ViewSet):
    """
    Pure service-aggregation ViewSet — no queryset, no model.
    All actions are list-level (@action detail=False).
    Permissions are set per action via get_permissions().
    """

    # Permission map — action name → permission class(es)
    _PERMISSION_MAP = {
        "dashboard":        [permissions.IsAuthenticated],
        "farmer_trend":     [permissions.IsAuthenticated],
        "farmer_breakdown": [permissions.IsAuthenticated],
        "supply_chain_trend":[permissions.IsAuthenticated],
        "quality_metrics":  [permissions.IsAuthenticated],
        "trace_status":     [permissions.IsAuthenticated],
        "export_map":       [permissions.IsAuthenticated],
        "regional_kpi":     [permissions.IsAuthenticated],
        "crop_yield":       [permissions.IsAuthenticated],
        "impact":           [permissions.AllowAny],
        "revenue_trend":    [permissions.IsAdminUser],
        "staff_leaderboard":[permissions.IsAdminUser],
        "buyer_engagement": [permissions.IsAdminUser],
        "snapshot_refresh": [permissions.IsAdminUser],
    }

    def get_permissions(self):
        perms = self._PERMISSION_MAP.get(self.action, [permissions.IsAuthenticated])
        return [p() for p in perms]

    # =========================================================================
    # DASHBOARD
    # =========================================================================

    @action(detail=False, methods=["get"], url_path="dashboard")
    def dashboard(self, request):
        """
        GET /api/v1/analytics/dashboard/
        Returns the pre-computed PlatformSnapshot singleton — O(1) query.
        Refreshed by the Celery beat task every 15 minutes.

        Query params:
            refresh=1  → force synchronous refresh (admin only)
        """
        if request.query_params.get("refresh") == "1" and request.user.is_superuser:
            snapshot = PlatformSnapshot.get_or_create_singleton()
            snapshot.refresh()
            return _ok(PlatformSnapshotSerializer(snapshot).data)

        key    = _cache_key("dashboard")
        cached = cache.get(key)
        if cached:
            return _ok(cached, from_cache=True)

        snapshot = PlatformSnapshot.get_or_create_singleton()
        data     = PlatformSnapshotSerializer(snapshot).data
        cache.set(key, data, timeout=_TTL)
        return _ok(data)

    # =========================================================================
    # FARMER ANALYTICS
    # =========================================================================

    @action(detail=False, methods=["get"], url_path="farmer-trend")
    def farmer_trend(self, request):
        """
        GET /api/v1/analytics/farmer-trend/
        Monthly new farmer registrations — new, verified, female per month.

        Query params:
            months  (int, 1-36, default 12)
            region  (string, optional)
        """
        from apps.analytics.services import get_farmer_trend

        months = min(int(request.query_params.get("months", 12)), 36)
        region = request.query_params.get("region", "")

        key = _cache_key("farmer_trend", months, region)
        data, from_cache = _cache_get_or_set(
            key,
            lambda: get_farmer_trend(months=months, region=region or None),
        )
        return _ok(FarmerTrendSerializer(data, many=True).data, from_cache=from_cache)

    @action(detail=False, methods=["get"], url_path="farmer-breakdown")
    def farmer_breakdown(self, request):
        """
        GET /api/v1/analytics/farmer-breakdown/
        Gender distribution, education levels, region leaderboard,
        and verification state counts — all in one response.
        """
        from apps.farmers.models import Farmer
        from django.db.models import Count, Q

        key    = _cache_key("farmer_breakdown")
        cached = cache.get(key)
        if cached:
            return _ok(cached, from_cache=True)

        qs = Farmer.objects.filter(is_active=True)

        data = {
            "gender": list(
                qs.values("gender")
                .annotate(count=Count("id"))
                .order_by("-count")
            ),
            "education": list(
                qs.values("education_level")
                .annotate(count=Count("id"))
                .order_by("-count")
            ),
            "by_region": list(
                qs.values("region")
                .annotate(
                    total    = Count("id"),
                    verified = Count("id", filter=Q(verification_status="verified")),
                    female   = Count("id", filter=Q(gender="female")),
                )
                .order_by("-total")
            ),
            "verification": qs.aggregate(
                total     = Count("id"),
                verified  = Count("id", filter=Q(verification_status="verified")),
                pending   = Count("id", filter=Q(verification_status="pending")),
                rejected  = Count("id", filter=Q(verification_status="rejected")),
                suspended = Count("id", filter=Q(verification_status="suspended")),
            ),
        }
        cache.set(key, data, timeout=_TTL)
        return _ok(data)

    # =========================================================================
    # SUPPLY CHAIN ANALYTICS
    # =========================================================================

    @action(detail=False, methods=["get"], url_path="supply-chain-trend")
    def supply_chain_trend(self, request):
        """
        GET /api/v1/analytics/supply-chain-trend/
        Monthly batch creation + weight volumes across all three batch types.

        Query params:
            months (int, 1-24, default 12)
        """
        from apps.analytics.services import get_supply_chain_trend

        months = min(int(request.query_params.get("months", 12)), 24)
        key    = _cache_key("supply_chain_trend", months)
        data, from_cache = _cache_get_or_set(
            key, lambda: get_supply_chain_trend(months=months)
        )
        return _ok(SupplyChainTrendSerializer(data, many=True).data, from_cache=from_cache)

    @action(detail=False, methods=["get"], url_path="quality-metrics")
    def quality_metrics(self, request):
        """
        GET /api/v1/analytics/quality-metrics/
        Aggregate moisture %, impurity %, and grade distribution
        across all farmer batches.
        """
        from apps.analytics.services import get_quality_metrics
        from apps.traceability.models import Batch
        from django.db.models import Count

        key    = _cache_key("quality_metrics")
        cached = cache.get(key)
        if cached:
            return _ok(cached, from_cache=True)

        metrics    = get_quality_metrics()
        grade_dist = list(
            Batch.objects.filter(is_active=True, grade__gt="")
            .values("grade")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        data = {**metrics, "grade_distribution": grade_dist}
        cache.set(key, data, timeout=_TTL)
        return _ok(data)

    @action(detail=False, methods=["get"], url_path="trace-status")
    def trace_status(self, request):
        """
        GET /api/v1/analytics/trace-status/
        Current TraceRecord status distribution + monthly creation/export trend.

        Query params:
            months (int, 1-24, default 12)
        """
        from apps.analytics.services import get_trace_status_trend
        from apps.traceability.models import TraceRecord
        from django.db.models import Count

        key    = _cache_key("trace_status")
        cached = cache.get(key)
        if cached:
            return _ok(cached, from_cache=True)

        months      = min(int(request.query_params.get("months", 12)), 24)
        status_dist = list(
            TraceRecord.objects.filter(is_active=True)
            .values("status")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        data = {
            "status_distribution": status_dist,
            "monthly_trend":       get_trace_status_trend(months=months),
        }
        cache.set(key, data, timeout=_TTL)
        return _ok(data)

    # =========================================================================
    # EXPORT / GEOGRAPHY
    # =========================================================================

    @action(detail=False, methods=["get"], url_path="export-map")
    def export_map(self, request):
        """
        GET /api/v1/analytics/export-map/
        Export destination countries with shipment count, total weight,
        distinct farmer and product counts.
        Frontend renders as a world choropleth map.
        """
        from apps.analytics.services import get_export_destination_map

        key = _cache_key("export_map")
        data, from_cache = _cache_get_or_set(key, get_export_destination_map)
        return _ok(
            ExportDestinationSerializer(data, many=True).data,
            from_cache=from_cache,
        )

    # =========================================================================
    # REVENUE
    # =========================================================================

    @action(detail=False, methods=["get"], url_path="revenue-trend")
    def revenue_trend(self, request):
        """
        GET /api/v1/analytics/revenue-trend/   (admin only)
        Monthly revenue totals, order counts, and average order value.

        Query params:
            months (int, 1-36, default 12)
        """
        from apps.analytics.services import get_revenue_trend

        months = min(int(request.query_params.get("months", 12)), 36)
        key    = _cache_key("revenue_trend", months)
        data, from_cache = _cache_get_or_set(
            key, lambda: get_revenue_trend(months=months)
        )
        return _ok(RevenueTrendSerializer(data, many=True).data, from_cache=from_cache)

    # =========================================================================
    # STAFF ANALYTICS
    # =========================================================================

    @action(detail=False, methods=["get"], url_path="staff-leaderboard")
    def staff_leaderboard(self, request):
        """
        GET /api/v1/analytics/staff-leaderboard/   (admin only)
        Field officer performance ranking — farmers registered, visits, produce.

        Query params:
            top_n  (int, 1-50, default 20)
            region (string, optional)
        """
        from apps.analytics.services import get_staff_performance_ranking

        top_n  = min(int(request.query_params.get("top_n", 20)), 50)
        region = request.query_params.get("region", "")
        key    = _cache_key("staff_leaderboard", top_n, region)

        data, from_cache = _cache_get_or_set(
            key,
            lambda: get_staff_performance_ranking(top_n=top_n, region=region or None),
        )
        return _ok(StaffPerformanceSerializer(data, many=True).data, from_cache=from_cache)

    @action(detail=False, methods=["get"], url_path="regional-kpi")
    def regional_kpi(self, request):
        """
        GET /api/v1/analytics/regional-kpi/
        Monthly regional KPI snapshots from the RegionalSummary table.

        Query params:
            year   (int, default current year)
            month  (int, default current month)
            region (string, optional — filters to one region)
        """
        now   = timezone.now()
        year  = int(request.query_params.get("year",  now.year))
        month = int(request.query_params.get("month", now.month))
        region = request.query_params.get("region", "")

        key    = _cache_key("regional_kpi", year, month, region)
        cached = cache.get(key)
        if cached:
            return _ok(cached, from_cache=True)

        qs = RegionalSummary.objects.for_period(year, month)
        if region:
            qs = qs.filter(region__iexact=region)

        data = RegionalSummarySerializer(qs, many=True).data
        cache.set(key, data, timeout=_TTL)
        return _ok(data)

    # =========================================================================
    # BUYER ANALYTICS
    # =========================================================================

    @action(detail=False, methods=["get"], url_path="buyer-engagement")
    def buyer_engagement(self, request):
        """
        GET /api/v1/analytics/buyer-engagement/   (admin only)
        Repeat buyer rate, total reviews, and average product rating.
        """
        from apps.analytics.services import get_buyer_engagement

        key = _cache_key("buyer_engagement")
        data, from_cache = _cache_get_or_set(key, get_buyer_engagement)
        return _ok(BuyerEngagementSerializer(data).data, from_cache=from_cache)

    # =========================================================================
    # CROP & YIELD
    # =========================================================================

    @action(detail=False, methods=["get"], url_path="crop-yield")
    def crop_yield(self, request):
        """
        GET /api/v1/analytics/crop-yield/
        Crop variety yield performance — expected vs actual, by variety and region.

        Query params:
            year (int, optional — defaults to all years)
        """
        from apps.analytics.services import get_crop_yield_summary

        year = request.query_params.get("year")
        year = int(year) if year else None
        key  = _cache_key("crop_yield", year or "all")

        data, from_cache = _cache_get_or_set(
            key, lambda: get_crop_yield_summary(year=year)
        )
        return _ok(data, from_cache=from_cache)

    # =========================================================================
    # PUBLIC IMPACT DASHBOARD
    # =========================================================================

    @action(detail=False, methods=["get"], url_path="impact")
    def impact(self, request):
        """
        GET /api/v1/analytics/impact/   (public — no authentication required)
        Women empowerment %, CO2 savings estimate, verification rate,
        countries reached. Powers the public-facing impact page.
        """
        from apps.analytics.services import get_export_destination_map

        key    = _cache_key("impact")
        cached = cache.get(key)
        if cached:
            return _ok(cached, from_cache=True)

        snapshot     = PlatformSnapshot.get_or_create_singleton()
        destinations = get_export_destination_map()

        data = {
            "farmers": {
                "total":                snapshot.total_farmers,
                "verified":             snapshot.verified_farmers,
                "female":               snapshot.female_farmers,
                "verification_rate_pct": float(snapshot.verification_rate_pct),
                "women_empowerment_pct": float(snapshot.women_empowerment_pct),
            },
            "farms": {
                "total":            snapshot.total_farms,
                "total_area_ha":    float(snapshot.total_area_ha),
                "avg_area_ha":      float(snapshot.avg_farm_area_ha),
                # Estimate: 2.5 tCO2/ha/year for sustainable smallholder farming
                "co2_saved_t_year": round(float(snapshot.total_area_ha) * 2.5, 1),
            },
            "supply_chain": {
                "total_batches":          snapshot.total_batches,
                "total_weight_kg":        float(snapshot.total_weight_kg),
                "exported_shipments":     snapshot.exported_shipments,
                "destination_countries":  snapshot.destination_countries,
                "top_destinations": [
                    {
                        "country":   d["export_destination_country"],
                        "shipments": d["shipments"],
                    }
                    for d in destinations[:10]
                ],
            },
            "staff": {
                "active_officers":       snapshot.active_field_officers,
                "total_visits":          snapshot.total_farm_visits,
                "produce_collected_kg":  float(snapshot.total_produce_kg),
            },
            "last_refreshed_at": (
                snapshot.last_refreshed_at.isoformat()
                if snapshot.last_refreshed_at else None
            ),
        }
        cache.set(key, data, timeout=_TTL)
        return _ok(data)

    # =========================================================================
    # ADMIN UTILITY — SNAPSHOT REFRESH
    # =========================================================================

    @action(detail=False, methods=["post"], url_path="snapshot-refresh")
    def snapshot_refresh(self, request):
        """
        POST /api/v1/analytics/snapshot-refresh/   (admin only)
        Force a synchronous refresh of PlatformSnapshot + RegionalSummary.
        Clears all analytics cache keys after refresh.
        Use sparingly — normal refreshes are handled by the Celery beat task.
        """
        from apps.analytics.services import build_regional_summaries

        snapshot = PlatformSnapshot.get_or_create_singleton()
        snapshot.refresh()
        regions = build_regional_summaries()

        # Clear all analytics cache keys
        cache.delete_many([
            _cache_key("dashboard"),
            _cache_key("impact"),
            _cache_key("export_map"),
            _cache_key("quality_metrics"),
            _cache_key("buyer_engagement"),
            _cache_key("farmer_breakdown"),
            _cache_key("trace_status"),
        ])

        return Response({
            "status":          "refreshed",
            "regions_updated": regions,
            "refreshed_at":    snapshot.last_refreshed_at,
        })