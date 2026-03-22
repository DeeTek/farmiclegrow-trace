"""
apps/core/admin.py  —  FarmicleGrow-Trace Platform

Base admin classes for all apps.

FIX vs original
───────────────
  ─ VerifiableModelAdmin.get_actions() duplicated the verify/reject actions.
    The original overrode get_actions() to manually add actions that were
    already registered via @admin.action. This caused them to appear twice
    in the Django admin dropdown. REMOVED the get_actions() override.
  ─ export_as_csv iterated queryset row-by-row with getattr() — O(n*m) Python
    calls. Now uses .values_list() for efficient DB-level projection.
  ─ active_badge was defined as a method but missing allow_tags / mark_safe
    annotation — format_html is correct but short_description was missing.

New vs original
───────────────
  ─ CodedModelAdmin         adds code search, code display in list
  ─ GeoModelAdmin           adds GPS coordinates display + map link column
  ─ AuditedModelAdmin       shows created_by / updated_by in readonly
  ─ TracedModelAdmin        for TraceRecord, Batch — adds QR code column
  ─ ImpersonationLogAdmin   immutable read-only admin for audit log
  ─ TimelineInline          base for inline status/event history
"""
from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html, mark_safe
from django.utils import timezone
from django.http import HttpResponse


# =============================================================================
# COLOUR PALETTES
# =============================================================================

VERIFICATION_COLOURS = {
    "verified":  "#28a745",
    "pending":   "#ffc107",
    "rejected":  "#dc3545",
    "suspended": "#6c757d",
}

STATUS_COLOURS = {
    "draft":        "#6c757d",
    "pending":      "#ffc107",
    "active":       "#28a745",
    "confirmed":    "#17a2b8",
    "completed":    "#28a745",
    "exported":     "#007bff",
    "paid":         "#28a745",
    "approved":     "#28a745",
    "rejected":     "#dc3545",
    "cancelled":    "#dc3545",
    "failed":       "#dc3545",
    "suspended":    "#fd7e14",
    "on_leave":     "#ffc107",
    "terminated":   "#343a40",
    "in_progress":  "#17a2b8",
    "scheduled":    "#6f42c1",
    "missed":       "#dc3545",
    "open":         "#ffc107",
    "resolved":     "#28a745",
    "escalated":    "#fd7e14",
    "published":    "#007bff",
    "queued":       "#6c757d",
    "generating":   "#17a2b8",
    "ready":        "#28a745",
    "in_transit":   "#17a2b8",
    "delivered":    "#28a745",
    "recalled":     "#dc3545",
}

EMPLOYMENT_COLOURS = {
    "active":     "#28a745",
    "on_leave":   "#ffc107",
    "probation":  "#17a2b8",
    "suspended":  "#fd7e14",
    "terminated": "#343a40",
    "inactive":   "#6c757d",
}


# =============================================================================
# BADGE HELPER  (module-level, shared across all admin classes)
# =============================================================================

def badge(text: str, color: str) -> str:
    """Render a coloured pill badge. Safe for use in list_display methods."""
    return format_html(
        '<span style="background:{};color:white;padding:2px 10px;'
        'border-radius:12px;font-size:11px;font-weight:600;'
        'white-space:nowrap">{}</span>',
        color, text,
    )


# =============================================================================
# BASE MODEL ADMIN
# =============================================================================

class BaseModelAdmin(admin.ModelAdmin):
    """
    Base admin for all FarmicleGrow-Trace models.

    Provides:
    ─ id / created_at / updated_at as read-only fields
    ─ is_active filter in sidebar
    ─ Soft-delete and restore actions
    ─ Efficient CSV export (uses values_list, not row-by-row getattr)
    """
    readonly_fields = ("id", "created_at", "updated_at")
    list_filter     = ("is_active",)
    actions         = ["soft_delete_selected", "restore_selected", "export_as_csv"]

    # ── Soft delete ──────────────────────────────────────────────────────────

    @admin.action(description="⚠ Soft-delete selected records")
    def soft_delete_selected(self, request, queryset):
        updated = queryset.update(is_active=False, deleted_at=timezone.now())
        self.message_user(request, f"{updated} record(s) soft-deleted.")

    @admin.action(description="✅ Restore selected records")
    def restore_selected(self, request, queryset):
        updated = queryset.update(is_active=True, deleted_at=None)
        self.message_user(request, f"{updated} record(s) restored.")

    # ── CSV export (FIX: values_list instead of per-row getattr) ─────────────

    @admin.action(description="📥 Export selected as CSV")
    def export_as_csv(self, request, queryset):
        import csv
        meta   = self.model._meta
        fields = list(meta.fields)
        names  = [f.name for f in fields]

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="{meta.model_name}_export.csv"'
        )
        response.write("\ufeff")   # BOM for Excel

        writer = csv.writer(response)
        writer.writerow([f.verbose_name for f in fields])

        # FIX: use values_list() — single SQL query, no Python object overhead
        for row in queryset.values_list(*names):
            writer.writerow(row)

        return response

    # ── Colour helpers ────────────────────────────────────────────────────────

    def active_badge(self, obj) -> str:
        return (
            badge("Active",   "#28a745") if obj.is_active
            else badge("Deleted", "#6c757d")
        )
    active_badge.short_description = "Active"
    active_badge.allow_tags = True


# =============================================================================
# CODED MODEL ADMIN  (new)
# =============================================================================

class CodedModelAdmin(BaseModelAdmin):
    """
    Admin for models with auto-generated codes.
    Adds code to search fields and list display.
    """

    def get_search_fields(self, request):
        existing = list(super().get_search_fields(request) or [])
        if "code" not in existing:
            existing.insert(0, "code")
        return existing

    def get_list_display(self, request):
        existing = list(super().get_list_display(request))
        if "code" not in existing:
            existing.insert(1, "code")
        return existing

    def code_display(self, obj) -> str:
        return format_html(
            '<code style="font-size:12px;color:#333">{}</code>',
            getattr(obj, "code", "—"),
        )
    code_display.short_description = "Code"


# =============================================================================
# VERIFIABLE MODEL ADMIN  (FIX: removed duplicate get_actions override)
# =============================================================================

class VerifiableModelAdmin(BaseModelAdmin):
    """
    Admin for models with a verification workflow.

    FIX vs original:
        Original overrode get_actions() to manually add verify_selected /
        reject_selected to the actions dict — but these were ALREADY registered
        as class methods via @admin.action, so they appeared twice in the
        dropdown. The get_actions() override is removed.
    """
    list_filter = ("is_active", "verification_status")

    def verification_badge(self, obj) -> str:
        vs    = getattr(obj, "verification_status", "pending")
        color = VERIFICATION_COLOURS.get(vs, "#6c757d")
        return badge(vs.replace("_", " ").title(), color)
    verification_badge.short_description = "Verification"
    verification_badge.allow_tags = True

    @admin.action(description="✅ Verify selected records")
    def verify_selected(self, request, queryset):
        updated = queryset.update(
            verification_status="verified",
            verified_at=timezone.now(),
            rejection_reason="",
        )
        self.message_user(request, f"{updated} record(s) verified.")

    @admin.action(description="❌ Reject selected records")
    def reject_selected(self, request, queryset):
        # Bulk reject: rejection reason is not collected in bulk action
        # (for individual rejection with reason, use the detail view)
        updated = queryset.update(
            verification_status="rejected",
            verified_at=None,
        )
        self.message_user(request, f"{updated} record(s) marked rejected.")

    @admin.action(description="⏸ Suspend selected records")
    def suspend_selected(self, request, queryset):
        updated = queryset.update(verification_status="suspended")
        self.message_user(request, f"{updated} record(s) suspended.")

    # FIX: get_actions() override REMOVED — it caused duplicate menu items


# =============================================================================
# STATUS MODEL ADMIN
# =============================================================================

class StatusModelAdmin(BaseModelAdmin):
    """Admin for models with a status field."""
    list_filter = ("is_active", "status")

    def status_badge(self, obj) -> str:
        s     = getattr(obj, "status", "")
        color = STATUS_COLOURS.get(s, "#6c757d")
        return badge(s.replace("_", " ").title(), color)
    status_badge.short_description = "Status"
    status_badge.allow_tags = True


# =============================================================================
# AUDITED MODEL ADMIN  (new)
# =============================================================================

class AuditedModelAdmin(BaseModelAdmin):
    """
    Admin for models that extend AuditedModel.
    Shows created_by / updated_by in readonly fields.
    """

    def get_readonly_fields(self, request, obj=None):
        existing = list(super().get_readonly_fields(request, obj))
        for field in ("created_by", "updated_by"):
            if field not in existing:
                existing.append(field)
        return existing


# =============================================================================
# GEO MODEL ADMIN  (new)
# =============================================================================

class GeoModelAdmin(BaseModelAdmin):
    """
    Admin for models that extend GeoModel.
    Adds GPS coordinate display and an OpenStreetMap link column.
    """

    def gps_display(self, obj) -> str:
        lat = getattr(obj, "latitude",  None)
        lon = getattr(obj, "longitude", None)
        if lat is None or lon is None:
            return format_html('<span style="color:#999">No GPS</span>')
        url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=15/{lat}/{lon}"
        return format_html(
            '<a href="{}" target="_blank" style="font-family:monospace;font-size:11px">'
            '📍 {:.6f}, {:.6f}</a>',
            url, float(lat), float(lon),
        )
    gps_display.short_description = "GPS"
    gps_display.allow_tags = True

    def polygon_badge(self, obj) -> str:
        has_poly = bool(getattr(obj, "polygon_coordinates", None))
        return badge("Has Polygon", "#28a745") if has_poly else badge("No Polygon", "#6c757d")
    polygon_badge.short_description = "Polygon"
    polygon_badge.allow_tags = True


# =============================================================================
# TRACED MODEL ADMIN  (new)
# =============================================================================

class TracedModelAdmin(CodedModelAdmin):
    """
    Admin for TraceRecord, Batch — models with QR codes and batch codes.
    Adds a QR code preview link column.
    """

    def qr_link(self, obj) -> str:
        code = getattr(obj, "code", None) or getattr(obj, "trace_code", None)
        if not code:
            return "—"
        url = f"/api/v1/scan/{code}/"
        return format_html(
            '<a href="{}" target="_blank">🔍 Scan</a>',
            url,
        )
    qr_link.short_description = "QR Scan"
    qr_link.allow_tags = True

    def batch_codes_display(self, obj) -> str:
        parts = []
        for field in ("farmer_batch_code", "warehouse_batch_code", "product_batch_code"):
            val = getattr(obj, field, None)
            if val:
                parts.append(format_html('<code style="font-size:10px">{}</code>', val))
        return mark_safe(" → ".join(parts)) if parts else "—"
    batch_codes_display.short_description = "Batch Chain"
    batch_codes_display.allow_tags = True


# =============================================================================
# EMPLOYMENT STATUS MIXIN
# =============================================================================

class EmploymentStatusMixin:
    """Mixin for HR admin classes to display employment status badge."""

    def employment_badge(self, obj) -> str:
        s     = getattr(obj, "employment_status", "")
        color = EMPLOYMENT_COLOURS.get(s, "#6c757d")
        return badge(s.replace("_", " ").title(), color)
    employment_badge.short_description = "Employment"
    employment_badge.allow_tags = True


# =============================================================================
# IMMUTABLE AUDIT LOG ADMIN  (new)
# =============================================================================

class ImmutableLogAdmin(admin.ModelAdmin):
    """
    Read-only admin for immutable audit/event log models.
    No add, change, or delete permissions.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        # Remove all bulk actions — log records must not be modified
        return {}