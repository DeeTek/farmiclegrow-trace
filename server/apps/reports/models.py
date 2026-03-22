"""
apps/reports/models.py  —  FarmicleGrow-Trace Platform

Models:
  Report          On-demand or scheduled report record + generated file
  ReportSchedule  Recurring schedule for automated report generation

SRD MODULE 10 coverage:
  ✓ Farmer production statistics
  ✓ Staff performance ranking
  ✓ Warehouse utilisation metrics
  ✓ Revenue and transaction reports
  ✓ CO2 reduction estimates
  ✓ Women participation metrics
  ✓ Traceability export chain summary

Design:
  Report rows are written by the Celery task and are effectively
  append-only after status reaches "ready" or "failed".
  File field stores the generated CSV/PDF — served via signed URL or
  direct media URL depending on the storage backend.
  ReportSchedule is driven by a Celery beat task that reads enabled
  schedules and enqueues generate_report_task at the right cadence.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models.abstract import BaseModel

# ---------------------------------------------------------------------------
# Choices  (also imported by serializers.py)
# ---------------------------------------------------------------------------

REPORT_TYPES = [
    ("farmer_summary",       _("Farmer Summary")),
    ("farm_production",      _("Farm Production")),
    ("staff_performance",    _("Staff Performance")),
    ("traceability_chain",   _("Traceability Chain")),
    ("warehouse_utilisation",_("Warehouse Utilisation")),
    ("order_summary",        _("Order Summary")),
    ("payment_summary",      _("Payment Summary")),
    ("buyer_activity",       _("Buyer Activity")),
    ("impact_dashboard",     _("Impact Dashboard")),
    ("co2_savings",          _("CO2 Savings")),
    ("women_participation",  _("Women Participation")),
    ("product_quality",      _("Product Quality")),
]

OUTPUT_FORMATS = [
    ("csv",  _("CSV")),
    ("pdf",  _("PDF")),
    ("json", _("JSON")),
    ("xlsx", _("Excel")),
]

FREQUENCIES = [
    ("daily",   _("Daily")),
    ("weekly",  _("Weekly")),
    ("monthly", _("Monthly")),
]

REPORT_STATUS = [
    ("queued",      _("Queued")),
    ("generating",  _("Generating")),
    ("ready",       _("Ready")),
    ("failed",      _("Failed")),
]


# =============================================================================
# REPORT
# =============================================================================

class Report(BaseModel):
    """
    A single on-demand or scheduled report generation record.

    Lifecycle:
      queued → generating → ready | failed

    The Celery task updates status, file, row_count, file_size_bytes,
    started_at and completed_at.  error_message is populated on failure.

    file_size_display and duration_seconds are computed properties used
    by ReportSerializer — no extra DB columns needed.
    """

    report_type   = models.CharField(max_length=40, choices=REPORT_TYPES, db_index=True)
    title         = models.CharField(max_length=300)
    description   = models.TextField(blank=True)
    output_format = models.CharField(
        max_length=10, choices=OUTPUT_FORMATS, default="csv",
    )
    status = models.CharField(
        max_length=15, choices=REPORT_STATUS,
        default="queued", db_index=True,
    )

    # ── Who requested it ──────────────────────────────────────────────────────
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reports",
        help_text=_("User who triggered the report. Null for scheduled/system reports."),
    )

    # ── Input filters (stored as JSON) ────────────────────────────────────────
    filters = models.JSONField(
        default=dict, blank=True,
        help_text=_(
            "Applied filters: region, district, date_from, date_to, "
            "year, month, officer_id, product_id, status."
        ),
    )

    # ── Output ────────────────────────────────────────────────────────────────
    file           = models.FileField(
                         upload_to="reports/%Y/%m/", null=True, blank=True,
                         help_text=_("Generated report file (CSV, PDF, XLSX, JSON)."),
                     )
    row_count      = models.PositiveIntegerField(
                         null=True, blank=True,
                         help_text=_("Number of data rows in the generated report."),
                     )
    file_size_bytes = models.PositiveIntegerField(null=True, blank=True)
    error_message   = models.TextField(blank=True)

    # ── Timing ────────────────────────────────────────────────────────────────
    queued_at    = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at   = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name        = _("Report")
        verbose_name_plural = _("Reports")
        ordering            = ["-queued_at"]
        indexes             = [
            models.Index(fields=["report_type", "status"]),
            models.Index(fields=["requested_by", "queued_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} [{self.status}] — {self.queued_at:%Y-%m-%d}"

    # ── Computed display properties ───────────────────────────────────────────

    @property
    def file_size_display(self) -> str | None:
        """Human-readable file size: '1.2 MB', '340 KB', etc."""
        if not self.file_size_bytes:
            return None
        size = self.file_size_bytes
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    @property
    def duration_seconds(self) -> float | None:
        """Wall-clock seconds between started_at and completed_at."""
        if self.started_at and self.completed_at:
            return round((self.completed_at - self.started_at).total_seconds(), 2)
        return None


# =============================================================================
# REPORT SCHEDULE
# =============================================================================

class ReportSchedule(BaseModel):
    """
    Recurring report generation schedule.

    A Celery beat task reads all enabled schedules and, when
    next_run_at <= now(), enqueues generate_report_task and advances
    next_run_at by the frequency interval.

    recipients is a JSON list of email addresses that receive the
    generated file via email once the report is ready.
    """

    report_type   = models.CharField(max_length=40, choices=REPORT_TYPES, db_index=True)
    title         = models.CharField(max_length=300)
    output_format = models.CharField(max_length=10, choices=OUTPUT_FORMATS, default="csv")
    frequency     = models.CharField(max_length=10, choices=FREQUENCIES, default="monthly")
    filters       = models.JSONField(default=dict, blank=True)
    recipients    = models.JSONField(
                        default=list, blank=True,
                        help_text=_("Email addresses to receive the report when generated."),
                    )
    is_enabled    = models.BooleanField(default=True, db_index=True)
    next_run_at   = models.DateTimeField(
                        null=True, blank=True, db_index=True,
                        help_text=_("When the schedule will next generate a report."),
                    )
    last_run_at   = models.DateTimeField(null=True, blank=True)
    created_by    = models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.SET_NULL, null=True, blank=True,
                        related_name="report_schedules",
                    )

    class Meta(BaseModel.Meta):
        verbose_name        = _("Report Schedule")
        verbose_name_plural = _("Report Schedules")
        ordering            = ["next_run_at"]
        indexes             = [
            models.Index(fields=["is_enabled", "next_run_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.frequency})"

    def advance_next_run(self) -> None:
        """
        Advance next_run_at by the frequency interval.
        Called by the beat task after enqueuing the report.
        """
        from datetime import timedelta
        now = timezone.now()
        if self.frequency == "daily":
            self.next_run_at = now + timedelta(days=1)
        elif self.frequency == "weekly":
            self.next_run_at = now + timedelta(weeks=1)
        else:  # monthly
            # Advance by ~30 days — simple and avoids monthday edge cases
            self.next_run_at = now + timedelta(days=30)
        self.last_run_at = now
        self.save(update_fields=["next_run_at", "last_run_at"])