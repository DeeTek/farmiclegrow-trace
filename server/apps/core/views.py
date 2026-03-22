"""
apps/core/views.py  —  FarmicleGrow-Trace Platform

Views that live in core because they span multiple domain apps and cannot
be owned by any single one without creating circular imports.

  HealthCheckView         GET /api/v1/health/
  VersionView             GET /api/v1/version/
  GlobalSearchAPIView     GET /api/v1/search/
  ImpersonationStatusView GET /api/v1/impersonation-status/

QR scan endpoint:
  QRScanView lives in apps.traceability.views — it belongs to the
  traceability domain and only imports from its own app's models and
  serializers. No circular import risk since traceability → core is
  already the established dependency direction.
"""
from __future__ import annotations

import logging

from django.core.cache import cache
from django.utils import timezone

from rest_framework import status as http_status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import SimpleRateThrottle

from apps.core.search import GlobalSearchView
from apps.core.models.mixins import _get_user_role

logger = logging.getLogger("apps.core.views")


# =============================================================================
# HEALTH CHECK
# =============================================================================

from rest_framework.exceptions import Throttled

class SearchThrottle(SimpleRateThrottle):
    scope = "search"

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            ident = str(request.user.pk)
        else:
            ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}

    def throttle_failure(self):
        raise Throttled(detail={"message": "Too many search requests. Please slow down and try again."})

class HealthCheckView(APIView):
    """
    GET /api/v1/health/

    Liveness + readiness probe for load balancers, uptime monitors, and CI.
    No authentication required.

    Returns HTTP 200 when healthy, HTTP 503 when degraded.

    Critical checks (database, cache) drive the overall HTTP status.
    Non-critical checks (celery, storage) are reported but do not affect it.
    """

    permission_classes = [AllowAny]
    CRITICAL_CHECKS    = {"database", "cache"}

    def get(self, request):
        checks: dict[str, str] = {}

        # ── Database ──────────────────────────────────────────────────────────
        try:
            from django.db import connection
            connection.ensure_connection()
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"error: {exc}"
            logger.error("health_check | database: %s", exc)

        # ── Cache ─────────────────────────────────────────────────────────────
        try:
            probe_key = "_health_check_probe"
            cache.set(probe_key, "1", timeout=5)
            checks["cache"] = "ok" if cache.get(probe_key) == "1" else "error: key not found"
        except Exception as exc:
            checks["cache"] = f"error: {exc}"
            logger.error("health_check | cache: %s", exc)

        # ── Storage (non-critical) ────────────────────────────────────────────
        try:
            import os
            from django.conf import settings
            media_root      = getattr(settings, "MEDIA_ROOT", "")
            checks["storage"] = (
                "ok" if media_root and os.path.isdir(media_root)
                else "warning: MEDIA_ROOT not found"
            )
        except Exception as exc:
            checks["storage"] = f"error: {exc}"

        # ── Celery (non-critical) ─────────────────────────────────────────────
        try:
            from celery import current_app
            current_app.control.ping(timeout=1)
            checks["celery"] = "ok"
        except Exception:
            checks["celery"] = "unavailable"

        # Filter by KEY name — not value — to correctly exclude non-critical checks
        critical_ok = all(
            checks[key] == "ok"
            for key in self.CRITICAL_CHECKS
            if key in checks
        )
        overall = "healthy" if critical_ok else "degraded"

        return Response(
            {
                "status":    overall,
                "timestamp": timezone.now().isoformat(),
                "checks":    checks,
            },
            status=(
                http_status.HTTP_200_OK
                if critical_ok
                else http_status.HTTP_503_SERVICE_UNAVAILABLE
            ),
        )


# =============================================================================
# VERSION
# =============================================================================

class VersionView(APIView):
    """
    GET /api/v1/version/

    Returns platform metadata. No authentication required.
    Useful for CI pipelines, mobile app version gating, and API clients
    confirming they're talking to the right service.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({
            "system":      "FarmicleGrow-Trace",
            "api_version": "v1",
            "build":       "2025.1.0",
            "description": "Farm-to-buyer agricultural traceability platform",
            "timestamp":   timezone.now().isoformat(),
            "endpoints": {
                "docs":                 "/api/v1/schema/",
                "health":               "/api/v1/health/",
                "search":               "/api/v1/search/?q=<query>",
                "autocomplete":         "/api/v1/search/autocomplete/?q=<prefix>&group=<key>",
                "scan":                 "/api/v1/scan/<code>/",
                "impersonation_status": "/api/v1/impersonation-status/",
            },
        })


# =============================================================================
# GLOBAL SEARCH  (cross-app — no single app owns all registered models)
# =============================================================================

class GlobalSearchAPIView(GlobalSearchView):
    """
    GET /api/v1/search/?q=<query>

    Thin subclass of GlobalSearchView that lives in the URL routing layer.
    All logic is in apps.core.search.GlobalSearchView.

    Lives in core because it searches across farmers, buyers, staff,
    traceability, and any future app — no domain app can own it without
    creating circular imports.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes  = [SearchThrottle]

