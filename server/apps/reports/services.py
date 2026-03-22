"""
apps/reports/services.py  —  FarmicleGrow-Trace Platform

Service functions for report generation.

queue_report()        — create a Report row and enqueue the Celery task
generate_report()     — called by the Celery task; builds the data and writes the file
_build_<type>()       — one builder function per report_type

Design:
  • queue_report() is the only public entry point from views.
  • generate_report() is called exclusively by reports.tasks.generate_report_task.
  • Each _build_*() function returns (rows: list[list], headers: list[str]).
  • File writing (CSV / XLSX / JSON / PDF) is handled by _write_file().
  • All DB queries use the QuerySet methods defined in apps.core.querysets.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date
from typing import Any

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger("apps.reports")


# =============================================================================
# QUEUE REPORT  (called by view)
# =============================================================================

@transaction.atomic
def queue_report(
    report_type: str,
    title: str,
    output_format: str,
    filters: dict,
    requested_by=None,
) -> "Report":
    """
    Create a Report row with status=queued and dispatch the Celery task.

    Uses transaction.on_commit so the task is only queued after the DB
    row commits — prevents the task running before the row is visible.

    Returns the Report instance (pk available immediately for the response).
    """
    from apps.reports.models import Report
    from apps.reports.tasks import generate_report_task

    report = Report.objects.create(
        report_type   = report_type,
        title         = title,
        output_format = output_format,
        filters       = filters,
        requested_by  = requested_by,
        status        = "queued",
    )

    transaction.on_commit(
        lambda: generate_report_task.delay(str(report.pk))
    )

    logger.info(
        "report_queued | pk=%s | type=%s | format=%s | by=%s",
        report.pk, report_type, output_format,
        getattr(requested_by, "pk", "system"),
    )
    return report


# =============================================================================
# GENERATE REPORT  (called by Celery task)
# =============================================================================

def generate_report(report_pk: str) -> None:
    """
    Main entry point for the Celery task.

    Marks the report as generating, calls the appropriate builder,
    writes the output file, then marks it ready or failed.
    """
    from apps.reports.models import Report

    try:
        report = Report.objects.get(pk=report_pk)
    except Report.DoesNotExist:
        logger.error("generate_report | Report not found: pk=%s", report_pk)
        return

    # Mark as generating
    report.status     = "generating"
    report.started_at = timezone.now()
    report.save(update_fields=["status", "started_at"])

    try:
        headers, rows = _dispatch_builder(report)
        file_name, file_content = _write_file(headers, rows, report.output_format)

        report.file = ContentFile(file_content, name=file_name)
        report.row_count        = len(rows)
        report.file_size_bytes  = len(file_content)
        report.status           = "ready"
        report.completed_at     = timezone.now()
        report.error_message    = ""
        report.save(update_fields=[
            "file", "row_count", "file_size_bytes",
            "status", "completed_at", "error_message",
        ])

        logger.info(
            "report_ready | pk=%s | type=%s | rows=%s | size=%s",
            report.pk, report.report_type, len(rows), len(file_content),
        )

    except Exception as exc:
        report.status        = "failed"
        report.completed_at  = timezone.now()
        report.error_message = str(exc)
        report.save(update_fields=["status", "completed_at", "error_message"])
        logger.exception(
            "report_failed | pk=%s | type=%s | error=%s",
            report.pk, report.report_type, exc,
        )
        raise  # re-raise so Celery retries on transient errors


# =============================================================================
# BUILDER DISPATCH
# =============================================================================

_BUILDERS = {
    "farmer_summary":        "_build_farmer_summary",
    "farm_production":       "_build_farm_production",
    "staff_performance":     "_build_staff_performance",
    "traceability_chain":    "_build_traceability_chain",
    "warehouse_utilisation": "_build_warehouse_utilisation",
    "order_summary":         "_build_order_summary",
    "payment_summary":       "_build_payment_summary",
    "buyer_activity":        "_build_buyer_activity",
    "impact_dashboard":      "_build_impact_dashboard",
    "co2_savings":           "_build_co2_savings",
    "women_participation":   "_build_women_participation",
    "product_quality":       "_build_product_quality",
}


def _dispatch_builder(report) -> tuple[list[str], list[list]]:
    builder_name = _BUILDERS.get(report.report_type)
    if not builder_name:
        raise ValueError(f"No builder registered for report_type '{report.report_type}'")
    builder = globals()[builder_name]
    return builder(report.filters)


# =============================================================================
# BUILDERS  — one per report_type
# =============================================================================

def _apply_date_filter(qs, filters: dict, date_field: str = "created_at"):
    if filters.get("date_from"):
        qs = qs.filter(**{f"{date_field}__date__gte": filters["date_from"]})
    if filters.get("date_to"):
        qs = qs.filter(**{f"{date_field}__date__lte": filters["date_to"]})
    return qs


def _build_farmer_summary(filters: dict):
    """
    SRD MODULE 10: Farmer production statistics.
    Columns: Code, Full Name, Gender, Region, District, Community,
             Farms, Total Area (ha), Verification Status, Registered At
    """
    from apps.farmers.models import Farmer

    qs = Farmer.objects.filter(is_active=True).select_related("registered_by")
    qs = _apply_date_filter(qs, filters)
    if filters.get("region"):
        qs = qs.filter(region__iexact=filters["region"])
    if filters.get("district"):
        qs = qs.filter(district__iexact=filters["district"])

    headers = [
        "Farmer Code", "First Name", "Last Name", "Gender",
        "Region", "District", "Community", "Education Level",
        "Land Ownership", "Cooperative",
        "Farm Count", "Total Area (ha)",
        "Verification Status", "Registered By", "Registered At",
    ]
    rows = []
    for f in qs:
        farms      = f.farms.filter(is_active=True)
        farm_count = farms.count()
        total_area = sum(fm.area_hectares for fm in farms)
        rows.append([
            f.code, f.first_name, f.last_name, f.gender,
            f.region, f.district, f.community, f.education_level,
            f.land_ownership, f.cooperative_name,
            farm_count, float(total_area),
            f.verification_status,
            f.registered_by.get_full_name() if f.registered_by else "",
            str(f.created_at.date()),
        ])
    return headers, rows


def _build_farm_production(filters: dict):
    """
    SRD MODULE 10: Farm yield and crop season data.
    """
    from apps.farmers.models import CropSeason

    qs = CropSeason.objects.filter(is_active=True).select_related("farm", "farm__farmer")
    if filters.get("year"):
        qs = qs.filter(harvest_year=int(filters["year"]))
    if filters.get("region"):
        qs = qs.filter(farm__farmer__region__iexact=filters["region"])

    headers = [
        "Season Code", "Farm Code", "Farmer Code", "Region",
        "Crop Variety", "Harvest Year", "Planting Date",
        "Fertilizer Type", "Expected Yield (kg)", "Actual Yield (kg)",
        "Yield Variance (%)", "Labour Type",
    ]
    rows = []
    for s in qs:
        rows.append([
            s.code, s.farm.code, s.farm.farmer.code, s.farm.farmer.region,
            s.crop_variety, s.harvest_year, str(s.planting_date or ""),
            s.fertilizer_type,
            float(s.expected_yield_kg or 0), float(s.actual_yield_kg or 0),
            s.yield_variance_pct or "",
            s.labour_type,
        ])
    return headers, rows


def _build_staff_performance(filters: dict):
    """
    SRD MODULE 10: Staff performance ranking — farmer count, visits, produce.
    """
    from django.contrib.auth import get_user_model
    from django.db.models import Count, Sum
    from django.db.models.functions import Coalesce
    from django.db.models import Value, FloatField

    User = get_user_model()
    qs   = User.objects.filter(
        role__in=[User.Role.FIELD_OFFICER],
        is_active=True,
    )
    if filters.get("region"):
        qs = qs.filter(region__iexact=filters["region"])

    qs = qs.annotate(
        farmer_count=Count("registered_farmers", distinct=True),
        visit_count =Count("farm_visits",        distinct=True),
        produce_kg  =Coalesce(
            Sum("farm_visits__produce_collected_kg"),
            Value(0.0), output_field=FloatField(),
        ),
    ).order_by("-farmer_count")

    headers = [
        "Officer Code", "Full Name", "Role", "Region",
        "Farmers Registered", "Farm Visits", "Produce Collected (kg)",
    ]
    rows = [
        [
            getattr(u, "code", ""), u.get_full_name(), u.role, u.region,
            u.farmer_count, u.visit_count, float(u.produce_kg),
        ]
        for u in qs
    ]
    return headers, rows


def _build_traceability_chain(filters: dict):
    """
    SRD MODULE 10: Full traceability chain summary.
    """
    from apps.traceability.models import TraceRecord

    qs = TraceRecord.objects.filter(is_active=True).select_related(
        "farmer", "product", "field_officer"
    )
    qs = _apply_date_filter(qs, filters, date_field="queued_at")
    if filters.get("status"):
        qs = qs.filter(status=filters["status"])
    if filters.get("region"):
        qs = qs.filter(farmer__region__iexact=filters["region"])

    headers = [
        "Trace Code", "Farmer Code", "Region", "District",
        "Product", "Farmer Batch", "Warehouse Batch", "Product Batch",
        "Status", "Weight (kg)", "Harvest Date",
        "Export Destination", "Export Date", "Chain Complete",
    ]
    rows = [
        [
            r.trace_code,
            r.farmer.code    if r.farmer  else "",
            r.farmer.region  if r.farmer  else "",
            r.farmer.district if r.farmer else "",
            r.product.name   if r.product else "",
            r.farmer_batch_code, r.warehouse_batch_code, r.product_batch_code,
            r.status, float(r.weight_kg),
            str(r.harvest_date or ""),
            r.export_destination_country, str(r.export_date or ""),
            r.chain_complete,
        ]
        for r in qs
    ]
    return headers, rows


def _build_warehouse_utilisation(filters: dict):
    """
    SRD MODULE 10: Warehouse utilisation metrics.
    """
    from apps.traceability.models import WarehouseIntake

    qs = WarehouseIntake.objects.filter(is_active=True).select_related("batch", "received_by")
    qs = _apply_date_filter(qs, filters, date_field="received_at")
    if filters.get("status"):
        qs = qs.filter(status=filters["status"])

    headers = [
        "Intake Code", "Batch Code", "Warehouse", "Status",
        "Received At", "Received By",
        "Total Weight (kg)", "Net Weight (kg)",
        "Moisture (%)", "Impurity (%)", "Grade Assigned",
        "Rejection Reason",
    ]
    rows = [
        [
            w.code, w.batch.batch_code, w.warehouse_name, w.status,
            str(w.received_at.date()), w.received_by.get_full_name() if w.received_by else "",
            float(w.total_weight_kg), float(w.net_weight_kg or 0),
            float(w.moisture_pct or 0), float(w.impurity_pct or 0),
            w.grade_assigned, w.rejection_reason,
        ]
        for w in qs
    ]
    return headers, rows


def _build_order_summary(filters: dict):
    """SRD MODULE 10: Revenue and order transaction report."""
    try:
        from apps.buyers.models import Order
    except ImportError:
        return ["Note"], [["Buyers app not installed"]]

    qs = Order.objects.filter(is_active=True).select_related("buyer")
    qs = _apply_date_filter(qs, filters)
    if filters.get("status"):
        qs = qs.filter(status=filters["status"])

    headers = [
        "Order Code", "Buyer", "Status",
        "Total Amount", "Currency", "Created At", "Confirmed At",
    ]
    rows = [
        [
            str(o.pk), getattr(o.buyer, "company_name", str(o.buyer)),
            o.status, float(o.total_amount), o.currency,
            str(o.created_at.date()),
            str(o.confirmed_at.date()) if getattr(o, "confirmed_at", None) else "",
        ]
        for o in qs
    ]
    return headers, rows


def _build_payment_summary(filters: dict):
    """SRD MODULE 10: Payment channels and success rates."""
    try:
        from apps.buyers.models import Payment
    except ImportError:
        return ["Note"], [["Buyers app not installed"]]

    qs = Payment.objects.filter(is_active=True)
    qs = _apply_date_filter(qs, filters, date_field="created_at")
    if filters.get("status"):
        qs = qs.filter(status=filters["status"])

    headers = [
        "Payment ID", "Order ID", "Amount", "Currency",
        "Channel", "Status", "Provider Ref", "Created At",
    ]
    rows = [
        [
            str(p.pk), str(p.order_id),
            float(p.amount), p.currency,
            p.payment_channel, p.status,
            p.provider_reference, str(p.created_at.date()),
        ]
        for p in qs
    ]
    return headers, rows


def _build_buyer_activity(filters: dict):
    """SRD MODULE 10: Buyer activity and order history."""
    try:
        from apps.buyers.models import Buyer
    except ImportError:
        return ["Note"], [["Buyers app not installed"]]

    qs = Buyer.objects.filter(is_active=True)
    qs = _apply_date_filter(qs, filters)
    if filters.get("status"):
        qs = qs.filter(verification_status=filters["status"])

    headers = [
        "Buyer ID", "Company", "Country", "Verification Status",
        "Total Orders", "Joined At",
    ]
    rows = [
        [
            str(b.pk),
            getattr(b, "company_name", ""),
            getattr(b, "country", ""),
            b.verification_status,
            b.orders.filter(is_active=True).count(),
            str(b.created_at.date()),
        ]
        for b in qs
    ]
    return headers, rows


def _build_impact_dashboard(filters: dict):
    """
    SRD MODULE 10: Admin analytics — total farmers, farms, CO2, revenue.
    Single-row summary report.
    """
    from apps.farmers.models import Farmer, Farm
    from django.db.models import Count, Sum
    from django.db.models.functions import Coalesce
    from django.db.models import Value, FloatField

    farmer_qs = Farmer.objects.filter(is_active=True)
    farm_qs   = Farm.objects.filter(is_active=True)

    if filters.get("region"):
        farmer_qs = farmer_qs.filter(region__iexact=filters["region"])
        farm_qs   = farm_qs.filter(farmer__region__iexact=filters["region"])

    total_farmers  = farmer_qs.count()
    verified       = farmer_qs.filter(verification_status="verified").count()
    female_farmers = farmer_qs.filter(gender="female").count()
    total_farms    = farm_qs.count()
    total_area     = farm_qs.aggregate(
        t=Coalesce(Sum("area_hectares"), Value(0.0), output_field=FloatField())
    )["t"]

    headers = [
        "Total Farmers", "Verified Farmers", "Female Farmers",
        "Female %", "Total Farms", "Total Area (ha)",
    ]
    rows = [[
        total_farmers, verified, female_farmers,
        round(female_farmers / total_farmers * 100, 1) if total_farmers else 0,
        total_farms, float(total_area),
    ]]
    return headers, rows


def _build_co2_savings(filters: dict):
    """SRD MODULE 10: CO2 reduction estimates by region."""
    from apps.farmers.models import Farm
    from django.db.models import Sum
    from django.db.models.functions import Coalesce
    from django.db.models import Value, FloatField

    qs = Farm.objects.filter(
        is_active=True,
        farmer__verification_status="verified",
    ).values("farmer__region").annotate(
        total_area=Coalesce(Sum("area_hectares"), Value(0.0), output_field=FloatField()),
        farm_count=Sum(Value(1)),
    )
    if filters.get("region"):
        qs = qs.filter(farmer__region__iexact=filters["region"])

    # Estimate: 2.5 tCO2/ha/year for smallholder sustainable farming
    CO2_PER_HA = 2.5

    headers = ["Region", "Farm Count", "Total Area (ha)", "Est. CO2 Saved (t/year)"]
    rows = [
        [
            r["farmer__region"], r["farm_count"],
            round(float(r["total_area"]), 2),
            round(float(r["total_area"]) * CO2_PER_HA, 2),
        ]
        for r in qs
    ]
    return headers, rows


def _build_women_participation(filters: dict):
    """SRD MODULE 10: Women participation metrics by region."""
    from apps.farmers.models import Farmer
    from django.db.models import Count

    qs = Farmer.objects.filter(is_active=True).values("region").annotate(
        total =Count("id"),
        female=Count("id", filter=__import__("django.db.models", fromlist=["Q"]).Q(gender="female")),
    )
    if filters.get("region"):
        qs = qs.filter(region__iexact=filters["region"])

    headers = ["Region", "Total Farmers", "Female Farmers", "Female %"]
    rows = [
        [
            r["region"], r["total"], r["female"],
            round(r["female"] / r["total"] * 100, 1) if r["total"] else 0,
        ]
        for r in qs
    ]
    return headers, rows


def _build_product_quality(filters: dict):
    """SRD MODULE 10: Product quality grading report."""
    from apps.traceability.models import Batch

    qs = Batch.objects.filter(
        is_active=True, batch_type="farmer",
        moisture_pct__isnull=False,
    ).select_related("farmer", "product")
    if filters.get("region"):
        qs = qs.filter(farmer__region__iexact=filters["region"])
    qs = _apply_date_filter(qs, filters, date_field="collection_date")

    headers = [
        "Batch Code", "Farmer Code", "Region",
        "Product", "Weight (kg)", "Moisture (%)", "Impurity (%)", "Grade",
        "Collection Date",
    ]
    rows = [
        [
            b.batch_code,
            b.farmer.code    if b.farmer  else "",
            b.farmer.region  if b.farmer  else "",
            b.product.name   if b.product else "",
            float(b.weight_kg),
            float(b.moisture_pct or 0),
            float(b.impurity_pct or 0),
            b.grade,
            str(b.collection_date or ""),
        ]
        for b in qs
    ]
    return headers, rows


# =============================================================================
# FILE WRITING
# =============================================================================

def _write_file(
    headers: list[str],
    rows: list[list],
    output_format: str,
) -> tuple[str, bytes]:
    """
    Serialize (headers, rows) into the target output format.
    Returns (filename, bytes_content).
    """
    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"report_{timestamp}"

    if output_format == "csv":
        return _write_csv(headers, rows, f"{base_name}.csv")
    elif output_format == "json":
        return _write_json(headers, rows, f"{base_name}.json")
    elif output_format == "xlsx":
        return _write_xlsx(headers, rows, f"{base_name}.xlsx")
    else:
        # Default to CSV for unsupported / pdf (PDF requires a separate library)
        return _write_csv(headers, rows, f"{base_name}.csv")


def _write_csv(headers, rows, filename) -> tuple[str, bytes]:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return filename, buf.getvalue().encode("utf-8-sig")  # BOM for Excel compatibility


def _write_json(headers, rows, filename) -> tuple[str, bytes]:
    data = [dict(zip(headers, row)) for row in rows]
    content = json.dumps(
        {"count": len(data), "results": data},
        ensure_ascii=False, default=str, indent=2,
    )
    return filename, content.encode("utf-8")


def _write_xlsx(headers, rows, filename) -> tuple[str, bytes]:
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(headers)
        for row in rows:
            ws.append([str(c) if not isinstance(c, (int, float, bool)) else c for c in row])
        buf = io.BytesIO()
        wb.save(buf)
        return filename, buf.getvalue()
    except ImportError:
        logger.warning("openpyxl not installed — falling back to CSV for XLSX report")
        return _write_csv(headers, rows, filename.replace(".xlsx", ".csv"))