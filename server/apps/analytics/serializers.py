"""
apps/analytics/serializers.py  —  FarmicleGrow-Trace Platform

Read-only serializers for the analytics API.
All serializers are flat dict / list shapes — no model writes here.
"""
from __future__ import annotations

from rest_framework import serializers

from apps.core.serializers import BaseModelSerializer
from .models import PlatformSnapshot, RegionalSummary


# =============================================================================
# PLATFORM SNAPSHOT
# =============================================================================

class PlatformSnapshotSerializer(serializers.ModelSerializer):
    """Full platform KPI snapshot — used by the main dashboard endpoint."""

    last_refreshed_ago = serializers.SerializerMethodField()

    class Meta:
        model  = PlatformSnapshot
        fields = [
            # Farmers
            "total_farmers", "verified_farmers", "female_farmers",
            "farmers_this_month", "verification_rate_pct", "women_empowerment_pct",
            # Farms
            "total_farms", "total_area_ha", "avg_farm_area_ha",
            # Supply chain
            "total_batches", "total_weight_kg", "total_trace_records",
            "exported_shipments", "destination_countries",
            # Commerce
            "total_orders", "orders_this_month",
            "total_revenue_ghs", "revenue_this_month",
            "total_buyers", "avg_order_value_ghs",
            # Staff
            "active_field_officers", "total_farm_visits", "total_produce_kg",
            # Meta
            "last_refreshed_at", "last_refreshed_ago",
        ]
        read_only_fields = fields

    def get_last_refreshed_ago(self, obj) -> str | None:
        if not obj.last_refreshed_at:
            return None
        from django.utils import timezone
        delta = timezone.now() - obj.last_refreshed_at
        minutes = int(delta.total_seconds() // 60)
        if minutes < 1:   return "just now"
        if minutes < 60:  return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:    return f"{hours}h ago"
        return f"{hours // 24}d ago"


# =============================================================================
# REGIONAL SUMMARY
# =============================================================================

class RegionalSummarySerializer(BaseModelSerializer):
    """Monthly regional KPI row — used for regional breakdown and trend charts."""

    verification_rate_pct = serializers.SerializerMethodField()
    women_pct             = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        model  = RegionalSummary
        fields = [
            "id", "region", "year", "month",
            "farmer_count", "verified_count", "female_count", "new_farmers_mtd",
            "verification_rate_pct", "women_pct",
            "farm_count", "total_area_ha",
            "batch_count", "total_weight_kg",
            "trace_records", "exported_count",
            "order_count", "revenue_ghs",
            "officer_count", "visit_count", "produce_kg",
            "created_at",
        ]
        read_only_fields = fields

    def get_verification_rate_pct(self, obj) -> float:
        return obj.verification_rate_pct

    def get_women_pct(self, obj) -> float:
        return obj.women_pct


# =============================================================================
# TIME-SERIES  (plain serializers — no model backing)
# =============================================================================

class MonthlyTrendSerializer(serializers.Serializer):
    """Generic monthly trend point — wraps any time-series dict from services."""
    month  = serializers.DateTimeField()
    value  = serializers.FloatField(required=False)
    count  = serializers.IntegerField(required=False)


class FarmerTrendSerializer(serializers.Serializer):
    month        = serializers.DateTimeField()
    new_farmers  = serializers.IntegerField()
    verified     = serializers.IntegerField()
    female       = serializers.IntegerField()


class SupplyChainTrendSerializer(serializers.Serializer):
    month             = serializers.DateTimeField()
    batch_count       = serializers.IntegerField()
    total_kg          = serializers.FloatField()
    farmer_batches    = serializers.IntegerField()
    warehouse_batches = serializers.IntegerField()
    product_batches   = serializers.IntegerField()


class RevenueTrendSerializer(serializers.Serializer):
    month           = serializers.DateTimeField()
    total_revenue   = serializers.FloatField()
    order_count     = serializers.IntegerField()
    avg_order_value = serializers.FloatField()


class ExportDestinationSerializer(serializers.Serializer):
    export_destination_country = serializers.CharField()
    shipments                  = serializers.IntegerField()
    total_kg                   = serializers.FloatField()
    farmers                    = serializers.IntegerField()
    products                   = serializers.IntegerField()


class StaffPerformanceSerializer(serializers.Serializer):
    id             = serializers.UUIDField()
    first_name     = serializers.CharField()
    last_name      = serializers.CharField()
    region         = serializers.CharField()
    farmer_count   = serializers.IntegerField()
    verified_count = serializers.IntegerField()
    visit_count    = serializers.IntegerField()
    produce_kg     = serializers.FloatField()


class QualityMetricsSerializer(serializers.Serializer):
    avg_moisture = serializers.FloatField()
    avg_impurity = serializers.FloatField()
    min_moisture = serializers.FloatField()
    max_moisture = serializers.FloatField()
    batch_count  = serializers.IntegerField()
    total_kg     = serializers.FloatField()


class BuyerEngagementSerializer(serializers.Serializer):
    total_buyers   = serializers.IntegerField()
    repeat_buyers  = serializers.IntegerField()
    repeat_rate_pct = serializers.FloatField()
    total_reviews  = serializers.IntegerField()
    avg_rating     = serializers.FloatField()