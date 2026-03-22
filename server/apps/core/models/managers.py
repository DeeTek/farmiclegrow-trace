"""
apps/core/managers.py  —  FarmicleGrow-Trace Platform

Managers for all domain models.

Architecture
────────────
Every domain manager uses Django's from_queryset() pattern so every QuerySet
method is chainable on both the manager and any returned queryset:

    Farmer.objects.verified().in_region("Ashanti").with_farm_count()[:20]

Domain managers covered
────────────────────────
  BaseManager / BaseQuerySet      — active(), soft_delete(), search(), time filters
  VerifiableManager               — verified(), pending(), suspended(), summary()
  CodedManager                    — get_by_code(), exists_by_code()
  GeoManager                      — near(), with_coordinates(), missing_coordinates()
  FarmerManager       → buyers app + traceability
  FarmManager         → farmers app geo queries
  TraceabilityManager → QR lookup, chain resolution
  BatchManager        → farmer/warehouse/product batch filtering
  OrderManager        → buyers e-commerce order workflow
  PaymentManager      → payment status, MTD/YTD revenue
  CartManager         → active cart, abandoned cart cleanup
  ProductManager      → marketplace listing, stock management
  FieldOfficerManager → staff performance, region coverage
  WarehouseManager    → intake tracking, capacity
  ReportManager       → scheduled reports, generation queue
  NotificationManager → unread counts, bulk-mark-read

FIX vs uploaded
───────────────
  ─ GeoManager.missing_coordinates() used Q without importing it (NameError)
  ─ All managers now use from_queryset() pattern consistently
  ─ Domain managers reference their QuerySet classes from querysets.py
"""
from __future__ import annotations

import math
from django.db import models
from django.db.models import Q, Sum, Count
from django.db.models.functions import Coalesce
from django.db.models import Value, FloatField
from django.utils import timezone


# =============================================================================
# BASE QUERYSET  (self-contained — no import from querysets.py avoids circular)
# =============================================================================

class BaseQuerySet(models.QuerySet):

    def active(self):
        return self.filter(is_active=True, deleted_at__isnull=True)

    def inactive(self):
        return self.filter(is_active=False)

    def deleted(self):
        return self.filter(deleted_at__isnull=False)

    def with_deleted(self):
        return self.all()

    def activate(self):
        return self.update(is_active=True, deleted_at=None)

    def deactivate(self):
        return self.update(is_active=False)

    def soft_delete(self):
        return self.update(is_active=False, deleted_at=timezone.now())

    def recent(self, days: int = 30):
        return self.filter(created_at__gte=timezone.now() - timezone.timedelta(days=days))

    def created_between(self, start, end):
        return self.filter(created_at__range=(start, end))

    def mtd(self, date_field: str = "created_at"):
        now = timezone.now()
        return self.filter(**{f"{date_field}__month": now.month, f"{date_field}__year": now.year})

    def ytd(self, date_field: str = "created_at"):
        return self.filter(**{f"{date_field}__year": timezone.now().year})

    def search(self, query: str, fields: list):
        if not query or len(query.strip()) < 2:
            return self.none()
        q = Q()
        for f in fields:
            lookup = f if "__" in f else f"{f}__icontains"
            q |= Q(**{lookup: query.strip()})
        return self.filter(q).distinct()

    def ids_only(self):
        return self.values_list("id", flat=True)

    def slim(self, *fields):
        return self.only(*fields) if fields else self

    def in_batches(self, size: int = 500):
        ids = list(self.values_list("id", flat=True))
        for i in range(0, len(ids), size):
            yield self.model.objects.filter(id__in=ids[i: i + size])

    def page(self, page_number: int = 1, page_size: int = 20):
        offset = (page_number - 1) * page_size
        return self[offset: offset + page_size]


# =============================================================================
# VERIFIABLE QUERYSET
# =============================================================================

class VerifiableQuerySet(BaseQuerySet):

    def verified(self):
        return self.filter(verification_status="verified")

    def pending_verification(self):
        return self.filter(verification_status="pending")

    def rejected(self):
        return self.filter(verification_status="rejected")

    def suspended(self):
        return self.filter(verification_status="suspended")

    def verified_this_month(self):
        now = timezone.now()
        return self.filter(verified_at__month=now.month, verified_at__year=now.year)

    def verification_summary(self) -> dict:
        return self.aggregate(
            total     = Count("id"),
            verified  = Count("id", filter=Q(verification_status="verified")),
            pending   = Count("id", filter=Q(verification_status="pending")),
            rejected  = Count("id", filter=Q(verification_status="rejected")),
            suspended = Count("id", filter=Q(verification_status="suspended")),
        )


# =============================================================================
# BASE MANAGER
# =============================================================================

class BaseManager(models.Manager):

    def get_queryset(self):
        return BaseQuerySet(self.model, using=self._db).filter(
            is_active=True, deleted_at__isnull=True,
        )

    def all_records(self):
        return BaseQuerySet(self.model, using=self._db)

    def active(self):
        return self.get_queryset()

    def recent(self, days: int = 30):
        return self.get_queryset().recent(days=days)

    def search(self, query: str, fields: list):
        return self.get_queryset().search(query, fields)


# =============================================================================
# VERIFIABLE MANAGER
# =============================================================================

class VerifiableManager(BaseManager):

    def get_queryset(self):
        return VerifiableQuerySet(self.model, using=self._db).filter(
            is_active=True, deleted_at__isnull=True,
        )

    def verified(self):
        return self.get_queryset().verified()

    def pending(self):
        return self.get_queryset().pending_verification()

    def suspended(self):
        return self.get_queryset().suspended()

    def pending_count(self) -> int:
        return self.pending().count()

    def verification_summary(self) -> dict:
        return self.get_queryset().verification_summary()


# =============================================================================
# CODED MANAGER
# =============================================================================

class CodedManager(BaseManager):

    def get_by_code(self, code: str):
        return self.get_queryset().get(code__iexact=code)

    def exists_by_code(self, code: str) -> bool:
        return self.get_queryset().filter(code__iexact=code).exists()

    def search_by_code(self, partial: str):
        return self.get_queryset().filter(code__icontains=partial)


# =============================================================================
# GEO MANAGER  (FIX: Q was not imported in original — caused NameError)
# =============================================================================

class GeoManager(BaseManager):

    def with_coordinates(self):
        return self.get_queryset().filter(
            latitude__isnull=False, longitude__isnull=False,
        )

    def missing_coordinates(self):
        # FIX: Q is now imported at module level
        return self.get_queryset().filter(
            Q(latitude__isnull=True) | Q(longitude__isnull=True)
        )

    def with_polygon(self):
        return self.get_queryset().filter(polygon_coordinates__isnull=False)

    def near(self, lat: float, lon: float, radius_km: float = 10.0):
        """Bounding-box proximity filter."""
        deg_per_km = 1 / 111.0
        lat_delta  = radius_km * deg_per_km
        lon_delta  = radius_km * deg_per_km / max(math.cos(math.radians(lat)), 1e-6)
        return self.get_queryset().filter(
            latitude__range =(lat - lat_delta, lat + lat_delta),
            longitude__range=(lon - lon_delta, lon + lon_delta),
        )


# =============================================================================
# DOMAIN-SPECIFIC MANAGERS — all use from_queryset() pattern
# =============================================================================

def _make_manager(queryset_class, base_manager_class=BaseManager):
    """
    Factory: creates a Manager from a QuerySet class using from_queryset().

    Usage inside a model:
        from apps.core.managers import _make_manager
        from apps.core.querysets import FarmerQuerySet

        class Farmer(BaseModel, CodedModel, VerifiableModel):
            objects = _make_manager(FarmerQuerySet, VerifiableManager)
    """
    return base_manager_class.from_queryset(queryset_class)()


# ── Farmers app ───────────────────────────────────────────────────────────────

class FarmerManager(VerifiableManager):
    """
    Attach to apps.farmers.Farmer.
    Usage: objects = FarmerManager.from_queryset(FarmerQuerySet)()
    """

    def in_region(self, region: str):
        return self.get_queryset().filter(region__iexact=region)

    def by_code(self, code: str):
        return self.get_queryset().get(code__iexact=code)

    def women(self):
        return self.get_queryset().filter(gender="female")

    def registered_by(self, officer_id):
        return self.get_queryset().filter(registered_by_id=officer_id)

    def pending_approval(self):
        return self.get_queryset().filter(
            verification_status="pending", is_active=True,
        )

    def recently_registered(self, days: int = 30):
        return self.get_queryset().recent(days=days)

    def incomplete_profiles(self, threshold: int = 60):
        # Lazy import to avoid circular dependency at module load
        from apps.core.querysets import FarmerQuerySet
        qs = self.get_queryset()
        if hasattr(qs, "with_profile_score"):
            return qs.with_profile_score().filter(profile_score__lt=threshold)
        return qs


class FarmManager(GeoManager):
    """Attach to apps.farmers.Farm."""

    def for_farmer(self, farmer_id):
        return self.get_queryset().filter(farmer_id=farmer_id)

    def by_code(self, code: str):
        return self.get_queryset().get(code__iexact=code)

    def in_region(self, region: str):
        return self.get_queryset().filter(farmer__region__iexact=region)

    def unregistered_gps(self):
        return self.missing_coordinates()


# ── Traceability app ──────────────────────────────────────────────────────────

class TraceabilityManager(CodedManager):
    """Attach to apps.traceability.TraceRecord."""

    def active_chain(self):
        return self.get_queryset().filter(
            status__in=["active", "exported", "in_transit", "delivered"],
        )

    def by_trace_code(self, code: str):
        return self.get_queryset().filter(trace_code__iexact=code)

    def by_batch_code(self, code: str):
        return self.get_queryset().filter(
            Q(farmer_batch_code__iexact=code)
            | Q(warehouse_batch_code__iexact=code)
            | Q(product_batch_code__iexact=code)
        )

    def for_public_scan(self):
        return self.active_chain().filter(
            farmer__verification_status="verified"
        ).select_related("farmer", "farm", "product")

    def resolve_qr(self, qr_code: str):
        """Resolve any QR code to a single TraceRecord. Returns None if not found."""
        try:
            return self.get_queryset().get(
                Q(trace_code__iexact=qr_code)
                | Q(farmer_batch_code__iexact=qr_code)
                | Q(product_batch_code__iexact=qr_code),
                is_active=True,
            )
        except (self.model.DoesNotExist, self.model.MultipleObjectsReturned):
            return None
    
    def with_full_chain(self):
        return self.get_queryset().with_full_chain()

    def status_pipeline(self):
        return self.get_queryset().status_pipeline()

    def destination_summary(self):
        return self.get_queryset().destination_summary()


class BatchManager(CodedManager):
    """Attach to apps.traceability.Batch."""

    def farmer_batches(self):
        return self.get_queryset().filter(batch_type="farmer")

    def warehouse_batches(self):
        return self.get_queryset().filter(batch_type="warehouse")

    def product_batches(self):
        return self.get_queryset().filter(batch_type="product")

    def active_batches(self):
        return self.get_queryset().filter(status="active")

    def by_officer(self, officer_id):
        return self.get_queryset().filter(collected_by_id=officer_id)

    def by_code(self, code: str):
        return self.get_queryset().filter(batch_code__iexact=code).first()


# ── Buyers app ────────────────────────────────────────────────────────────────

class OrderManager(BaseManager):
    """Attach to apps.buyers.Order."""

    def for_buyer(self, buyer_id):
        return self.get_queryset().filter(buyer_id=buyer_id)

    def pending(self):
        return self.get_queryset().filter(status="pending")

    def in_progress(self):
        return self.get_queryset().filter(
            status__in=["pending", "confirmed", "processing", "dispatched"]
        )

    def delivered(self):
        return self.get_queryset().filter(status="delivered")

    def high_value(self, threshold: float = 10_000.0):
        return self.get_queryset().filter(total_amount__gte=threshold)

    def overdue(self):
        return self.in_progress().filter(
            expected_delivery_date__lt=timezone.now().date()
        )


class PaymentManager(BaseManager):
    """Attach to apps.buyers.Payment."""

    def completed(self):
        return self.get_queryset().filter(status="completed")

    def pending(self):
        return self.get_queryset().filter(status="pending")

    def mobile_money(self):
        return self.get_queryset().filter(payment_channel="mobile_money")

    def for_order(self, order_id):
        return self.get_queryset().filter(order_id=order_id)

    def for_buyer(self, buyer_id):
        return self.get_queryset().filter(order__buyer_id=buyer_id)

    def mtd_total(self) -> float:
        now = timezone.now()
        return self.completed().filter(
            payment_date__month=now.month,
            payment_date__year=now.year,
        ).aggregate(
            total=Coalesce(Sum("amount"), Value(0.0))
        )["total"]

    def ytd_total(self) -> float:
        return self.completed().filter(
            payment_date__year=timezone.now().year,
        ).aggregate(
            total=Coalesce(Sum("amount"), Value(0.0))
        )["total"]


class CartManager(BaseManager):
    """Attach to apps.buyers.Cart."""

    def active_for_buyer(self, buyer_id):
        return self.get_queryset().filter(buyer_id=buyer_id, status="active").first()

    def abandoned(self):
        return self.get_queryset().filter(status="abandoned")

    def expired_active(self):
        """Active carts past their expires_at timestamp."""
        return self.get_queryset().filter(
            status="active",
            expires_at__lt=timezone.now(),
        )

    def mark_expired(self) -> int:
        """Bulk-abandon expired active carts. Call from a Celery beat task."""
        return self.expired_active().update(status="abandoned")


# ── Product / marketplace ─────────────────────────────────────────────────────

class ProductManager(BaseManager):
    """Attach to apps.farmers.Product."""

    def available(self):
        return self.get_queryset().filter(is_available=True, stock_kg__gt=0)

    def by_category(self, category: str):
        return self.get_queryset().filter(category__iexact=category)

    def low_stock(self, threshold_kg: float = 100):
        return self.get_queryset().filter(
            is_available=True, stock_kg__gt=0, stock_kg__lt=threshold_kg,
        )

    def out_of_stock(self):
        return self.get_queryset().filter(
            Q(stock_kg__lte=0) | Q(is_available=False)
        )

    def marketplace_listing(self):
        return self.available().select_related("origin_farmer", "origin_farm")


# ── Staff app ─────────────────────────────────────────────────────────────────

class FieldOfficerManager(VerifiableManager):
    """Attach to apps.staff.FieldOfficer."""

    def active_officers(self):
        return self.get_queryset().filter(
            is_active=True, employment_status="active", deleted_at__isnull=True,
        )

    def in_region(self, region: str):
        return self.get_queryset().filter(assigned_region__iexact=region)

    def pending_approval(self):
        return self.get_queryset().filter(verification_status="pending", is_active=True)


class WarehouseManagerManager(VerifiableManager):
    """Attach to apps.staff.WarehouseManager (note: manager for the model)."""

    def active_managers(self):
        return self.get_queryset().filter(is_active=True, employment_status="active")

    def by_warehouse(self, warehouse_name: str):
        return self.get_queryset().filter(warehouse_name__icontains=warehouse_name)


# ── Reports app ───────────────────────────────────────────────────────────────

class ReportManager(BaseManager):
    """Attach to apps.reports.Report."""

    def queued(self):
        return self.get_queryset().filter(status="queued")

    def ready(self):
        return self.get_queryset().filter(status="ready")

    def for_user(self, user_id):
        return self.get_queryset().filter(requested_by_id=user_id)

    def stale(self, older_than_days: int = 7):
        cutoff = timezone.now() - timezone.timedelta(days=older_than_days)
        return self.ready().filter(created_at__lte=cutoff)


# ── Notifications ─────────────────────────────────────────────────────────────

class NotificationManager(BaseManager):
    """Attach to apps.buyers.BuyerNotification or apps.notifications.Notification."""

    def for_user(self, user_id):
        return self.get_queryset().filter(buyer_id=user_id)

    def unread(self, user_id):
        return self.for_user(user_id).filter(is_read=False)

    def mark_all_read(self, user_id) -> int:
        return self.unread(user_id).update(is_read=True, read_at=timezone.now())

    def unread_count(self, user_id) -> int:
        return self.unread(user_id).count()