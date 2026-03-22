"""apps/analytics/admin.py"""
from django.contrib import admin
from apps.core.admin import BaseModelAdmin, ImmutableLogAdmin
from .models import PlatformSnapshot, RegionalSummary


@admin.register(PlatformSnapshot)
class PlatformSnapshotAdmin(admin.ModelAdmin):
    list_display  = ["last_refreshed_at", "total_farmers", "verified_farmers",
                     "total_orders", "total_revenue_ghs"]
    readonly_fields = [f.name for f in PlatformSnapshot._meta.get_fields()
                       if hasattr(f, "name") and f.name not in ("id",)]

    def has_add_permission(self, request):    return False
    def has_delete_permission(self, r, o=None): return False

    @admin.action(description="Force refresh snapshot now")
    def refresh_snapshot(self, request, queryset):
        for obj in queryset:
            obj.refresh()
        self.message_user(request, "Snapshot refreshed.")


@admin.register(RegionalSummary)
class RegionalSummaryAdmin(ImmutableLogAdmin):
    list_display  = ["region", "year", "month", "farmer_count",
                     "verified_count", "total_area_ha", "revenue_ghs"]
    list_filter   = ["region", "year"]
    search_fields = ["region"]