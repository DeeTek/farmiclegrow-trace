"""
apps/analytics/urls.py

URL configuration for the analytics app.

Registered under api/v1/ in the project root urls.py:
    path("api/v1/", include("apps.analytics.urls")),

Uses DefaultRouter — AnalyticsViewSet is registered at r"analytics".
All endpoints are list-level @action methods (no detail routes).

Generated URL patterns:

  GET  /api/v1/analytics/dashboard/             platform KPI snapshot
  GET  /api/v1/analytics/farmer-trend/          monthly registration trend
  GET  /api/v1/analytics/farmer-breakdown/      gender + education + region breakdown
  GET  /api/v1/analytics/supply-chain-trend/    batch + weight monthly trend
  GET  /api/v1/analytics/quality-metrics/       moisture/impurity/grade metrics
  GET  /api/v1/analytics/trace-status/          trace record status distribution + trend
  GET  /api/v1/analytics/export-map/            destination country shipment volumes
  GET  /api/v1/analytics/revenue-trend/         monthly revenue (admin only)
  GET  /api/v1/analytics/staff-leaderboard/     officer performance ranking (admin only)
  GET  /api/v1/analytics/regional-kpi/          regional KPI snapshots
  GET  /api/v1/analytics/buyer-engagement/      repeat buyers + review scores (admin only)
  GET  /api/v1/analytics/crop-yield/            crop variety yield performance
  GET  /api/v1/analytics/impact/                public impact dashboard (no auth)
  POST /api/v1/analytics/snapshot-refresh/      force snapshot refresh (admin only)
"""

from rest_framework.routers import DefaultRouter

from .views import AnalyticsViewSet

router = DefaultRouter()
router.register(r"analytics", AnalyticsViewSet, basename="analytics")

urlpatterns = router.urls