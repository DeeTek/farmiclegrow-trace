"""
apps/core/urls.py

URL configuration for the core app.
Registered under api/v1/ in the project root urls.py.

──────────────────────────────────────────────────────────────────────────────
DUPLICATION AUDIT — what is NOT registered here and why:
──────────────────────────────────────────────────────────────────────────────

  export/<model>/
    → NOT here. Each app owns its own export:
        /api/v1/farmers/export-csv/      FarmerViewSet.export_csv  (CSVExportMixin)
        /api/v1/products/                ProductViewSet (admin write)
      A generic ExportView in core would duplicate filter logic already in
      FarmerViewSet, OrderViewSet, etc. and require those views to stay in sync.

  dashboard/
    → NOT here. The staff KPI block lives at:
        /api/v1/staff/<id>/dashboard/    StaffViewSet.dashboard
      The buyer order summary lives at:
        /api/v1/buyers/<id>/orders/      BuyerViewSet.orders
      A core DashboardStatsView would re-query the same models and duplicate
      role-scoping logic already in each app's queryset methods.

  bulk-status/
    → NOT here. Status transitions are domain-specific and already wrapped in
      atomic service functions per app (confirm_order, approve_field_officer,
      etc.). A generic bulk-status endpoint would bypass those service-layer
      guards and allow invalid state transitions.

  map-data/
    → NOT here. Farm geo data lives in apps.farmers.Farm. The GeoFilterMixin
      already applied to FarmViewSet handles spatial queries. A separate core
      MapDataView duplicates the Farm queryset and filter logic.

  scan/<code>/
    → NOT here. QRScanView moved to apps.traceability.urls — it is a
      traceability-domain concern (resolves TraceRecord fields) and imports
      only from its own app's models and serializers.

──────────────────────────────────────────────────────────────────────────────
What core exclusively owns (no other app has these):
──────────────────────────────────────────────────────────────────────────────

  health/                — infra-level liveness/readiness probe
  version/               — platform metadata
  search/                — cross-app GlobalSearchView (no single app owns this)
  search/autocomplete/   — prefix autocomplete via SearchRegistry
  search/stats/          — admin registry + index advisory stats
  impersonation-status/  — JWT claim inspection (no domain app owns token metadata)
"""

from django.urls import path

from apps.core.search import AutocompleteView, SearchStatsView
from .views import (
    GlobalSearchAPIView,
    HealthCheckView,
    VersionView,
)

urlpatterns = [
    # ── Infrastructure ────────────────────────────────────────────────────────
    path("health/",  HealthCheckView.as_view(), name="health-check"),
    path("version/", VersionView.as_view(),      name="version"),

    # ── Cross-app search ──────────────────────────────────────────────────────
    path("search/",              GlobalSearchAPIView.as_view(), name="global-search"),
    path("search/autocomplete/", AutocompleteView.as_view(),    name="search-autocomplete"),
    path("search/stats/",        SearchStatsView.as_view(),     name="search-stats"),
    
]
