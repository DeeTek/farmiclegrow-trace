"""apps/reports/admin.py"""
from django.contrib import admin
from apps.core.admin import StatusModelAdmin
from .models import Report, ReportSchedule


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display  = ["title", "report_type", "status", "output_format",
                     "row_count", "file_size_display", "requested_by", "queued_at"]
    list_filter   = ["status", "report_type", "output_format"]
    search_fields = ["title", "report_type"]
    readonly_fields = ["status", "row_count", "file_size_bytes", "error_message",
                       "queued_at", "started_at", "completed_at"]

    @admin.action(description="Re-generate selected reports")
    def regenerate(self, request, queryset):
        from .views import _generate_report_sync
        for report in queryset:
            _generate_report_sync(report)
        self.message_user(request, f"{queryset.count()} report(s) regenerated.")


@admin.register(ReportSchedule)
class ReportScheduleAdmin(admin.ModelAdmin):
    list_display  = ["title", "report_type", "frequency", "is_enabled",
                     "next_run_at", "last_run_at"]
    list_filter   = ["report_type", "frequency", "is_enabled"]
    search_fields = ["title"]