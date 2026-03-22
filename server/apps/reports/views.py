"""
apps/reports/views.py  —  FarmicleGrow-Trace Platform

ReportViewSet        — CRUD + queue + download
ReportScheduleViewSet — CRUD for recurring schedules

All business logic delegated to reports.services.
No direct DB writes in views — only service calls.
"""
from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response

from apps.core.models.mixins import AuditCreateMixin, DateRangeFilterMixin

from .models import Report, ReportSchedule
from .serializers import (
    ReportCreateSerializer,
    ReportListSerializer,
    ReportScheduleSerializer,
    ReportScheduleWriteSerializer,
    ReportSerializer,
)


# =============================================================================
# PERMISSIONS
# =============================================================================

class IsAdminOrStaff(permissions.BasePermission):
    """Reports are restricted to staff and admin roles."""

    def has_permission(self, request, view):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        role = getattr(request.user, "role", None)
        return request.user.is_authenticated and (
            request.user.is_superuser
            or role in (
                User.Role.SUPER_ADMIN,
                User.Role.FIELD_OFFICER,
                User.Role.WAREHOUSE_MANAGER,
            )
        )


# =============================================================================
# REPORT VIEWSET
# =============================================================================

class ReportViewSet(AuditCreateMixin, DateRangeFilterMixin, viewsets.ModelViewSet):
    """
    Endpoint                              Method  Permission
    ─────────────────────────────────────────────────────────
    /v1/reports/                          GET     staff / admin
    /v1/reports/queue/                    POST    staff / admin
    /v1/reports/<id>/                     GET     staff / admin
    /v1/reports/<id>/                     DELETE  admin
    /v1/reports/<id>/download/            GET     staff / admin
    /v1/reports/<id>/retry/               POST    admin

    POST /v1/reports/queue/ does NOT use the standard create() path.
    It validates via ReportCreateSerializer then calls services.queue_report().
    This avoids the standard ModelViewSet.create() which would skip service logic.
    """

    queryset         = Report.objects.all().select_related("requested_by")
    serializer_class = ReportSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]
    filter_backends  = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["report_type", "status", "output_format"]
    search_fields    = ["title", "report_type"]
    ordering_fields  = ["queued_at", "completed_at", "status"]
    ordering         = ["-queued_at"]
    date_range_field = "queued_at"
    http_method_names = ["get", "post", "delete", "head", "options"]  # no PUT/PATCH on reports

    def get_queryset(self):
        qs = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
          return qs.none()
        # Non-admin users see only their own reports
        if not (self.request.user.is_superuser or
                getattr(self.request.user, "role", None) in ("super_admin",)):
            qs = qs.filter(requested_by=self.request.user)
        return qs

    def get_serializer_class(self):
        if self.action == "list":   return ReportListSerializer
        if self.action == "queue":  return ReportCreateSerializer
        return ReportSerializer

    # Disable standard create — use /queue/ instead
    def create(self, request, *args, **kwargs):
        return Response(
            {"detail": "Use POST /reports/queue/ to generate a report."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    # Disable update — reports are immutable once queued
    def update(self, request, *args, **kwargs):
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def partial_update(self, request, *args, **kwargs):
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

    # ── Queue a new report ────────────────────────────────────────────────────

    @action(detail=False, methods=["post"])
    def queue(self, request):
        """
        POST /v1/reports/queue/
        Validates input, creates Report row with status=queued, and
        dispatches the Celery task via reports.services.queue_report().
        """
        from apps.reports.services import queue_report

        ser = ReportCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        report = queue_report(
            report_type   = ser.validated_data["report_type"],
            title         = ser.validated_data["title"],
            output_format = ser.validated_data["output_format"],
            filters       = ser.validated_data.get("filters", {}),
            requested_by  = request.user,
        )
        return Response(
            ReportSerializer(report, context={"request": request}).data,
            status=status.HTTP_202_ACCEPTED,
        )

    # ── Download ──────────────────────────────────────────────────────────────

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        """
        GET /v1/reports/<id>/download/
        Returns a redirect or direct URL to the generated file.
        Returns 404 if the report is not yet ready.
        """
        report = self.get_object()
        if report.status != "ready" or not report.file:
            return Response(
                {"detail": "Report is not ready for download.", "status": report.status},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {"download_url": request.build_absolute_uri(report.file.url)},
            status=status.HTTP_200_OK,
        )

    # ── Retry failed report ───────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        """
        POST /v1/reports/<id>/retry/
        Re-queue a failed report without creating a new row.
        Admin only.
        """
        if not (request.user.is_superuser or
                getattr(request.user, "role", None) == "super_admin"):
            return Response(status=status.HTTP_403_FORBIDDEN)

        report = self.get_object()
        if report.status != "failed":
            return Response(
                {"detail": "Only failed reports can be retried."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.reports.tasks import generate_report_task
        report.status        = "queued"
        report.error_message = ""
        report.save(update_fields=["status", "error_message"])
        generate_report_task.delay(str(report.pk))

        return Response(
            ReportSerializer(report, context={"request": request}).data,
            status=status.HTTP_202_ACCEPTED,
        )


# =============================================================================
# REPORT SCHEDULE VIEWSET
# =============================================================================

class ReportScheduleViewSet(AuditCreateMixin, viewsets.ModelViewSet):
    """
    Endpoint                                  Method  Permission
    ─────────────────────────────────────────────────────────────
    /v1/report-schedules/                     GET     admin
    /v1/report-schedules/                     POST    admin
    /v1/report-schedules/<id>/                GET     admin
    /v1/report-schedules/<id>/                PATCH   admin
    /v1/report-schedules/<id>/                DELETE  admin
    /v1/report-schedules/<id>/toggle/         POST    admin
    """

    queryset           = ReportSchedule.objects.all().select_related("created_by")
    serializer_class   = ReportScheduleSerializer
    permission_classes = [permissions.IsAdminUser]
    filter_backends    = [DjangoFilterBackend, OrderingFilter]
    filterset_fields   = ["report_type", "frequency", "is_enabled"]
    ordering           = ["next_run_at"]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return ReportScheduleWriteSerializer
        return ReportScheduleSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"])
    def toggle(self, request, pk=None):
        """POST /v1/report-schedules/<id>/toggle/ — enable or disable the schedule."""
        schedule            = self.get_object()
        schedule.is_enabled = not schedule.is_enabled
        schedule.save(update_fields=["is_enabled"])
        return Response(
            ReportScheduleSerializer(schedule).data,
            status=status.HTTP_200_OK,
        )