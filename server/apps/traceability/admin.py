"""apps/traceability/admin.py"""
from django.contrib import admin
from django.utils.html import format_html
from apps.core.admin import (
    BaseModelAdmin, CodedModelAdmin, TracedModelAdmin, StatusModelAdmin,
)
from .models import Batch, WarehouseIntake, TraceRecord, Certification


class CertificationInline(admin.TabularInline):
    model  = Certification
    extra  = 0
    fields = ["cert_type", "issued_by", "issued_date", "expiry_date", "is_valid_display"]
    readonly_fields = ["is_valid_display"]

    def is_valid_display(self, obj):
        return "✅" if obj.is_valid else "❌"
    is_valid_display.short_description = "Valid"


@admin.register(Batch)
class BatchAdmin(CodedModelAdmin):
    list_display  = [
        "batch_code", "batch_type", "status_badge_display",
        "farmer", "weight_kg", "collection_date", "active_badge",
    ]
    list_filter   = ["batch_type", "status", "farmer__region"]
    search_fields = ["batch_code", "code", "farmer__code", "farmer__first_name"]
    readonly_fields = ["id", "code", "batch_code", "created_at"]

    def status_badge_display(self, obj):
        from apps.core.admin import STATUS_COLOURS, badge
        return badge(obj.status, STATUS_COLOURS.get(obj.status, "#6c757d"))
    status_badge_display.short_description = "Status"
    status_badge_display.allow_tags = True


@admin.register(WarehouseIntake)
class WarehouseIntakeAdmin(StatusModelAdmin, CodedModelAdmin):
  list_display  = ["code", "warehouse_batch", "status_badge", "total_weight_kg",
                     "moisture_pct", "grade", "received_at"]
  list_filter   = ["status"]
  search_fields = ["code", "warehouse_batch__batch_code"]
  readonly_fields = ["id", "code", "created_at", "updated_at"]
  
  def warehouse_batch(self, obj):
    return obj.batch.batch_code if obj.batch else "-"

  warehouse_batch.short_description = "Batch Code"

  def grade(self, obj):
    return obj.grade_assigned

  grade.short_description = "Grade"

@admin.register(TraceRecord)
class TraceRecordAdmin(TracedModelAdmin, StatusModelAdmin):
  list_display  = ["trace_code", "farmer", "product", "status_badge","export_destination_country", "weight_kg", "qr_link", "active_badge",
    ]
  list_filter   = ["status", "farmer__region", "export_destination_country"]
  search_fields = ["trace_code", "farmer_batch_code", "product_batch_code", "farmer__code", "farmer__first_name",
    ]
  readonly_fields = ["id", "code", "trace_code", "qr_code_image", "created_at"]
  inlines = [CertificationInline]


@admin.register(Certification)
class CertificationAdmin(BaseModelAdmin):
  list_display  = ["cert_type", "issued_by", "issued_date", "expiry_date", "is_valid_display"]
  list_filter   = ["cert_type"]
  search_fields = ["cert_number", "issued_by"]

  def is_valid_display(self, obj): return "✅" if obj.is_valid else "❌"
  is_valid_display.short_description = "Valid"