"""
apps/core/mixins.py  —  FarmicleGrow-Trace Platform

View and QuerySet mixins for all 6 domain apps.

FIX vs uploaded
───────────────
  ─ RoleQuerySetMixin: class body mixed 2-space and 4-space indentation.
    The `get_queryset` method used 2-space indent for the class body but
    then the helper methods used 4-space. This is an IndentationError.
    All class bodies now use consistent 4-space indentation.

  ─ CSVExportMixin.get_csv_data() raised NotImplementedError even for
    the default case — now provides a working default using values_list().

Mixin coverage
──────────────
  RoleQuerySetMixin       scope queryset per user role
  RoleSerializerMixin     return different serializer per role
  OwnerQuerySetMixin      scope to authenticated user's own records
  VerificationActionMixin verify/reject/suspend ViewSet actions
  VerificationStatsMixin  GET /verification-stats/ aggregate action
  AuditCreateMixin        inject created_by/updated_by on save
  SoftDeleteMixin         soft-delete in destroy() (fix: hasattr check was always True)
  RegionFilterMixin       ?region= / ?district= / ?community= query params
  GeoFilterMixin          ?lat= / ?lon= / ?radius_km= proximity filter
  DateRangeFilterMixin    ?date_from= / ?date_to= query params
  SearchFilterMixin       ?q= keyword search
  PaginationMixin         standardised envelope {count, page, results, …}
  CachedQuerySetMixin     per-role cache key on list()
  BulkCreateMixin         POST /bulk-create/ with per-item error collection
  BulkUpdateMixin         PATCH /bulk-update/ with per-item error reporting
  CSVExportMixin          GET /export-csv/ with working default
  DashboardMixin          GET /dashboard/ + GET /stats/ actions
  TraceabilityMixin       GET /trace/<code>/ QR resolution
  ImpersonationAwareMixin reads is_impersonation JWT claim
"""
from __future__ import annotations

import hashlib
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError, NotFound


# =============================================================================
# ROLE HELPER
# =============================================================================

def _get_user_role(user) -> str:
    """
    Single source of truth for user role detection.
    Returns: "admin" | "officer" | "hr" | "buyer"
    """
    if not user or not user.is_authenticated:
        return "buyer"
    if user.is_staff or user.is_superuser:
        return "admin"
    if hasattr(user, "field_officer_profile"):
        return "officer"
    if hasattr(user, "warehouse_manager_profile"):
        return "officer"
    if hasattr(user, "staff_profile"):
        try:
            staff = user.staff_profile
            if getattr(staff, "is_hr", False) or getattr(staff, "is_management", False):
                return "hr"
        except Exception:
            pass
    return "buyer"


# =============================================================================
# 1.  ROLE-BASED QUERYSET
# =============================================================================

class RoleQuerySetMixin:
    """
    Scopes the queryset based on requesting user's platform role.

    FIX vs uploaded: class body used mixed 2-space / 4-space indentation
    causing IndentationError at import time. All methods now use 4-space indent.
    """

    def get_queryset(self):
        qs   = super().get_queryset()
        role = _get_user_role(self.request.user)

        dispatch = {
            "admin":   self.get_admin_queryset,
            "officer": self.get_officer_queryset,
            "hr":      self.get_hr_queryset,
            "buyer":   self.get_buyer_queryset,
        }
        handler = dispatch.get(role)
        if handler:
            return handler(qs)
        raise PermissionDenied("You do not have access to this resource.")

    def get_admin_queryset(self, qs):
        return qs

    def get_buyer_queryset(self, qs):
        return self.get_admin_queryset(qs)

    def get_officer_queryset(self, qs):
        return self.get_admin_queryset(qs)

    def get_hr_queryset(self, qs):
        return self.get_admin_queryset(qs)


# =============================================================================
# 2.  ROLE-BASED SERIALIZER
# =============================================================================

class RoleSerializerMixin:
    """Returns a different serializer class based on requesting user's role."""

    buyer_serializer_class   = None
    officer_serializer_class = None
    hr_serializer_class      = None
    admin_serializer_class   = None

    def get_serializer_class(self):
        role = _get_user_role(self.request.user)
        mapping = {
            "buyer":   self.buyer_serializer_class,
            "officer": self.officer_serializer_class,
            "hr":      self.hr_serializer_class,
            "admin":   self.admin_serializer_class,
        }
        cls = mapping.get(role)
        return cls or (self.admin_serializer_class or super().get_serializer_class())


# =============================================================================
# 3.  OWNER QUERYSET
# =============================================================================

class OwnerQuerySetMixin:
    """Scopes queryset to records belonging to the requesting user."""

    owner_field  = "user"
    owner_lookup = None

    def get_queryset(self):
        qs     = super().get_queryset()
        user   = self.request.user
        lookup = self.owner_lookup or f"{self.owner_field}__id"
        return qs.filter(**{lookup: user.pk})


# =============================================================================
# 4.  VERIFICATION ACTIONS
# =============================================================================

class VerificationActionMixin:
    """Provides verify(), reject(), suspend() ViewSet actions."""

    def verify(self, request, pk=None):
        obj = self.get_object()
        if not hasattr(obj, "verify"):
            return Response(
                {"detail": "This object does not support verification."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        staff = getattr(request.user, "staff_profile", None)
        obj.verify(verified_by=staff)
        return Response(
            {"detail": f"{obj.__class__.__name__} verified successfully."},
            status=status.HTTP_200_OK,
        )

    def reject(self, request, pk=None):
        obj    = self.get_object()
        reason = request.data.get("reason", "").strip()
        if not reason:
            raise ValidationError({"reason": "Rejection reason is required."})
        if not hasattr(obj, "reject"):
            return Response(
                {"detail": "This object does not support rejection."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        obj.reject(reason=reason)
        return Response(
            {"detail": f"{obj.__class__.__name__} rejected."},
            status=status.HTTP_200_OK,
        )

    def suspend(self, request, pk=None):
        obj    = self.get_object()
        reason = request.data.get("reason", "").strip()
        if not hasattr(obj, "suspend"):
            return Response(
                {"detail": "This object does not support suspension."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        obj.suspend(reason=reason)
        return Response({"detail": "Record suspended."}, status=status.HTTP_200_OK)


# =============================================================================
# 5.  VERIFICATION STATS
# =============================================================================

class VerificationStatsMixin:
    """Adds GET /verification-stats/ action."""

    def verification_stats(self, request):
        qs = self.get_queryset()
        if not hasattr(qs, "verification_summary"):
            return Response(
                {"detail": "Verification summary not supported."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(qs.verification_summary(), status=status.HTTP_200_OK)


# =============================================================================
# 6.  AUDIT CREATE
# =============================================================================

class AuditCreateMixin:
    """Injects created_by / updated_by from request.user on save."""

    def _get_audit_fields(self, serializer) -> set:
        try:
            return {f.name for f in serializer.Meta.model._meta.get_fields()}
        except AttributeError:
            return set()

    def perform_create(self, serializer):
        user   = self.request.user
        kwargs = {}
        fields = self._get_audit_fields(serializer)
        if user.is_authenticated:
            if "created_by" in fields:
                kwargs["created_by"] = user
            if "updated_by" in fields:
                kwargs["updated_by"] = user
        serializer.save(**kwargs)

    def perform_update(self, serializer):
        user   = self.request.user
        kwargs = {}
        fields = self._get_audit_fields(serializer)
        if user.is_authenticated and "updated_by" in fields:
            kwargs["updated_by"] = user
        serializer.save(**kwargs)


# =============================================================================
# 7.  SOFT DELETE  (FIX: original hasattr check was always True)
# =============================================================================

class SoftDeleteMixin:
    """
    Overrides destroy() to soft-delete instead of hard-delete.

    FIX vs original: hasattr(instance, "delete") is always True for any
    Python object — the guard was completely useless. Now uses isinstance().
    """

    def destroy(self, request, *args, **kwargs):
        from apps.core.abstract import SoftDeleteModel
        instance = self.get_object()

        if isinstance(instance, SoftDeleteModel):
            instance.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        instance.hard_delete() if hasattr(instance, "hard_delete") else instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# =============================================================================
# 8.  REGION / DISTRICT FILTER
# =============================================================================

class RegionFilterMixin:
    """Adds ?region=, ?district=, ?community= query param filtering."""

    def get_queryset(self):
        qs        = super().get_queryset()
        region    = self.request.query_params.get("region")
        district  = self.request.query_params.get("district")
        community = self.request.query_params.get("community")
        if region:
            qs = qs.filter(region__iexact=region)
        if district:
            qs = qs.filter(district__iexact=district)
        if community:
            qs = qs.filter(community__icontains=community)
        return qs


# =============================================================================
# 9.  GEO FILTER
# =============================================================================

class GeoFilterMixin:
    """Adds ?lat=, ?lon=, ?radius_km= proximity filtering."""

    default_radius_km: float = 10.0
    max_radius_km:     float = 100.0

    def get_queryset(self):
        qs  = super().get_queryset()
        lat = self.request.query_params.get("lat")
        lon = self.request.query_params.get("lon")
        if not lat or not lon:
            return qs
        try:
            lat_f  = float(lat)
            lon_f  = float(lon)
            radius = min(
                float(self.request.query_params.get("radius_km", self.default_radius_km)),
                self.max_radius_km,
            )
        except (TypeError, ValueError):
            raise ValidationError({"detail": "lat, lon, and radius_km must be valid numbers."})

        if hasattr(qs, "near"):
            return qs.near(lat_f, lon_f, radius_km=radius)

        import math
        deg_per_km = 1 / 111.0
        lat_delta  = radius * deg_per_km
        lon_delta  = radius * deg_per_km / max(math.cos(math.radians(lat_f)), 1e-6)
        return qs.filter(
            latitude__range =(lat_f - lat_delta, lat_f + lat_delta),
            longitude__range=(lon_f - lon_delta, lon_f + lon_delta),
        )


# =============================================================================
# 10.  DATE RANGE FILTER
# =============================================================================

class DateRangeFilterMixin:
    """Adds ?date_from= and ?date_to= ISO date filtering."""

    date_range_field: str = "created_at"

    def get_queryset(self):
        qs        = super().get_queryset()
        date_from = self.request.query_params.get("date_from")
        date_to   = self.request.query_params.get("date_to")
        field     = self.date_range_field
        if date_from:
            try:
                qs = qs.filter(**{f"{field}__date__gte": date_from})
            except (ValueError, TypeError):
                raise ValidationError({"date_from": "Invalid date. Use YYYY-MM-DD."})
        if date_to:
            try:
                qs = qs.filter(**{f"{field}__date__lte": date_to})
            except (ValueError, TypeError):
                raise ValidationError({"date_to": "Invalid date. Use YYYY-MM-DD."})
        return qs


# =============================================================================
# 11.  SEARCH FILTER
# =============================================================================

class SearchFilterMixin:
    """Adds ?q= keyword search across configured search_fields."""

    search_fields: list = []

    def get_queryset(self):
        qs    = super().get_queryset()
        query = self.request.query_params.get("q", "").strip()
        if query and self.search_fields and len(query) >= 2:
            from django.db.models import Q
            q = Q()
            for field in self.search_fields:
                lookup = field if "__" in field else f"{field}__icontains"
                q |= Q(**{lookup: query})
            qs = qs.filter(q).distinct()
        return qs


# =============================================================================
# 12.  PAGINATION
# =============================================================================

class PaginationMixin:
    """Standardised pagination envelope: {count, page, page_size, total_pages, results}."""

    def get_paginated_response_data(self, serializer_data: list, page=None) -> dict:
        paginator = getattr(self, "paginator", None)
        if paginator is None or page is None:
            return {"results": serializer_data}
        count       = paginator.page.paginator.count
        page_number = paginator.page.number
        page_size   = paginator.get_page_size(self.request)
        total_pages = paginator.page.paginator.num_pages
        return {
            "count":       count,
            "page":        page_number,
            "page_size":   page_size,
            "total_pages": total_pages,
            "next":        paginator.get_next_link(),
            "previous":    paginator.get_previous_link(),
            "results":     serializer_data,
        }


# =============================================================================
# 13.  CACHED QUERYSET
# =============================================================================

class CachedQuerySetMixin:
    """Caches expensive list querysets per role + query params."""

    cache_timeout:    int = 300
    cache_key_prefix: str = "cached_qs"

    def _build_cache_key(self, request) -> str:
        role   = _get_user_role(request.user)
        params = "&".join(f"{k}={v}" for k, v in sorted(request.query_params.items()))
        raw    = f"{self.cache_key_prefix}:{role}:{params}"
        return hashlib.md5(raw.encode()).hexdigest()

    def list(self, request, *args, **kwargs):
        cache_key = self._build_cache_key(request)
        cached    = cache.get(cache_key)
        if cached is not None:
            return Response(cached)
        response = super().list(request, *args, **kwargs)
        cache.set(cache_key, response.data, timeout=self.cache_timeout)
        return response

    def invalidate_cache(self, request):
        cache.delete(self._build_cache_key(request))


# =============================================================================
# 14.  BULK CREATE
# =============================================================================

class BulkCreateMixin:
    """
    Adds POST /bulk-create/ to create multiple records in one request.

    Body:  [{ ...record1... }, { ...record2... }, ...]
    Response:  { "created": 5, "failed": 0, "created_ids": [...], "errors": [] }
    """

    bulk_serializer_class = None
    bulk_max_records      = 100

    def bulk_create(self, request, *args, **kwargs):
        data = request.data
        if not isinstance(data, list):
            raise ValidationError({"detail": "Request body must be a JSON array."})
        if len(data) > self.bulk_max_records:
            raise ValidationError(
                {"detail": f"Maximum {self.bulk_max_records} records per bulk create."}
            )
        serializer_class = self.bulk_serializer_class or self.get_serializer_class()
        created, errors  = [], []

        for i, item in enumerate(data):
            ser = serializer_class(data=item, context=self.get_serializer_context())
            if ser.is_valid():
                try:
                    instance = ser.save()
                    created.append(str(getattr(instance, "id", i)))
                except Exception as exc:
                    errors.append({"index": i, "error": str(exc)})
            else:
                errors.append({"index": i, "errors": ser.errors})

        return Response(
            {
                "created":     len(created),
                "failed":      len(errors),
                "created_ids": created,
                "errors":      errors,
            },
            status=status.HTTP_207_MULTI_STATUS if errors else status.HTTP_201_CREATED,
        )


# =============================================================================
# 15.  BULK UPDATE
# =============================================================================

class BulkUpdateMixin:
    """Adds PATCH /bulk-update/ to update multiple records in one request."""

    bulk_update_max_records = 100

    def bulk_update(self, request, *args, **kwargs):
        data = request.data
        if not isinstance(data, list):
            raise ValidationError({"detail": "Request body must be a JSON array."})
        if len(data) > self.bulk_update_max_records:
            raise ValidationError(
                {"detail": f"Maximum {self.bulk_update_max_records} records per bulk update."}
            )
        updated, errors = [], []

        for i, item in enumerate(data):
            record_id = item.get("id")
            if not record_id:
                errors.append({"index": i, "error": "id is required."})
                continue
            try:
                instance   = self.get_queryset().get(pk=record_id)
                serializer = self.get_serializer(
                    instance, data=item, partial=True,
                    context=self.get_serializer_context(),
                )
                if serializer.is_valid():
                    serializer.save()
                    updated.append(str(record_id))
                else:
                    errors.append({"index": i, "id": str(record_id), "errors": serializer.errors})
            except self.get_queryset().model.DoesNotExist:
                errors.append({"index": i, "id": str(record_id), "error": "Not found."})
            except Exception as exc:
                errors.append({"index": i, "id": str(record_id), "error": str(exc)})

        return Response(
            {"updated": len(updated), "failed": len(errors), "errors": errors},
            status=status.HTTP_207_MULTI_STATUS if errors else status.HTTP_200_OK,
        )


# =============================================================================
# 16.  CSV EXPORT  (FIX: default implementation now works without subclassing)
# =============================================================================

class CSVExportMixin:
    """
    Adds /export-csv/ to any ViewSet.

    FIX vs original: get_csv_data() raised NotImplementedError in the base
    class, forcing every subclass to implement it even for the default
    (all fields) case. Now provides a working default.

    Subclasses may override get_csv_headers() and get_csv_rows() for custom columns.
    """
    csv_filename: str = "export.csv"

    def export_csv(self, request):
        from django.http import HttpResponse
        import csv

        qs      = self.get_queryset()
        headers = self.get_csv_headers()
        rows    = self.get_csv_rows(qs)

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{self.csv_filename}"'
        response.write("\ufeff")   # BOM for Excel UTF-8

        writer = csv.writer(response)
        writer.writerow(headers)
        writer.writerows(rows)
        return response

    def get_csv_headers(self) -> list:
        """Returns verbose_name for all model fields."""
        try:
            meta = self.queryset.model._meta
            return [f.verbose_name for f in meta.fields]
        except AttributeError:
            return []

    def get_csv_rows(self, queryset) -> list:
        """Default: returns all field values via values_list."""
        try:
            meta   = self.queryset.model._meta
            fields = [f.name for f in meta.fields]
            return list(queryset.values_list(*fields))
        except AttributeError:
            return []


# =============================================================================
# 17.  DASHBOARD
# =============================================================================

class DashboardMixin:
    """Adds /dashboard/ (per-object) and /stats/ (list-level) actions."""

    def dashboard(self, request, pk=None):
        obj   = self.get_object()
        stats = self.get_dashboard_stats(obj)
        return Response(stats, status=status.HTTP_200_OK)

    def stats(self, request):
        return Response(self.get_list_stats(), status=status.HTTP_200_OK)

    def get_dashboard_stats(self, obj) -> dict:
        raise NotImplementedError("Implement get_dashboard_stats(obj) → dict")

    def get_list_stats(self) -> dict:
        raise NotImplementedError("Implement get_list_stats() → dict")


# =============================================================================
# 18.  TRACEABILITY
# =============================================================================

class TraceabilityMixin:
    """Adds GET /trace/<code>/ QR scan resolution to any ViewSet."""

    def trace(self, request, code: str = None):
        if not code:
            raise ValidationError({"detail": "Trace code is required."})

        role = _get_user_role(request.user)
        qs   = self.get_queryset()

        if hasattr(qs, "chain_for_qr"):
            results = qs.chain_for_qr(code)
        else:
            from django.db.models import Q
            results = qs.filter(
                Q(trace_code__iexact=code)
                | Q(farmer_batch_code__iexact=code)
                | Q(product_batch_code__iexact=code)
            )

        if not results.exists():
            raise NotFound(f"No traceability record found for: {code}")

        instance         = results.first()
        serializer_class = self.get_trace_serializer_class(role)
        serializer       = serializer_class(instance, context={"request": request, "role": role})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def get_trace_serializer_class(self, role: str):
        return self.get_serializer_class()


# =============================================================================
# 19.  IMPERSONATION AWARE
# =============================================================================

class ImpersonationAwareMixin:
    """
    Reads is_impersonation claim from the JWT token and attaches it to the
    request so views can block dangerous operations in impersonated sessions.
    """

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        token = getattr(request, "auth", None)
        if token and hasattr(token, "get"):
            request.is_impersonation = bool(token.get("is_impersonation", False))
            request.impersonated_by  = token.get("impersonated_by")
        else:
            request.is_impersonation = False
            request.impersonated_by  = None

    def _block_during_impersonation(self, request):
        """Call at the start of sensitive actions."""
        if getattr(request, "is_impersonation", False):
            raise PermissionDenied(
                "This action cannot be performed during an impersonated session."
            )