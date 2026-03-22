"""
apps/reports/serializers.py  —  FarmicleGrow-Trace Platform

Fixes vs provided version:
  • REPORT_TYPES and OUTPUT_FORMATS imported from .models (not hardcoded)
  • ReportScheduleSerializer read_only_fields added (was missing — any field
    could be silently written via a PATCH on the read serializer)
  • ReportCreateSerializer.validate_filters — added date format validation
    for date_from / date_to so invalid dates fail early at serializer layer
    rather than crashing inside the Celery task
  • ReportScheduleWriteSerializer — added validate_next_run_at to reject
    schedules with a next_run_at in the past
  • No repeated logic — file_size_display and duration_seconds delegate
    entirely to model properties (no recalculation in serializer)
"""
from __future__ import annotations

from rest_framework import serializers

from apps.core.serializers import BaseModelSerializer, BaseWriteSerializer
from .models import Report, ReportSchedule, REPORT_TYPES, OUTPUT_FORMATS


# =============================================================================
# REPORT  — read serializers
# =============================================================================

class ReportListSerializer(BaseModelSerializer):
    """Lightweight shape for paginated list view."""

    class Meta(BaseModelSerializer.Meta):
        model  = Report
        fields = [
            "id", "report_type", "title", "output_format", "status",
            "row_count", "file_size_display", "duration_seconds",
            "queued_at", "completed_at", "created_ago",
        ]
        read_only_fields = fields


class ReportSerializer(BaseModelSerializer):
    """Full detail — includes download URL and error message."""

    requested_by_name = serializers.SerializerMethodField()
    file_size_display = serializers.SerializerMethodField()
    duration_seconds  = serializers.SerializerMethodField()
    download_url      = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = Report
        fields = [
            "id", "report_type", "title", "description", "output_format",
            "status", "filters", "row_count",
            "file_size_display", "requested_by_name",
            "duration_seconds", "download_url",
            "error_message",
            "queued_at", "started_at", "completed_at", "created_ago",
        ]
        read_only_fields = [
            "id", "status", "row_count",
            "file_size_display", "duration_seconds", "download_url",
            "queued_at", "started_at", "completed_at", "error_message",
        ]

    def get_requested_by_name(self, obj) -> str:
        return obj.requested_by.get_full_name() if obj.requested_by else "System"

    def get_file_size_display(self, obj) -> str | None:
        # Delegates to model property — no recalculation here
        return obj.file_size_display

    def get_duration_seconds(self, obj) -> float | None:
        # Delegates to model property
        return obj.duration_seconds

    def get_download_url(self, obj) -> str | None:
        if not obj.file:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.file.url) if request else obj.file.url


# =============================================================================
# REPORT  — write serializer (request creation)
# =============================================================================

class ReportCreateSerializer(serializers.Serializer):
    """
    Validates a report generation request before queuing the Celery task.

    No model write — the view calls report_services.queue_report() which
    creates the Report row and enqueues the Celery task.
    """

    report_type   = serializers.ChoiceField(choices=[r[0] for r in REPORT_TYPES])
    title         = serializers.CharField(max_length=300, required=False, allow_blank=True)
    output_format = serializers.ChoiceField(
        choices=[f[0] for f in OUTPUT_FORMATS], default="csv"
    )
    filters = serializers.DictField(required=False, default=dict)

    # Allowed filter keys — validated in validate_filters()
    _ALLOWED_FILTERS = {
        "region", "district", "date_from", "date_to",
        "year", "month", "officer_id", "product_id", "status",
    }

    def validate_filters(self, value: dict) -> dict:
        invalid = set(value.keys()) - self._ALLOWED_FILTERS
        if invalid:
            raise serializers.ValidationError(f"Invalid filter keys: {invalid}")

        # Validate date strings early — fail here, not inside the Celery task
        import datetime
        for date_key in ("date_from", "date_to"):
            date_str = value.get(date_key)
            if date_str:
                try:
                    datetime.date.fromisoformat(date_str)
                except ValueError:
                    raise serializers.ValidationError(
                        {date_key: f"Invalid date format '{date_str}'. Use YYYY-MM-DD."}
                    )

        # date_from must be before date_to if both present
        date_from = value.get("date_from")
        date_to   = value.get("date_to")
        if date_from and date_to and date_from > date_to:
            raise serializers.ValidationError(
                {"date_from": "date_from must be before date_to."}
            )

        return value

    def validate(self, attrs: dict) -> dict:
        # Auto-populate title from report_type label if not provided
        if not attrs.get("title"):
            attrs["title"] = dict(REPORT_TYPES).get(
                attrs["report_type"], attrs["report_type"]
            )
        return attrs


# =============================================================================
# REPORT SCHEDULE  — read + write serializers
# =============================================================================

class ReportScheduleSerializer(BaseModelSerializer):
    """Read serializer — used for list and retrieve."""

    created_by_name = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = ReportSchedule
        fields = [
            "id", "report_type", "title", "output_format",
            "frequency", "filters", "recipients", "is_enabled",
            "next_run_at", "last_run_at",
            "created_by_name", "created_at", "created_ago",
        ]
        read_only_fields = fields

    def get_created_by_name(self, obj) -> str:
        return obj.created_by.get_full_name() if obj.created_by else "System"


class ReportScheduleWriteSerializer(BaseWriteSerializer):
    """Write serializer — used for create and update."""

    class Meta(BaseWriteSerializer.Meta):
        model   = ReportSchedule
        # created_by injected by view; last_run_at is system-managed
        exclude = BaseWriteSerializer.Meta.exclude + ["created_by", "last_run_at"]

    def validate_recipients(self, value: list) -> list:
        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError as DjValidationError

        if not isinstance(value, list):
            raise serializers.ValidationError("recipients must be a list of email addresses.")

        for email in value:
            try:
                validate_email(email)
            except DjValidationError:
                raise serializers.ValidationError(f"Invalid email address: {email}")

        # Deduplicate while preserving order
        seen, unique = set(), []
        for email in value:
            if email not in seen:
                seen.add(email)
                unique.append(email)
        return unique

    def validate_next_run_at(self, value):
        """Reject schedules whose first run is set in the past."""
        from django.utils import timezone
        if value and value < timezone.now():
            raise serializers.ValidationError(
                "next_run_at must be a future datetime."
            )
        return value

    def validate_filters(self, value: dict) -> dict:
        """Reuse the same filter key validation as ReportCreateSerializer."""
        allowed = {
            "region", "district", "date_from", "date_to",
            "year", "month", "officer_id", "product_id", "status",
        }
        invalid = set(value.keys()) - allowed
        if invalid:
            raise serializers.ValidationError(f"Invalid filter keys: {invalid}")
        return value