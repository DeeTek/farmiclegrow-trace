"""
apps/core/serializers.py  —  FarmicleGrow-Trace Platform

Base serializer classes for all 6 domain apps.

FIX vs uploaded
───────────────
  ─ BaseWriteSerializer.create() / update(): called _meta.get_fields() on
    every save — expensive reflection per row. Now cached via @lru_cache.
  ─ RoleBasedSerializer._get_allowed_fields(): accessed user.staff_profile
    as bare attribute — raises AttributeError if profile doesn't exist.
    Now uses getattr(..., None) with try/except.

Serializer classes
──────────────────
  BaseModelSerializer          read: id, timestamps, is_active, created_ago
  BaseWriteSerializer          write: strips auto-managed fields, injects audit
  RoleBasedSerializer          role-scoped field visibility
  GeoSerializer                lat/lon/polygon with range validation
  VerificationStatusSerializer read: verification status block
  VerificationActionSerializer write: verify/reject/suspend actions
  StatusTransitionSerializer   write: validated status transition
  TraceabilityBaseSerializer   base for all trace scan serializers
  PublicTraceSerializer        buyer-safe QR scan output (no PII)
  BulkOperationSerializer      validate bulk create/update payloads
  ShortCodeSerializer          code + absolute URL read-only block
  PhoneField                   normalises Ghanaian phone to E.164
  GhanaCardField               validates GHA-XXXXXXXXX-N format
  CurrencyField                decimal with GHS default
  PaginatedResponseSerializer  standardised list envelope
  ErrorResponseSerializer      standardised error body
  BulkResultSerializer         bulk operation result envelope
  KPIBlockSerializer           dashboard KPI card schema
"""
from __future__ import annotations

from functools import lru_cache
from django.utils import timezone
from rest_framework import serializers


# =============================================================================
# CACHE HELPER  (FIX: was called on every save in original)
# =============================================================================

@lru_cache(maxsize=256)
def _get_model_field_names(model_class) -> frozenset:
    """
    Return frozenset of all field names for a model class.
    Cached per class — avoids expensive _meta.get_fields() on every save().
    """
    return frozenset(f.name for f in model_class._meta.get_fields())


# =============================================================================
# 1.  BASE MODEL SERIALIZER  (read)
# =============================================================================

class BaseModelSerializer(serializers.ModelSerializer):
    """
    Standard read serializer.
    Adds: created_ago human-readable, read-only id/timestamps/is_active.
    """
    created_ago = serializers.SerializerMethodField(read_only=True)

    class Meta:
        read_only_fields = ["id", "created_at", "updated_at", "is_active"]

    def get_created_ago(self, obj) -> str:
        if not hasattr(obj, "created_at") or not obj.created_at:
            return ""
        delta = timezone.now() - obj.created_at
        days  = delta.days
        if days == 0:
            hours   = delta.seconds // 3600
            minutes = (delta.seconds % 3600) // 60
            if hours > 0:   return f"{hours}h ago"
            if minutes > 0: return f"{minutes}m ago"
            return "just now"
        if days < 7:   return f"{days}d ago"
        if days < 30:  return f"{days // 7}w ago"
        if days < 365: return f"{days // 30}mo ago"
        return f"{days // 365}yr ago"


# =============================================================================
# 2.  BASE WRITE SERIALIZER
# =============================================================================

class BaseWriteSerializer(serializers.ModelSerializer):
    """
    Base for create/update operations.
    Strips auto-managed fields; injects created_by/updated_by from request.

    FIX: _meta.get_fields() now cached via @lru_cache — was called per save.
    """

    class Meta:
        exclude = ["id", "created_at", "updated_at", "deleted_at", "is_active"]

    def _model_fields(self) -> frozenset:
        return _get_model_field_names(self.Meta.model)

    def create(self, validated_data):
        request = self.context.get("request")
        if request and getattr(request, "user", None) and request.user.is_authenticated:
            fields = self._model_fields()
            if "created_by" in fields:
                validated_data.setdefault("created_by", request.user)
            if "updated_by" in fields:
                validated_data["updated_by"] = request.user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context.get("request")
        if request and getattr(request, "user", None) and request.user.is_authenticated:
            if "updated_by" in self._model_fields():
                validated_data["updated_by"] = request.user
        return super().update(instance, validated_data)


# =============================================================================
# 3.  ROLE-BASED SERIALIZER
# =============================================================================

class RoleBasedSerializer(BaseModelSerializer):
    """
    Exposes different fields per requesting user's role.

    FIX vs uploaded:
      _get_allowed_fields() accessed user.staff_profile without guard —
      raises AttributeError for users without a staff_profile.
      Now uses getattr(..., None) + try/except.
    """

    BUYER_FIELDS   : list | str = []
    OFFICER_FIELDS : list | str = []
    HR_FIELDS      : list | str = []
    ADMIN_FIELDS   : list | str = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if not request:
            return
        allowed = self._get_allowed_fields(request.user)
        if allowed == "__all__":
            return
        allowed_set = set(allowed)
        for field_name in set(self.fields.keys()) - allowed_set:
            self.fields.pop(field_name, None)

    def _get_allowed_fields(self, user):
        if not user or not user.is_authenticated:
            return self.BUYER_FIELDS
        if user.is_staff or user.is_superuser:
            return self.ADMIN_FIELDS
        if hasattr(user, "field_officer_profile"):
            return self.OFFICER_FIELDS
        if hasattr(user, "staff_profile"):
            # FIX: was bare attribute access → AttributeError for users without profile
            try:
                staff = user.staff_profile
                if getattr(staff, "is_hr", False) or getattr(staff, "is_management", False):
                    return self.HR_FIELDS
            except Exception:
                pass
        if hasattr(user, "buyer_profile"):
            return self.BUYER_FIELDS
        return self.BUYER_FIELDS


# =============================================================================
# 4.  GEO SERIALIZER
# =============================================================================

class GeoSerializer(serializers.Serializer):
    """Read/write for GeoModel fields with coordinate range validation."""

    latitude            = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    longitude           = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    altitude_meters     = serializers.DecimalField(max_digits=7, decimal_places=2, required=False, allow_null=True)
    gps_accuracy_meters = serializers.FloatField(required=False, allow_null=True)
    polygon_coordinates = serializers.JSONField(required=False, allow_null=True)

    def validate_latitude(self, value):
        if value is not None and not (-90 <= float(value) <= 90):
            raise serializers.ValidationError("Latitude must be between -90 and 90.")
        return value

    def validate_longitude(self, value):
        if value is not None and not (-180 <= float(value) <= 180):
            raise serializers.ValidationError("Longitude must be between -180 and 180.")
        return value

    def validate_polygon_coordinates(self, value):
        if value is None:
            return value
        from apps.core.utils import validate_polygon
        is_valid, err = validate_polygon(value)
        if not is_valid:
            raise serializers.ValidationError(f"Invalid polygon: {err}")
        return value

    def validate(self, attrs):
        lat = attrs.get("latitude")
        lon = attrs.get("longitude")
        if (lat is None) != (lon is None):
            raise serializers.ValidationError("latitude and longitude must be provided together.")
        return attrs


# =============================================================================
# 5.  VERIFICATION SERIALIZERS
# =============================================================================

class VerificationStatusSerializer(serializers.Serializer):
    """Read-only block for any VerifiableModel instance."""
    verification_status = serializers.CharField(read_only=True)
    verified_at         = serializers.DateTimeField(read_only=True, allow_null=True)
    rejection_reason    = serializers.CharField(read_only=True)
    is_verified         = serializers.SerializerMethodField()
    is_pending          = serializers.SerializerMethodField()

    def get_is_verified(self, obj) -> bool: return getattr(obj, "is_verified", False)
    def get_is_pending(self, obj)  -> bool: return getattr(obj, "is_pending", False)


class VerificationActionSerializer(serializers.Serializer):
    """Write serializer for verify/reject/suspend admin actions."""
    action           = serializers.ChoiceField(choices=["verify", "reject", "suspend"])
    rejection_reason = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs.get("action") in ("reject", "suspend") and not attrs.get("rejection_reason", "").strip():
            raise serializers.ValidationError(
                {"rejection_reason": "A reason is required for reject/suspend."}
            )
        return attrs


# =============================================================================
# 6.  STATUS TRANSITION SERIALIZER
# =============================================================================

class StatusTransitionSerializer(serializers.Serializer):
    """Validates a status transition for StatusModel subclasses."""
    new_status = serializers.CharField()
    note       = serializers.CharField(required=False, allow_blank=True)

    def validate_new_status(self, value):
        valid = self.context.get("valid_transitions", [])
        if valid and value not in valid:
            raise serializers.ValidationError(
                f"Invalid transition. Valid: {valid}"
            )
        return value


# =============================================================================
# 7.  TRACEABILITY SERIALIZERS
# =============================================================================

class TraceabilityBaseSerializer(BaseModelSerializer):
    """Base for all trace record serializers. Provides scan_url."""

    trace_code           = serializers.CharField(read_only=True)
    farmer_batch_code    = serializers.CharField(read_only=True)
    warehouse_batch_code = serializers.CharField(read_only=True)
    product_batch_code   = serializers.CharField(read_only=True)
    status               = serializers.CharField(read_only=True)
    scan_url             = serializers.SerializerMethodField()

    class Meta(BaseModelSerializer.Meta):
        fields = [
            "trace_code", "farmer_batch_code", "warehouse_batch_code",
            "product_batch_code", "status", "scan_url", "created_at", "created_ago",
        ]

    def get_scan_url(self, obj) -> str:
        request = self.context.get("request")
        code    = getattr(obj, "trace_code", "")
        if request:
            return request.build_absolute_uri(f"/api/v1/trace/{code}/")
        return f"/api/v1/trace/{code}/"


class PublicTraceSerializer(TraceabilityBaseSerializer):
    """Buyer-safe scan output — no PII, no internal data."""

    farmer_code     = serializers.SerializerMethodField()
    farmer_region   = serializers.SerializerMethodField()
    farmer_district = serializers.SerializerMethodField()
    farm_code       = serializers.SerializerMethodField()
    product_name    = serializers.SerializerMethodField()
    harvest_date    = serializers.DateField(read_only=True, allow_null=True)

    def get_farmer_code(self, obj)     -> str: return getattr(getattr(obj, "farmer", None), "code", "")
    def get_farmer_region(self, obj)   -> str: return getattr(getattr(obj, "farmer", None), "region", "")
    def get_farmer_district(self, obj) -> str: return getattr(getattr(obj, "farmer", None), "district", "")
    def get_farm_code(self, obj)       -> str: return getattr(getattr(obj, "farm", None), "code", "")
    def get_product_name(self, obj)    -> str: return getattr(getattr(obj, "product", None), "name", "")


# =============================================================================
# 8.  BULK OPERATION SERIALIZER
# =============================================================================

class BulkOperationSerializer(serializers.Serializer):
    """Validates bulk create/update array payloads."""
    records = serializers.ListField(
        child=serializers.DictField(),
        min_length=1,
        max_length=100,
    )

    def validate_records(self, records):
        if not records:
            raise serializers.ValidationError("At least one record is required.")
        return records


# =============================================================================
# 9.  CODE + URL FIELD
# =============================================================================

class ShortCodeSerializer(serializers.Serializer):
    """Read-only code + absolute API URL block."""
    code     = serializers.CharField(read_only=True)
    code_url = serializers.SerializerMethodField()

    def get_code_url(self, obj) -> str:
        request = self.context.get("request")
        code    = getattr(obj, "code", "") or ""
        model   = obj.__class__.__name__.lower()
        path    = f"/api/v1/{model}s/{code}/"
        return request.build_absolute_uri(path) if request else path


# =============================================================================
# 10.  CUSTOM FIELDS
# =============================================================================

class CodeField(serializers.CharField):
    """Read-only field for auto-generated codes."""
    def __init__(self, **kwargs):
        kwargs.setdefault("read_only", True)
        kwargs.setdefault("help_text", "Auto-generated unique identifier code.")
        super().__init__(**kwargs)


class PhoneField(serializers.CharField):
    """Normalises Ghanaian phone numbers to E.164 on input."""
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        from apps.core.utils import normalise_phone
        normalised = normalise_phone(value)
        if not normalised.startswith("+"):
            raise serializers.ValidationError(
                "Phone must be E.164 format (e.g. +233241234567)."
            )
        return normalised


class GhanaCardField(serializers.CharField):
    """Validates Ghana Card format: GHA-XXXXXXXXX-N"""
    def to_internal_value(self, data):
        import re
        value = super().to_internal_value(data).strip().upper()
        if not re.match(r"^GHA-[A-Z0-9]{9}-\d$", value):
            raise serializers.ValidationError(
                "Ghana Card must match format: GHA-XXXXXXXXX-N (e.g. GHA-123456789-0)"
            )
        return value


class CurrencyField(serializers.DecimalField):
    """Decimal with GHS currency defaults."""
    def __init__(self, **kwargs):
        kwargs.setdefault("max_digits", 14)
        kwargs.setdefault("decimal_places", 2)
        super().__init__(**kwargs)


# =============================================================================
# 11.  RESPONSE ENVELOPE SERIALIZERS
# =============================================================================

class PaginatedResponseSerializer(serializers.Serializer):
    """Standardised paginated list response envelope."""
    count       = serializers.IntegerField()
    page        = serializers.IntegerField()
    page_size   = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    next        = serializers.URLField(allow_null=True)
    previous    = serializers.URLField(allow_null=True)
    results     = serializers.ListField()


class ErrorResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()
    code   = serializers.CharField(required=False)
    field  = serializers.CharField(required=False)


class BulkResultSerializer(serializers.Serializer):
    created     = serializers.IntegerField(required=False)
    updated     = serializers.IntegerField(required=False)
    failed      = serializers.IntegerField()
    created_ids = serializers.ListField(required=False)
    errors      = serializers.ListField()


class KPIBlockSerializer(serializers.Serializer):
    """Standardised KPI block for dashboard cards."""
    label = serializers.CharField()
    total = serializers.IntegerField()
    mtd   = serializers.IntegerField(required=False)
    ytd   = serializers.IntegerField(required=False)
    trend = serializers.DictField(required=False)