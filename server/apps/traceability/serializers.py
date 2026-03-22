"""
apps/traceability/serializers.py  —  FarmicleGrow-Trace Platform

Serializers:
  CertificationSerializer / CertificationWriteSerializer
  BatchListSerializer / BatchSerializer / BatchWriteSerializer
  WarehouseIntakeSerializer / WarehouseIntakeWriteSerializer
  PublicTraceSerializer    — buyer-safe QR scan response (no PII)
  AdminTraceSerializer     — full chain for officers / admin
  TraceRecordListSerializer / TraceRecordWriteSerializer
  QRScanResponseSerializer — polymorphic scan response shape
  TraceStatusUpdateSerializer

Fixes vs previous version:
  • PublicTraceSerializer defined here (was imported from apps.core.serializers
    which doesn't define it — ImportError at startup)
  • TraceabilityBaseSerializer import removed (unused)
  • BatchSerializer fields aligned with actual Batch model fields
  • WarehouseIntakeSerializer uses: batch, status, net_weight_kg, qc_report
    (was warehouse_batch, intake_status — field name mismatches)
  • CertificationWriteSerializer exclude list corrected — removed "product"
    (Certification has no product FK)
  • TraceStatusUpdateSerializer.status uses TraceRecord.CHAIN_STATUSES
    (was TraceRecord.CHAIN_STATUSES which didn't exist — now it does)
"""
from __future__ import annotations

from rest_framework import serializers

from apps.core.serializers import BaseModelSerializer, BaseWriteSerializer
from .models import Batch, WarehouseIntake, TraceRecord, Certification


# =============================================================================
# CERTIFICATION
# =============================================================================

class CertificationSerializer(BaseModelSerializer):
    is_valid = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = Certification
        fields = [
            "id", "cert_type", "cert_number", "issued_by",
            "issued_date", "expiry_date", "document",
            "status", "is_valid", "notes", "created_at",
        ]
        read_only_fields = ["id", "is_valid"]

    def get_is_valid(self, obj) -> bool:
        return obj.is_valid


class CertificationWriteSerializer(BaseWriteSerializer):
    class Meta(BaseWriteSerializer.Meta):
        model   = Certification
        # Only exclude trace_record — that's injected by the view
        # (no product FK on Certification)
        exclude = BaseWriteSerializer.Meta.exclude + ["trace_record"]


# =============================================================================
# BATCH
# =============================================================================

class BatchListSerializer(BaseModelSerializer):
    farmer_name = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = Batch
        fields = [
            "id", "batch_code", "batch_type", "status", "weight_kg",
            "farmer_name", "collection_date", "created_ago",
        ]
        read_only_fields = fields

    def get_farmer_name(self, obj) -> str:
        return obj.farmer.full_name if obj.farmer else ""


class BatchSerializer(BaseModelSerializer):
    farmer_name  = serializers.SerializerMethodField()
    officer_name = serializers.SerializerMethodField()
    parent_code  = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = Batch
        fields = [
            "id", "code", "batch_code", "batch_type", "status", "weight_kg",
            "farmer", "farmer_name",
            "collected_by", "officer_name",
            "collection_date", "collection_location",
            "moisture_pct", "impurity_pct", "grade",
            "parent_batch", "parent_code",
            "harvest_date", "notes",
            "created_at", "created_ago",
        ]

    def get_farmer_name(self, obj) -> str:
        return obj.farmer.full_name if obj.farmer else ""

    def get_officer_name(self, obj) -> str:
        return obj.collected_by.get_full_name() if obj.collected_by else ""

    def get_parent_code(self, obj) -> str:
        return obj.parent_batch.batch_code if obj.parent_batch else ""


class BatchWriteSerializer(BaseWriteSerializer):
    class Meta(BaseWriteSerializer.Meta):
        model   = Batch
        exclude = BaseWriteSerializer.Meta.exclude + ["code", "batch_code"]


# =============================================================================
# WAREHOUSE INTAKE
# =============================================================================

class WarehouseIntakeSerializer(BaseModelSerializer):
    received_by_name = serializers.SerializerMethodField()
    batch_code       = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = WarehouseIntake
        fields = [
            "id", "code",
            "batch", "batch_code",
            "received_at", "received_by", "received_by_name",
            "status",                       # was intake_status — standardised
            "warehouse_name", "warehouse_location",
            "total_weight_kg", "net_weight_kg",
            "moisture_pct", "impurity_pct", "grade_assigned",
            "qc_report", "rejection_reason", "processing_notes",
            "latitude", "longitude",
            "created_at", "created_ago",
        ]

    def get_received_by_name(self, obj) -> str:
        return obj.received_by.get_full_name() if obj.received_by else ""

    def get_batch_code(self, obj) -> str:
        return obj.batch.batch_code if obj.batch_id else ""


class WarehouseIntakeWriteSerializer(BaseWriteSerializer):
    class Meta(BaseWriteSerializer.Meta):
        model   = WarehouseIntake
        exclude = BaseWriteSerializer.Meta.exclude + ["code", "received_by", "received_at"]


# =============================================================================
# TRACE RECORD  — public (buyer-safe) + admin (full chain)
# =============================================================================

class PublicTraceSerializer(BaseModelSerializer):
    """
    Buyer-safe QR scan response — no PII, no officer details.

    SRD MODULE 5 / MODULE 8:
      Buyers see: product name, farmer region, batch codes, harvest date,
      certifications, status. No farmer personal data or GPS coordinates.
    """
    product_name   = serializers.SerializerMethodField()
    farmer_region  = serializers.SerializerMethodField()
    farmer_district = serializers.SerializerMethodField()
    certifications = CertificationSerializer(many=True, read_only=True)
    scan_url       = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = TraceRecord
        fields = [
            "trace_code",
            "farmer_region", "farmer_district",
            "product_name",
            "farmer_batch_code", "product_batch_code",
            "status", "harvest_date", "weight_kg",
            "export_destination_country",
            "certifications",
            "scan_url",
        ]

    def get_product_name(self, obj) -> str:
        return obj.product.name if obj.product_id else ""

    def get_farmer_region(self, obj) -> str:
        return obj.farmer.region if obj.farmer_id else ""

    def get_farmer_district(self, obj) -> str:
        return obj.farmer.district if obj.farmer_id else ""

    def get_scan_url(self, obj) -> str:
        request = self.context.get("request")
        path    = f"/api/v1/scan/{obj.trace_code}/"
        return request.build_absolute_uri(path) if request else path


class AdminTraceSerializer(BaseModelSerializer):
    """
    Full chain including officer name, farmer PII, warehouse data, certifications.
    For admin / field officer access only.
    """
    farmer_name    = serializers.SerializerMethodField()
    farm_code      = serializers.SerializerMethodField()
    product_name   = serializers.SerializerMethodField()
    officer_name   = serializers.SerializerMethodField()
    certifications = CertificationSerializer(many=True, read_only=True)
    scan_url       = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = TraceRecord
        fields = [
            "id", "trace_code",
            "farmer", "farmer_name",
            "farm", "farm_code",
            "product", "product_name",
            "farmer_batch_code", "warehouse_batch_code", "product_batch_code",
            "status", "harvest_date", "weight_kg",
            "export_destination_country", "export_date",
            "field_officer", "officer_name",
            "warehouse_intake",
            "certifications",
            "chain_complete",
            "notes",
            "scan_url",
            "created_at", "created_ago",
        ]

    def get_farmer_name(self, obj) -> str:
        return obj.farmer.full_name if obj.farmer_id else ""

    def get_farm_code(self, obj) -> str:
        return obj.farm.code if obj.farm_id else ""

    def get_product_name(self, obj) -> str:
        return obj.product.name if obj.product_id else ""

    def get_officer_name(self, obj) -> str:
        return obj.field_officer.get_full_name() if obj.field_officer_id else ""

    def get_scan_url(self, obj) -> str:
        request = self.context.get("request")
        path    = f"/api/v1/scan/{obj.trace_code}/"
        return request.build_absolute_uri(path) if request else path


class TraceRecordListSerializer(BaseModelSerializer):
    product_name = serializers.SerializerMethodField()
    farmer_code  = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = TraceRecord
        fields = [
            "id", "trace_code", "farmer_code", "product_name",
            "farmer_batch_code", "product_batch_code",
            "status", "harvest_date", "export_destination_country",
            "chain_complete", "created_ago",
        ]
        read_only_fields = fields

    def get_product_name(self, obj) -> str:
        return obj.product.name if obj.product_id else ""

    def get_farmer_code(self, obj) -> str:
        return obj.farmer.code if obj.farmer_id else ""


class TraceRecordWriteSerializer(BaseWriteSerializer):
    class Meta(BaseWriteSerializer.Meta):
        model   = TraceRecord
        exclude = BaseWriteSerializer.Meta.exclude + [
            "code", "trace_code", "qr_code_image",
        ]


# =============================================================================
# QR SCAN + STATUS UPDATE
# =============================================================================

class QRScanResponseSerializer(serializers.Serializer):
    """Polymorphic QR scan response shape — public or admin based on caller role."""
    trace_code     = serializers.CharField()
    scan_url       = serializers.CharField()
    farmer         = serializers.DictField()
    farm           = serializers.DictField(allow_null=True)
    batch          = serializers.DictField()
    product        = serializers.DictField()
    certifications = serializers.ListField()
    status         = serializers.CharField()
    generated_at   = serializers.CharField()


class TraceStatusUpdateSerializer(serializers.Serializer):
    """
    Input serializer for the update-status action.
    Uses TraceRecord.CHAIN_STATUSES which is aliased to TraceStatus.choices.
    """
    status = serializers.ChoiceField(
        choices=[c[0] for c in TraceRecord.CHAIN_STATUSES]
    )
    note                = serializers.CharField(required=False, allow_blank=True)
    destination_country = serializers.CharField(required=False, allow_blank=True)