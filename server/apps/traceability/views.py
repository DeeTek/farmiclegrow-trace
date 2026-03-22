"""
apps/traceability/views.py  —  FarmicleGrow-Trace Platform

ViewSets and views:
  BatchViewSet            — raw produce batch CRUD + trace link + assign
  WarehouseIntakeViewSet  — warehouse reception + QC accept/reject
  TraceRecordViewSet      — full chain CRUD + status update + certify + analytics
  QRScanView              — GET /api/v1/scan/<code>/  public QR code resolver

QuerySet architecture:
  All queryset methods (with_full_chain, for_public_scan, status_pipeline,
  destination_summary, weight_by_region, etc.) are defined in
  apps.core.models.querysets and attached to the models via BatchManager
  and TraceabilityManager in models.py.

  traceability/querysets.py provides only build_chain() — a standalone
  function that assembles the JSON dict for QR scan responses.

QR scan endpoint:
  QRScanView lives here because it is a traceability-domain concern:
  it resolves trace_code, farmer_batch_code, and product_batch_code —
  all of which are fields on TraceRecord. It imports only from this app's
  own models and serializers, so there is no circular import risk.
  The URL /api/v1/scan/<code>/ is registered in the root urls.py pointing
  at this view.

Signal pattern:
  status_changed is fired directly for all status transitions.
  model_changed is emitted automatically by core's post_save signal —
  no manual call needed.
"""
from __future__ import annotations

import logging

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models.mixins import (
    AuditCreateMixin,
    DateRangeFilterMixin,
    SoftDeleteMixin,
    _get_user_role,
)
from apps.core.signals import status_changed

from .models import Batch, WarehouseIntake, TraceRecord, Certification
from .querysets import build_chain
from .serializers import (
    AdminTraceSerializer,
    BatchListSerializer,
    BatchSerializer,
    BatchWriteSerializer,
    CertificationSerializer,
    CertificationWriteSerializer,
    PublicTraceSerializer,
    TraceRecordListSerializer,
    TraceRecordWriteSerializer,
    TraceStatusUpdateSerializer,
    WarehouseIntakeSerializer,
    WarehouseIntakeWriteSerializer,
)

logger = logging.getLogger("apps.traceability.views")


# =============================================================================
# PERMISSIONS
# =============================================================================

class IsWarehouseManagerOrAdmin(permissions.BasePermission):
    """Warehouse managers and super-admins only."""

    def has_permission(self, request, view):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        role = getattr(request.user, "role", None)
        return request.user.is_authenticated and (
            request.user.is_superuser
            or role in (User.Role.WAREHOUSE_MANAGER, User.Role.SUPER_ADMIN)
        )


class IsFieldOfficerOrAdmin(permissions.BasePermission):
    """Field officers and super-admins only."""

    def has_permission(self, request, view):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        role = getattr(request.user, "role", None)
        return request.user.is_authenticated and (
            request.user.is_superuser
            or role in (User.Role.FIELD_OFFICER, User.Role.SUPER_ADMIN)
        )


# =============================================================================
# QR CODE SCAN
# =============================================================================

class QRScanView(APIView):
    """
    GET /api/v1/scan/<code>/

    Resolves any FarmicleGrow QR code to its traceability record.
    Accepts: trace_code, farmer_batch_code, product_batch_code.

    Public endpoint — no authentication required for buyer-facing scans.
    Admins, officers, and HR staff can pass ?format=full for the complete record.

    Query params:
        format = "full"    (admin / officer / hr only — bypasses cache)
                 "public"  (default — anonymised, cached 2 minutes per code)

    Response (public):
        {
          "trace_code":   "TRC-GH-2025-83920",
          "farmer_code":  "FMR-AS-12345",
          "product_name": "Shea Butter",
          "region":       "Ashanti",
          "status":       "exported"
        }
    """

    permission_classes = [AllowAny]
    CACHE_TIMEOUT      = 120   # seconds
    FULL_ACCESS_ROLES  = {"admin", "officer", "hr"}

    def get(self, request, code: str):
        code = (code or "").strip().upper()
        if len(code) < 3:
            return Response(
                {"detail": "A valid QR code is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        role     = _get_user_role(request.user)
        fmt      = request.query_params.get("format", "public")
        use_full = fmt == "full" and role in self.FULL_ACCESS_ROLES

        # Serve from cache for public scans — QR scans are read-heavy
        cache_key = f"qr_scan:{code}"
        if not use_full:
            cached = cache.get(cache_key)
            if cached is not None:
                return Response(cached, status=status.HTTP_200_OK)

        try:
            record = (
                TraceRecord.objects
                .select_related("farmer", "farm", "product", "field_officer")
                .get(
                    Q(trace_code__iexact=code)
                    | Q(farmer_batch_code__iexact=code)
                    | Q(product_batch_code__iexact=code),
                    is_active=True,
                )
            )

            serializer_cls = AdminTraceSerializer if use_full else PublicTraceSerializer
            data           = serializer_cls(record, context={"request": request}).data

            if not use_full:
                cache.set(cache_key, data, timeout=self.CACHE_TIMEOUT)

            return Response(data, status=status.HTTP_200_OK)

        except TraceRecord.DoesNotExist:
            pass
        except Exception as exc:
            logger.error("qr_scan | code=%s error=%s", code, exc, exc_info=True)

        return Response(
            {"detail": f"No traceability record found for code: {code}"},
            status=status.HTTP_404_NOT_FOUND,
        )


# =============================================================================
# BATCH VIEWSET
# =============================================================================

class BatchViewSet(
    AuditCreateMixin,
    SoftDeleteMixin,
    DateRangeFilterMixin,
    viewsets.ModelViewSet,
):
    """
    Endpoint                         Method  Permission
    ──────────────────────────────────────────────────────
    /v1/batches/                     GET     authenticated
    /v1/batches/                     POST    field officer / admin
    /v1/batches/<id>/                GET     authenticated
    /v1/batches/<id>/                PATCH   field officer / admin
    /v1/batches/<id>/                DELETE  admin
    /v1/batches/<id>/trace/          GET     authenticated
    /v1/batches/<id>/assign/         POST    admin

    QuerySet methods used:
      .with_full_chain()  — core.querysets.TraceabilityQuerySet
      .farmer_batches()   — core.querysets.BatchQuerySet
    """

    queryset = Batch.objects.all().select_related(
        "farmer", "farm", "product", "collected_by", "parent_batch"
    )
    serializer_class  = BatchSerializer
    filter_backends   = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields  = ["batch_type", "status", "farmer__region"]
    search_fields     = ["batch_code", "code", "farmer__code", "farmer__first_name"]
    ordering_fields   = ["collection_date", "weight_kg", "created_at"]
    ordering          = ["-created_at"]
    date_range_field  = "collection_date"

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update"):
            return [permissions.IsAuthenticated(), IsFieldOfficerOrAdmin()]
        if self.action in ("destroy", "assign"):
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == "list":
            return BatchListSerializer
        if self.action in ("create", "update", "partial_update"):
            return BatchWriteSerializer
        return BatchSerializer

    def perform_create(self, serializer):
        batch = serializer.save(collected_by=self.request.user)
        status_changed.send(
            sender     = Batch,
            instance   = batch,
            old_status = None,
            new_status = batch.status,
        )

    # ── Trace link ────────────────────────────────────────────────────────────

    @action(detail=True, methods=["get"])
    def trace(self, request, pk=None):
        """GET /v1/batches/<id>/trace/ — full chain for this batch."""
        batch  = self.get_object()
        record = (
            TraceRecord.objects
            .with_full_chain()
            .filter(farmer_batch_code=batch.batch_code)
            .first()
        )
        if not record:
            return Response(
                {"detail": "No trace record linked to this batch."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(AdminTraceSerializer(record, context={"request": request}).data)

    # ── Assign to TraceRecord ─────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def assign(self, request, pk=None):
        """
        POST /v1/batches/<id>/assign/
        Link batch to a TraceRecord, populating the correct batch code tier
        based on batch_type (farmer / warehouse / product).
        Body: { "trace_record_id": "<uuid>" }
        """
        batch    = self.get_object()
        trace_id = request.data.get("trace_record_id")
        if not trace_id:
            raise ValidationError({"trace_record_id": "Required."})

        try:
            record = TraceRecord.objects.get(pk=trace_id)
        except TraceRecord.DoesNotExist:
            raise NotFound("TraceRecord not found.")

        update_fields = []
        if batch.batch_type == Batch.BatchType.FARMER:
            record.farmer_batch_code = batch.batch_code
            update_fields.append("farmer_batch_code")
        elif batch.batch_type == Batch.BatchType.WAREHOUSE:
            record.warehouse_batch_code = batch.batch_code
            update_fields.append("warehouse_batch_code")
        elif batch.batch_type == Batch.BatchType.PRODUCT:
            record.product_batch_code = batch.batch_code
            update_fields.append("product_batch_code")

        if update_fields:
            record.save(update_fields=update_fields)

        # Invalidate QR scan cache
        cache.delete(f"qr_scan:{record.trace_code.upper()}")

        return Response(
            AdminTraceSerializer(record, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


# =============================================================================
# WAREHOUSE INTAKE VIEWSET
# =============================================================================

class WarehouseIntakeViewSet(
    AuditCreateMixin,
    SoftDeleteMixin,
    viewsets.ModelViewSet,
):
    """
    Endpoint                                   Method  Permission
    ───────────────────────────────────────────────────────────────
    /v1/warehouse-intakes/                     GET     authenticated
    /v1/warehouse-intakes/                     POST    warehouse manager / admin
    /v1/warehouse-intakes/<id>/                GET     authenticated
    /v1/warehouse-intakes/<id>/                PATCH   warehouse manager / admin
    /v1/warehouse-intakes/<id>/accept/         POST    warehouse manager / admin
    /v1/warehouse-intakes/<id>/reject/         POST    warehouse manager / admin
    """

    queryset = WarehouseIntake.objects.all().select_related(
        "batch", "batch__farmer", "received_by"
    )
    serializer_class = WarehouseIntakeSerializer
    filter_backends  = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status", "batch__batch_type"]
    search_fields    = ["batch__batch_code", "batch__farmer__code"]
    ordering_fields  = ["received_at", "created_at"]
    ordering         = ["-created_at"]

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "accept", "reject"):
            return [permissions.IsAuthenticated(), IsWarehouseManagerOrAdmin()]
        return [permissions.IsAuthenticated()]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return WarehouseIntakeWriteSerializer
        return WarehouseIntakeSerializer

    def perform_create(self, serializer):
        intake = serializer.save(received_by=self.request.user)
        status_changed.send(
            sender     = WarehouseIntake,
            instance   = intake,
            old_status = None,
            new_status = intake.status,
        )

    # ── QC Accept ─────────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        """POST /v1/warehouse-intakes/<id>/accept/ — QC passed."""
        intake = self.get_object()
        if intake.status not in (
            WarehouseIntake.IntakeStatus.RECEIVED,
            WarehouseIntake.IntakeStatus.UNDER_QC,
        ):
            raise ValidationError(
                {"detail": f"Cannot accept intake with status '{intake.status}'."}
            )
        old_status    = intake.status
        intake.status = WarehouseIntake.IntakeStatus.PASSED
        intake.save(update_fields=["status"])
        status_changed.send(
            sender     = WarehouseIntake,
            instance   = intake,
            old_status = old_status,
            new_status = intake.status,
        )
        return Response(WarehouseIntakeSerializer(intake).data)

    # ── QC Reject ─────────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """POST /v1/warehouse-intakes/<id>/reject/ — QC failed."""
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError({"reason": "Rejection reason is required."})

        intake = self.get_object()
        if intake.status == WarehouseIntake.IntakeStatus.PROCESSED:
            raise ValidationError(
                {"detail": "Cannot reject an already processed intake."}
            )
        old_status              = intake.status
        intake.status           = WarehouseIntake.IntakeStatus.REJECTED
        intake.rejection_reason = reason
        intake.save(update_fields=["status", "rejection_reason"])
        status_changed.send(
            sender     = WarehouseIntake,
            instance   = intake,
            old_status = old_status,
            new_status = intake.status,
        )
        return Response(WarehouseIntakeSerializer(intake).data)


# =============================================================================
# TRACE RECORD VIEWSET
# =============================================================================

class TraceRecordViewSet(
    AuditCreateMixin,
    SoftDeleteMixin,
    DateRangeFilterMixin,
    viewsets.ModelViewSet,
):
    """
    Endpoint                                     Method  Permission
    ──────────────────────────────────────────────────────────────────
    /v1/trace-records/                           GET     authenticated
    /v1/trace-records/                           POST    admin
    /v1/trace-records/<id>/                      GET     authenticated
    /v1/trace-records/<id>/                      PATCH   admin
    /v1/trace-records/<id>/                      DELETE  admin
    /v1/trace-records/<id>/update-status/        POST    admin
    /v1/trace-records/<id>/chain/                GET     authenticated
    /v1/trace-records/<id>/certify/              POST    admin
    /v1/trace-records/pipeline/                  GET     authenticated
    /v1/trace-records/destination-summary/       GET     authenticated

    QuerySet methods used (all from core.querysets.TraceabilityQuerySet):
      .with_full_chain()      — select_related + prefetch
      .for_public_scan()      — active_chain + verified + .only()
      .status_pipeline()      — aggregate by status
      .destination_summary()  — exported records by country + weight
    """

    queryset         = TraceRecord.objects.all()
    serializer_class = AdminTraceSerializer
    filter_backends  = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = [
        "status",
        "farmer__region",
        "export_destination_country",
        "farmer__verification_status",
    ]
    search_fields = [
        "trace_code",
        "farmer_batch_code",
        "product_batch_code",
        "farmer__code",
        "farmer__first_name",
        "farmer__last_name",
    ]
    ordering_fields  = ["created_at", "harvest_date", "weight_kg"]
    ordering         = ["-created_at"]
    date_range_field = "created_at"

    def get_permissions(self):
        if self.action in (
            "create", "update", "partial_update", "destroy",
            "update_status", "certify",
        ):
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == "list":
            return TraceRecordListSerializer
        if self.action in ("create", "update", "partial_update"):
            return TraceRecordWriteSerializer
        return AdminTraceSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
          return TraceRecord.objects.none()
        qs = TraceRecord.objects.with_full_chain()
        if not (self.request.user.is_staff or self.request.user.is_superuser):
            qs = qs.for_public_scan()
        return qs

    # ── Status transition ─────────────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="update-status")
    @transaction.atomic
    def update_status(self, request, pk=None):
        """POST /v1/trace-records/<id>/update-status/"""
        record        = self.get_object()
        ser           = TraceStatusUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        old_status    = record.status
        update_fields = ["status"]
        record.status = ser.validated_data["status"]

        if ser.validated_data.get("destination_country"):
            record.export_destination_country = ser.validated_data["destination_country"]
            update_fields.append("export_destination_country")

        if ser.validated_data.get("note"):
            existing     = record.notes or ""
            record.notes = f"{existing}\n[{record.status}] {ser.validated_data['note']}".strip()
            update_fields.append("notes")

        record.save(update_fields=update_fields)

        # Invalidate QR scan cache
        cache.delete(f"qr_scan:{record.trace_code.upper()}")

        status_changed.send(
            sender     = TraceRecord,
            instance   = record,
            old_status = old_status,
            new_status = record.status,
        )

        return Response(AdminTraceSerializer(record, context={"request": request}).data)

    # ── Full chain view ───────────────────────────────────────────────────────

    @action(detail=True, methods=["get"])
    def chain(self, request, pk=None):
        """GET /v1/trace-records/<id>/chain/ — full structured chain dict."""
        record = self.get_object()
        return Response(build_chain(record))

    # ── Certification ─────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def certify(self, request, pk=None):
        """POST /v1/trace-records/<id>/certify/ — attach a certification."""
        record = self.get_object()
        ser    = CertificationWriteSerializer(
            data    = request.data,
            context = {"request": request},
        )
        ser.is_valid(raise_exception=True)
        cert = ser.save(trace_record=record)
        return Response(
            CertificationSerializer(cert).data,
            status=status.HTTP_201_CREATED,
        )

    # ── Pipeline analytics ────────────────────────────────────────────────────

    @action(detail=False, methods=["get"])
    def pipeline(self, request):
        """
        GET /v1/trace-records/pipeline/
        Status counts for admin pipeline dashboard.
        """
        return Response(self.get_queryset().status_pipeline())

    @action(detail=False, methods=["get"], url_path="destination-summary")
    def destination_summary(self, request):
        """
        GET /v1/trace-records/destination-summary/
        Export volumes by destination country + weight totals.
        """
        return Response(self.get_queryset().destination_summary())
