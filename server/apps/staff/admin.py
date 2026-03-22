"""
apps/staff/admin.py  —  FarmicleGrow-Trace Platform

Admin is split into two panels following the two lifecycles:
  Onboarding panel   — StaffApplication, StaffProfile, StaffStatusLog
  Operations panel   — StaffDeployment, StaffQualification, StaffHierarchy,
                        FieldSession, ProduceCollection, StaffKPISnapshot
"""
from django.contrib import admin

"""
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.staff.models import (
    ApplicationStatus, BatchStatus, FieldSession, ProfileStatus,
    ProduceCollection, SessionStatus, StaffApplication,
    StaffDeployment, StaffHierarchy, StaffKPISnapshot,
    StaffProfile, StaffQualification, StaffStatusLog,
)


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _status_badge(status_value: str, colour_map: dict) -> str:
    colour = colour_map.get(status_value, "grey")
    return format_html(
        '<span style="color:{}; font-weight:bold; text-transform:uppercase">{}</span>',
        colour, status_value.replace("_", " "),
    )


# ─────────────────────────────────────────────────────────────────────────────
# INLINES
# ─────────────────────────────────────────────────────────────────────────────

class StaffStatusLogInline(admin.TabularInline):
    model           = StaffStatusLog
    extra           = 0
    fields          = ["from_status", "to_status", "reason", "changed_by", "changed_at"]
    readonly_fields = fields
    can_delete      = False
    ordering        = ["-changed_at"]
    verbose_name    = "Status Transition"


class StaffDeploymentInline(admin.TabularInline):
    model           = StaffDeployment
    extra           = 0
    fields          = ["region", "district", "project_or_season", "start_date", "end_date", "status"]
    readonly_fields = ["status"]
    show_change_link = True


class StaffQualificationInline(admin.TabularInline):
    model           = StaffQualification
    extra           = 0
    fields          = ["certificate_type", "issuing_body", "issued_date", "expiry_date", "verification_status"]
    show_change_link = True


class FieldSessionInline(admin.TabularInline):
    model           = FieldSession
    extra           = 0
    fields          = ["session_date", "scope", "status", "farmers_registered_count",
                       "produce_records_count", "total_kg_collected"]
    readonly_fields = fields
    can_delete      = False
    show_change_link = True


class ProduceCollectionInline(admin.TabularInline):
    model           = ProduceCollection
    extra           = 0
    fields          = ["farmer_batch_code", "commodity_type", "quantity_kg",
                       "collection_date", "batch_status"]
    readonly_fields = fields
    can_delete      = False
    show_change_link = True


# ─────────────────────────────────────────────────────────────────────────────
# ONBOARDING — STAFF APPLICATION
# ─────────────────────────────────────────────────────────────────────────────

_APPLICATION_STATUS_COLOURS = {
    ApplicationStatus.SUBMITTED:           "blue",
    ApplicationStatus.UNDER_REVIEW:        "orange",
    ApplicationStatus.MORE_INFO_REQUESTED: "purple",
    ApplicationStatus.APPROVED:            "green",
    ApplicationStatus.REJECTED:            "red",
}


@admin.register(StaffApplication)
class StaffApplicationAdmin(admin.ModelAdmin):
    list_display  = [
        "full_name", "email", "intended_role",
        "preferred_region", "status_badge",
        "submitted_at", "reviewed_by",
    ]
    list_filter   = ["status", "intended_role", "preferred_region", "gender"]
    search_fields = ["full_name", "email", "ghana_card_number", "phone"]
    readonly_fields = [
        "submitted_at", "reviewed_at", "reviewed_by",
        "has_resulting_profile",
    ]
    ordering      = ["-submitted_at"]
    fieldsets     = (
        ("Application", {
            "fields": (
                "full_name", "email", "phone", "ghana_card_number",
                "date_of_birth", "gender", "educational_level",
            ),
        }),
        ("Role & Location", {
            "fields": ("intended_role", "preferred_region", "preferred_district"),
        }),
        ("Supporting Documents", {
            "fields": ("motivation_note", "qualification_document"),
        }),
        ("Review", {
            "fields": (
                "status", "review_note",
                "submitted_at", "reviewed_at", "reviewed_by",
                "has_resulting_profile",
            ),
        }),
    )

    def status_badge(self, obj):
        return format_html(
            '<span style="color:{}; font-weight:bold">{}</span>',
            _APPLICATION_STATUS_COLOURS.get(obj.status, "grey"),
            obj.get_status_display(),
        )
    status_badge.short_description = "Status"

    def has_resulting_profile(self, obj) -> str:
        return "✓ Profile created" if hasattr(obj, "resulting_profile") else "—"
    has_resulting_profile.short_description = "Staff Profile"

    def has_delete_permission(self, request, obj=None):
        return False  # Applications are kept permanently


# ─────────────────────────────────────────────────────────────────────────────
# ONBOARDING — STAFF PROFILE
# ─────────────────────────────────────────────────────────────────────────────

_PROFILE_STATUS_COLOURS = {
    ProfileStatus.PENDING_ACTIVATION: "blue",
    ProfileStatus.ACTIVE:             "green",
    ProfileStatus.ON_LEAVE:           "orange",
    ProfileStatus.SUSPENDED:          "red",
    ProfileStatus.TERMINATED:         "darkred",
}


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display  = [
        "staff_id", "full_name", "role", "employment_type",
        "status_badge", "active_deployment_summary",
        "onboarded_at",
    ]
    list_filter   = ["status", "role", "employment_type"]
    search_fields = ["staff_id", "full_name", "ghana_card_number", "user__email", "phone"]
    readonly_fields = [
        "staff_id", "ghana_card_number", "source_application",
        "onboarded_at", "created_at", "updated_at",
    ]
    inlines       = [
        StaffStatusLogInline, StaffDeploymentInline,
        StaffQualificationInline, FieldSessionInline,
    ]
    ordering      = ["-onboarded_at"]
    fieldsets     = (
        ("Identity", {
            "fields": (
                "staff_id", "user",
                "ghana_card_number", "full_name",
                "date_of_birth", "gender", "phone",
                "profile_photo",
            ),
        }),
        ("Emergency Contact", {
            "fields": ("emergency_contact_name", "emergency_contact_phone"),
            "classes": ("collapse",),
        }),
        ("Education", {
            "fields": ("educational_level",),
        }),
        ("Employment", {
            "fields": ("employment_type", "role", "status", "onboarded_at"),
        }),
        ("Provenance", {
            "fields": ("source_application", "created_at", "updated_at"),
        }),
    )

    def status_badge(self, obj):
        return format_html(
            '<span style="color:{}; font-weight:bold">{}</span>',
            _PROFILE_STATUS_COLOURS.get(obj.status, "grey"),
            obj.get_status_display(),
        )
    status_badge.short_description = "Status"

    def active_deployment_summary(self, obj) -> str:
        dep = obj.active_deployment
        if not dep:
            return "—"
        return f"{dep.district}, {dep.region}"
    active_deployment_summary.short_description = "Active Deployment"

    def has_delete_permission(self, request, obj=None):
        return False  # Profiles are never deleted


@admin.register(StaffStatusLog)
class StaffStatusLogAdmin(admin.ModelAdmin):
    list_display  = ["staff", "from_status", "to_status", "changed_by", "changed_at"]
    list_filter   = ["to_status"]
    search_fields = ["staff__staff_id", "staff__full_name"]
    readonly_fields = [f.name for f in StaffStatusLog._meta.fields]
    ordering      = ["-changed_at"]

    def has_add_permission(self, request):    return False
    def has_change_permission(self, *args):   return False
    def has_delete_permission(self, *args):   return False


# ─────────────────────────────────────────────────────────────────────────────
# OPERATIONAL — DEPLOYMENT
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(StaffDeployment)
class StaffDeploymentAdmin(admin.ModelAdmin):
    list_display  = [
        "staff", "region", "district",
        "project_or_season", "start_date", "end_date", "status",
    ]
    list_filter   = ["status", "region"]
    search_fields = ["staff__staff_id", "staff__full_name", "district", "project_or_season"]
    readonly_fields = ["created_at"]
    ordering      = ["-start_date"]


# ─────────────────────────────────────────────────────────────────────────────
# OPERATIONAL — QUALIFICATION
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(StaffQualification)
class StaffQualificationAdmin(admin.ModelAdmin):
    list_display  = [
        "staff", "certificate_type", "issuing_body",
        "issued_date", "expiry_date",
        "is_expired_badge", "verification_status",
    ]
    list_filter   = ["verification_status", "certificate_type"]
    search_fields = ["staff__staff_id", "staff__full_name", "issuing_body"]
    readonly_fields = ["verified_at", "created_at"]

    def is_expired_badge(self, obj) -> str:
        if obj.is_expired:
            return format_html('<span style="color:red; font-weight:bold">EXPIRED</span>')
        return format_html('<span style="color:green">Valid</span>')
    is_expired_badge.short_description = "Expiry"


# ─────────────────────────────────────────────────────────────────────────────
# OPERATIONAL — HIERARCHY
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(StaffHierarchy)
class StaffHierarchyAdmin(admin.ModelAdmin):
    list_display  = ["staff", "supervisor", "hierarchy_type", "start_date", "end_date", "is_active"]
    list_filter   = ["hierarchy_type", "is_active"]
    search_fields = ["staff__staff_id", "supervisor__staff_id"]
    readonly_fields = ["created_at"]


# ─────────────────────────────────────────────────────────────────────────────
# OPERATIONAL — FIELD SESSION
# ─────────────────────────────────────────────────────────────────────────────

_SESSION_STATUS_COLOURS = {
    SessionStatus.OPEN:      "blue",
    SessionStatus.SUBMITTED: "orange",
    SessionStatus.SYNCED:    "green",
    SessionStatus.FLAGGED:   "red",
}


@admin.register(FieldSession)
class FieldSessionAdmin(admin.ModelAdmin):
    list_display  = [
        "id_short", "staff", "session_date", "scope",
        "status_badge",
        "farmers_registered_count", "produce_records_count", "total_kg_collected",
        "opened_at",
    ]
    list_filter   = ["status", "scope", "session_date"]
    search_fields = ["staff__staff_id", "staff__full_name", "device_identifier"]
    readonly_fields = ["opened_at", "farmers_registered_count",
                       "produce_records_count", "total_kg_collected"]
    inlines       = [ProduceCollectionInline]
    ordering      = ["-opened_at"]

    def id_short(self, obj) -> str:
        return str(obj.pk)[:8]
    id_short.short_description = "Session ID"

    def status_badge(self, obj):
        return format_html(
            '<span style="color:{}; font-weight:bold">{}</span>',
            _SESSION_STATUS_COLOURS.get(obj.status, "grey"),
            obj.get_status_display(),
        )
    status_badge.short_description = "Status"

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status == SessionStatus.SYNCED:
            return False
        return super().has_delete_permission(request, obj)


# ─────────────────────────────────────────────────────────────────────────────
# OPERATIONAL — PRODUCE COLLECTION
# ─────────────────────────────────────────────────────────────────────────────

_BATCH_STATUS_COLOURS = {
    BatchStatus.COLLECTED:              "blue",
    BatchStatus.SUBMITTED_TO_WAREHOUSE: "orange",
    BatchStatus.RECEIVED_AT_WAREHOUSE:  "green",
    BatchStatus.REJECTED_AT_WAREHOUSE:  "red",
    BatchStatus.VOIDED:                 "grey",
}


@admin.register(ProduceCollection)
class ProduceCollectionAdmin(admin.ModelAdmin):
    list_display  = [
        "farmer_batch_code", "staff",
        "commodity_type", "quantity_kg",
        "collection_date", "batch_status_badge",
    ]
    list_filter   = ["batch_status", "commodity_type", "collection_date"]
    search_fields = [
        "farmer_batch_code", "staff__staff_id",
        "staff__full_name",
    ]
    readonly_fields = [
        "farmer_batch_code", "created_at",
        "warehouse_intake", "voided_by",
    ]
    ordering      = ["-collection_date", "-created_at"]
    fieldsets     = (
        ("Collection", {
            "fields": (
                "staff", "session", "farmer", "farm_plot",
                "commodity_type", "quantity_kg",
                "collection_date",
                "collection_gps_lat", "collection_gps_lng",
                "collection_notes",
            ),
        }),
        ("Batch", {
            "fields": ("farmer_batch_code", "batch_status", "warehouse_intake"),
        }),
        ("Void", {
            "fields": ("voided_reason", "voided_by"),
            "classes": ("collapse",),
        }),
    )

    def batch_status_badge(self, obj):
        return format_html(
            '<span style="color:{}; font-weight:bold">{}</span>',
            _BATCH_STATUS_COLOURS.get(obj.batch_status, "grey"),
            obj.get_batch_status_display(),
        )
    batch_status_badge.short_description = "Batch Status"

    def has_delete_permission(self, request, obj=None):
        return False  # Collections are never deleted — voided instead


# ─────────────────────────────────────────────────────────────────────────────
# OPERATIONAL — KPI SNAPSHOT
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(StaffKPISnapshot)
class StaffKPISnapshotAdmin(admin.ModelAdmin):
    list_display  = [
        "staff", "period_type", "period_start", "period_end",
        "farmers_registered", "total_kg_collected",
        "data_quality_score", "overall_performance_index",
        "computed_at",
    ]
    list_filter   = ["period_type", "period_end"]
    search_fields = ["staff__staff_id", "staff__full_name"]
    readonly_fields = [
        "farmers_registered", "farms_profiled", "sessions_completed",
        "total_kg_collected", "avg_kg_per_farmer", "batches_generated",
        "data_quality_score", "overall_performance_index", "computed_at",
    ]
    ordering      = ["-period_end"]

    def has_add_permission(self, request):
        return False  # KPI snapshots are computed by tasks, never manually created

    def has_delete_permission(self, request, obj=None):
        return False  # Historical KPI records are permanent
        
        
"""