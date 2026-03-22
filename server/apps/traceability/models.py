"""
apps/traceability/models.py  —  FarmicleGrow-Trace Platform

End-to-end supply-chain traceability from smallholder farm to export buyer.

Models:
  Batch            Raw produce batch collected by a field officer
  WarehouseIntake  Reception and QC record at warehouse
  TraceRecord      Immutable QR-scannable farm-to-buyer chain record
  Certification    Compliance certification attached to a trace record

Manager / QuerySet strategy:
  BatchQuerySet and TraceabilityQuerySet live in apps.core.querysets
  (Section 10 and 9 respectively) and are imported directly.
  The traceability/querysets.py file only defines build_chain() — a
  standalone helper — so there is no split between model manager and
  view-level queryset methods.

SRD MODULE 5 coverage:
  ✓ Farmer → Farm → Batch (farmer batch code)
  ✓ Batch → Warehouse Intake (warehouse batch code)
  ✓ Warehouse → Processing Batch (product batch code)
  ✓ QR code system — scan reveals buyer-safe public data or full admin chain
  ✓ Certification chain (organic, FairTrade, ISO 22000, etc.)
  ✓ Export destination tracking
  ✓ Recall capability via TraceStatus.RECALLED
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models.abstract import BaseModel, CodedModel, GeoModel, StatusModel
from apps.core.models.base import BaseTracedModel

# Managers and QuerySets live in core — single source of truth
from apps.core.models.querysets import BatchQuerySet, TraceabilityQuerySet


class BatchManager(models.Manager):
    def get_queryset(self):
        return BatchQuerySet(self.model, using=self._db)


class TraceabilityManager(models.Manager):
    def get_queryset(self):
        return TraceabilityQuerySet(self.model, using=self._db)


# =============================================================================
# BATCH
# =============================================================================

class Batch(BaseModel, CodedModel, StatusModel):
    """
    Raw produce batch collected in the field by a field officer.

    Three batch types mirror the physical supply chain:
      farmer    → collected at farm gate by field officer
      warehouse → aggregated from multiple farmer batches at warehouse
      product   → final processed and packaged batch ready for export

    SRD MODULE 3: every produce collection gets a unique batch code which
    becomes the farmer_batch_code on the TraceRecord. The QR code embeds
    the full chain: farmer code → batch → warehouse → product.
    """

    CODE_PREFIX = "BCH"

    class BatchType(models.TextChoices):
        FARMER    = "farmer",    _("Farmer Batch")
        WAREHOUSE = "warehouse", _("Warehouse Batch")
        PRODUCT   = "product",   _("Product Batch")

    class BatchStatus(models.TextChoices):
        ACTIVE     = "active",     _("Active")
        PROCESSING = "processing", _("Processing")
        EXPORTED   = "exported",   _("Exported")
        REJECTED   = "rejected",   _("Rejected")
        CANCELLED  = "cancelled",  _("Cancelled")

    STATUS_CHOICES = BatchStatus.choices
    status = models.CharField(
        max_length=15, choices=BatchStatus.choices,
        default=BatchStatus.ACTIVE, db_index=True,
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    farmer = models.ForeignKey(
        "farmers.Farmer", on_delete=models.PROTECT,
        related_name="batches", null=True, blank=True,
    )
    farm = models.ForeignKey(
        "farmers.Farm", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="batches",
    )
    product = models.ForeignKey(
        "farmers.Product", on_delete=models.PROTECT,
        related_name="batches", null=True, blank=True,
    )
    collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="batches",
    )
    parent_batch = models.ForeignKey(
        "self", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="child_batches",
        help_text=_("For warehouse/product batches aggregated from farmer batches."),
    )

    # ── Identity ───────────────────────────────────────────────────────────────
    batch_code = models.CharField(
        max_length=50, unique=True, blank=True, db_index=True,
        help_text=_("Auto-generated unique batch identifier."),
    )
    batch_type = models.CharField(
        max_length=12, choices=BatchType.choices,
        default=BatchType.FARMER, db_index=True,
    )
    weight_kg       = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    collection_date = models.DateField(null=True, blank=True)
    harvest_date    = models.DateField(null=True, blank=True)
    collection_location = models.CharField(
        max_length=255, blank=True,
        help_text=_("GPS or named location where produce was collected."),
    )

    # ── Quality ────────────────────────────────────────────────────────────────
    moisture_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    impurity_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    grade        = models.CharField(max_length=20, blank=True)
    notes        = models.TextField(blank=True)

    objects = BatchManager()

    class Meta(BaseModel.Meta):
        verbose_name = _("Batch")
        indexes = [
            models.Index(fields=["batch_type", "status"]),
            models.Index(fields=["farmer",     "batch_type"]),
        ]

    def save(self, *args, **kwargs):
        if not self.batch_code:
            from apps.core.utils import generate_batch_code
            self.batch_code = generate_batch_code()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.batch_code} [{self.batch_type}] {self.weight_kg} kg"


# =============================================================================
# WAREHOUSE INTAKE
# =============================================================================

class WarehouseIntake(BaseTracedModel, GeoModel):
    """
    Reception and QC record when a batch arrives at the warehouse.

    GeoModel records the warehouse GPS location at intake time.
    QC measurements (moisture, impurity, grade) are recorded here.
    Rejection at this stage prevents the batch from proceeding to processing.

    FK is `batch` (not warehouse_batch) — consistent with Batch model naming.
    `status` (not intake_status) — consistent with every other model.
    """

    CODE_PREFIX = "WI"

    class IntakeStatus(models.TextChoices):
        RECEIVED  = "received",  _("Received")
        UNDER_QC  = "under_qc",  _("Under QC")
        PASSED    = "passed",    _("Passed QC")
        REJECTED  = "rejected",  _("Rejected")
        PROCESSED = "processed", _("Processed")

    batch = models.OneToOneField(
        Batch, on_delete=models.PROTECT, related_name="warehouse_intake",
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="warehouse_intakes",
    )
    status     = models.CharField(
        max_length=15, choices=IntakeStatus.choices,
        default=IntakeStatus.RECEIVED, db_index=True,
    )
    received_at        = models.DateTimeField(auto_now_add=True, db_index=True)
    warehouse_name     = models.CharField(max_length=200, blank=True)
    warehouse_location = models.CharField(max_length=255, blank=True)
    total_weight_kg    = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"),
        help_text=_("Gross weight at intake."),
    )
    net_weight_kg = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text=_("Net weight after deducting impurities/moisture."),
    )
    moisture_pct     = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    impurity_pct     = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    grade_assigned   = models.CharField(max_length=20, blank=True)
    qc_report        = models.TextField(
        blank=True,
        help_text=_("Full QC narrative report from the warehouse manager."),
    )
    rejection_reason = models.TextField(blank=True)
    processing_notes = models.TextField(blank=True)

    class Meta(BaseTracedModel.Meta):
        verbose_name = _("Warehouse Intake")

    def __str__(self) -> str:
        return f"{self.code} — {self.warehouse_name} [{self.status}]"


# =============================================================================
# TRACE RECORD
# =============================================================================

class TraceRecord(BaseModel, CodedModel, StatusModel):
    """
    Immutable QR-scannable chain record — one per exported product lot.

    Denormalises all three batch codes so the QR scan resolves with a
    single indexed lookup (no joins at scan time).

    Status is forward-only except RECALLED.
    CHAIN_STATUSES is an alias for TraceStatus.choices — used by
    TraceStatusUpdateSerializer.status ChoiceField.

    SRD MODULE 5:
      Farmer code + batch number → product → traceability code → QR code
      Buyer scans → sees farmer general data, batch info, certifications
      Admin/officer scans → sees full chain including officer name, GPS, etc.
    """

    CODE_PREFIX = "TRC"

    class TraceStatus(models.TextChoices):
        ACTIVE       = "active",       _("Active")
        IN_TRANSIT   = "in_transit",   _("In Transit")
        AT_WAREHOUSE = "at_warehouse", _("At Warehouse")
        PROCESSING   = "processing",   _("Processing")
        EXPORTED     = "exported",     _("Exported")
        DELIVERED    = "delivered",    _("Delivered")
        RECALLED     = "recalled",     _("Recalled")
        CANCELLED    = "cancelled",    _("Cancelled")

    STATUS_CHOICES = TraceStatus.choices
    CHAIN_STATUSES = TraceStatus.choices   # alias for serializer ChoiceField

    status = models.CharField(
        max_length=15, choices=TraceStatus.choices,
        default=TraceStatus.ACTIVE, db_index=True,
    )

    # ── Chain links ────────────────────────────────────────────────────────────
    farmer = models.ForeignKey(
        "farmers.Farmer", on_delete=models.PROTECT, related_name="trace_records",
    )
    farm = models.ForeignKey(
        "farmers.Farm", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="trace_records",
    )
    product = models.ForeignKey(
        "farmers.Product", on_delete=models.PROTECT, related_name="trace_records",
    )
    field_officer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="trace_records",
    )
    warehouse_intake = models.ForeignKey(
        WarehouseIntake, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="trace_records",
    )

    # ── Denormalised batch codes (indexed for O(1) QR lookup) ─────────────────
    trace_code           = models.CharField(max_length=50, unique=True, blank=True, db_index=True)
    farmer_batch_code    = models.CharField(max_length=50, blank=True, db_index=True)
    warehouse_batch_code = models.CharField(max_length=50, blank=True, db_index=True)
    product_batch_code   = models.CharField(max_length=50, blank=True, db_index=True)

    # ── Harvest / export data ──────────────────────────────────────────────────
    weight_kg                  = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    harvest_date               = models.DateField(null=True, blank=True)
    export_destination_country = models.CharField(max_length=100, blank=True, db_index=True)
    export_date                = models.DateField(null=True, blank=True)
    notes                      = models.TextField(blank=True)
    qr_code_image              = models.ImageField(
        upload_to="trace/qr/%Y/%m/", null=True, blank=True,
        help_text=_("Generated QR code image for physical labelling."),
    )

    objects = TraceabilityManager()

    class Meta(BaseModel.Meta):
        verbose_name = _("Trace Record")
        indexes = [
            models.Index(fields=["trace_code"]),
            models.Index(fields=["farmer_batch_code"]),
            models.Index(fields=["product_batch_code"]),
            models.Index(fields=["status", "export_destination_country"]),
        ]

    def save(self, *args, **kwargs):
        if not self.trace_code:
            from apps.core.utils import generate_trace_code
            self.trace_code = generate_trace_code()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        product_name = self.product.name if self.product_id else ""
        return f"{self.trace_code} — {product_name} [{self.status}]"

    @property
    def chain_complete(self) -> bool:
        """True when all three batch code tiers are populated."""
        return bool(
            self.farmer_batch_code
            and self.warehouse_batch_code
            and self.product_batch_code
        )


# =============================================================================
# CERTIFICATION
# =============================================================================

class Certification(BaseModel):
    """
    Compliance certification attached to a trace record.

    SRD MODULE 5 / MODULE 7: buyers can download certifications from the
    product QR page. is_valid checks both approval status and expiry.
    """

    class CertType(models.TextChoices):
        ORGANIC    = "organic",    _("Organic")
        FAIRTRADE  = "fairtrade",  _("FairTrade")
        RAINFOREST = "rainforest", _("Rainforest Alliance")
        GLOBAL_GAP = "global_gap", _("GlobalG.A.P.")
        ISO_22000  = "iso_22000",  _("ISO 22000")
        FSSC_22000 = "fssc_22000", _("FSSC 22000")
        UTZ        = "utz",        _("UTZ Certified")
        OTHER      = "other",      _("Other")

    class CertStatus(models.TextChoices):
        PENDING  = "pending",  _("Pending Review")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        EXPIRED  = "expired",  _("Expired")

    trace_record = models.ForeignKey(
        TraceRecord, on_delete=models.CASCADE, related_name="certifications",
    )
    cert_type    = models.CharField(max_length=20, choices=CertType.choices, db_index=True)
    cert_number  = models.CharField(max_length=100, blank=True)
    issued_by    = models.CharField(max_length=200, blank=True)
    issued_date  = models.DateField(null=True, blank=True)
    expiry_date  = models.DateField(null=True, blank=True)
    status       = models.CharField(
        max_length=10, choices=CertStatus.choices,
        default=CertStatus.PENDING, db_index=True,
    )
    document     = models.FileField(           # named `document` to match serializer
        upload_to="certifications/%Y/", null=True, blank=True,
        help_text=_("Uploaded certificate file (PDF)."),
    )
    notes        = models.TextField(blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = _("Certification")

    @property
    def is_valid(self) -> bool:
        from django.utils import timezone
        if self.status != self.CertStatus.APPROVED:
            return False
        if self.expiry_date and timezone.now().date() > self.expiry_date:
            return False
        return True

    def __str__(self) -> str:
        return f"{self.cert_type} — {self.trace_record.trace_code}"