"""
apps/traceability/querysets.py

Standalone helpers for the traceability app.

QuerySet classes (BatchQuerySet, TraceabilityQuerySet) and their
managers live in apps.core.querysets (Sections 9 and 10) and are
imported by traceability/models.py directly — no redefinition here.

This file provides only:
  build_chain()   — assemble the full structured JSON dict for a TraceRecord
  resolve_qr()    — convenience wrapper around TraceabilityQuerySet.resolve_qr()

Cross-reference with apps.core.querysets:
  TraceabilityQuerySet methods used by views.py:
    .with_full_chain()       → Section 9: select_related + prefetch certifications
    .resolve_qr(code)        → Section 9: Q(trace_code) | Q(farmer_batch_code) | Q(product_batch_code)
    .for_public_scan()       → Section 9: active_chain() + verified farmers + .only()
    .status_pipeline()       → Section 9: aggregate by status
    .destination_summary()   → Section 9: exported records grouped by country + weight totals

  BatchQuerySet methods used by views.py:
    .farmer_batches()        → Section 10
    .warehouse_batches()     → Section 10
    .product_batches()       → Section 10
    .by_code(code)           → Section 10
    .weight_by_officer()     → Section 10  (officer dashboard)
    .weight_by_region()      → Section 10  (regional analytics)

All of these are fully covered by core.querysets. No duplication needed.
"""
from __future__ import annotations

from django.utils import timezone


# =============================================================================
# BUILD CHAIN
# =============================================================================

def build_chain(record) -> dict:
    """
    Assemble the full structured traceability chain dict for a TraceRecord.

    Returns a fully JSON-serialisable dict — no model instances.

    Used by:
      • QRScanView (core/views.py) — cached 2 min per code
      • TraceRecordViewSet.chain action — admin full detail
      • PDF certificate generation

    Public vs admin split is handled by the caller:
      - Public scan: omit farmer.full_name, farmer.phone, farm GPS exact coords
      - Admin scan:  include everything

    The dict is intentionally flat-enough for direct JSON serialisation
    but nested by domain section for readability.
    """
    farmer  = record.farmer
    farm    = record.farm
    product = record.product
    officer = record.field_officer
    intake  = record.warehouse_intake

    return {
        "trace_code":   record.trace_code,
        "status":       record.status,
        "scan_url":     f"/api/v1/scan/{record.trace_code}/",
        "generated_at": timezone.now().isoformat(),

        # ── Farmer ── public-safe subset only (no PII)
        "farmer": {
            "code":          farmer.code     if farmer else None,
            "region":        farmer.region   if farmer else None,
            "district":      farmer.district if farmer else None,
            "community":     farmer.community if farmer else None,
            # full_name and phone are intentionally excluded from public chain
        } if farmer else None,

        # ── Farm
        "farm": {
            "code":            farm.code             if farm else None,
            "area_hectares":   str(farm.area_hectares) if farm else None,
            "region":          farm.region           if farm else None,
            "district":        farm.district         if farm else None,
            "cropping_system": farm.cropping_system  if farm else None,
            # Exact GPS excluded from public chain (approximate region/district shown)
        } if farm else None,

        # ── Batch codes (all three tiers)
        "batch": {
            "farmer_batch_code":    record.farmer_batch_code,
            "warehouse_batch_code": record.warehouse_batch_code,
            "product_batch_code":   record.product_batch_code,
            "chain_complete":       record.chain_complete,
            "weight_kg":            str(record.weight_kg),
            "harvest_date":         str(record.harvest_date) if record.harvest_date else None,
        },

        # ── Product
        "product": {
            "name":           product.name           if product else None,
            "category":       product.category       if product else None,
            "origin_country": product.origin_country if product else None,
            "grade":          product.grade          if product else None,
        },

        # ── Warehouse QC data
        "warehouse": {
            "name":           intake.warehouse_name  if intake else None,
            "location":       intake.warehouse_location if intake else None,
            "moisture_pct":   str(intake.moisture_pct)  if intake and intake.moisture_pct  else None,
            "impurity_pct":   str(intake.impurity_pct)  if intake and intake.impurity_pct  else None,
            "grade_assigned": intake.grade_assigned  if intake else None,
            "qc_report":      intake.qc_report       if intake else None,
        } if intake else None,

        # ── Certifications (approved only)
        "certifications": [
            {
                "cert_type":   c.cert_type,
                "cert_number": c.cert_number,
                "issued_by":   c.issued_by,
                "expiry_date": str(c.expiry_date) if c.expiry_date else None,
                "is_valid":    c.is_valid,
            }
            for c in record.certifications.all()
            if c.status == "approved"
        ],

        # ── Export
        "export": {
            "destination_country": record.export_destination_country,
            "export_date":         str(record.export_date) if record.export_date else None,
        },
    }


# =============================================================================
# RESOLVE QR  (convenience wrapper)
# =============================================================================

def resolve_qr(code: str) -> "TraceRecord | None":
    """
    Convenience function wrapper around TraceabilityQuerySet.resolve_qr().

    Callers in core/views.py can use this directly without importing the
    TraceRecord model at module level (avoids circular imports in core).

        from apps.traceability.querysets import resolve_qr
        record = resolve_qr(code)
    """
    from apps.traceability.models import TraceRecord
    return TraceRecord.objects.resolve_qr(code)