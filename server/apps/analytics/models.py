"""
apps/analytics/models.py  —  FarmicleGrow-Trace Platform

Models:
  SingletonModel     Abstract base for models that should have exactly one row
  PlatformSnapshot   Singleton — cached platform-wide KPIs, refreshed every 15 min
  RegionalSummary    Append-only monthly regional KPI snapshot per region

Design:
  PlatformSnapshot holds a single row. Views read from it directly (O(1) — no
  aggregation on the hot path). The Celery beat task calls refresh() every
  15 minutes to recompute from source tables.

  RegionalSummary stores one row per (region, year, month). Used for time-series
  charts and regional trend comparisons. Built by the same Celery task.

  All heavy aggregations live in analytics/services.py — not in the model itself.
  PlatformSnapshot.refresh() is a thin coordinator that calls service functions.

Fixes vs original:
  • SingletonModel defined here — not imported from apps.core (it doesn't exist there)
  • FieldOfficer import removed — staff uses User.Role.FIELD_OFFICER, no separate model
  • total_farm_visits now queries FarmVisit correctly
  • PlatformSnapshot.refresh() delegates to analytics.services — no giant inline method
  • RegionalSummary gains manager for time-series and leaderboard queries
"""
from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models.abstract import BaseModel


# =============================================================================
# SINGLETON BASE
# =============================================================================

class SingletonModel(models.Model):
    """
    Abstract base for models that must have exactly one DB row.

    get_or_create_singleton() is the only valid way to fetch the instance.
    save() is overridden to enforce the singleton constraint — any attempt to
    create a second row sets pk=1 first.
    """

    class Meta:
        abstract = True

    @classmethod
    def get_or_create_singleton(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError(f"{self.__class__.__name__} is a singleton and cannot be deleted.")


# =============================================================================
# PLATFORM SNAPSHOT
# =============================================================================

class PlatformSnapshot(SingletonModel):
    """
    Singleton platform-wide KPI cache.

    Refreshed every 15 minutes by the Celery beat task:
        apps.analytics.tasks.refresh_platform_snapshot

    All columns are nullable with default=0 so the first refresh can populate
    them without a migration-time data dependency.

    Data is sourced from:
      apps.farmers      → Farmer, Farm, FarmVisit
      apps.traceability → Batch, TraceRecord
      apps.buyers       → Order, Payment (via buyers app)
      accounts / User   → field officers counted by role
    """

    # ── Farmers ───────────────────────────────────────────────────────────────
    total_farmers         = models.PositiveIntegerField(default=0)
    verified_farmers      = models.PositiveIntegerField(default=0)
    female_farmers        = models.PositiveIntegerField(default=0)
    farmers_this_month    = models.PositiveIntegerField(default=0)

    # ── Farms ─────────────────────────────────────────────────────────────────
    total_farms           = models.PositiveIntegerField(default=0)
    total_area_ha         = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    avg_farm_area_ha      = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    # ── Supply chain ──────────────────────────────────────────────────────────
    total_batches         = models.PositiveIntegerField(default=0)
    total_trace_records   = models.PositiveIntegerField(default=0)
    total_weight_kg       = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    exported_shipments    = models.PositiveIntegerField(default=0)
    destination_countries = models.PositiveIntegerField(default=0)

    # ── Commerce ──────────────────────────────────────────────────────────────
    total_orders          = models.PositiveIntegerField(default=0)
    orders_this_month     = models.PositiveIntegerField(default=0)
    total_revenue_ghs     = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    revenue_this_month    = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total_buyers          = models.PositiveIntegerField(default=0)
    avg_order_value_ghs   = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # ── Staff ─────────────────────────────────────────────────────────────────
    active_field_officers = models.PositiveIntegerField(default=0)
    total_farm_visits     = models.PositiveIntegerField(default=0)
    total_produce_kg      = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    # ── Computed KPIs ─────────────────────────────────────────────────────────
    verification_rate_pct = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    women_empowerment_pct = models.DecimalField(max_digits=5, decimal_places=1, default=0)

    last_refreshed_at     = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Platform Snapshot")

    def __str__(self) -> str:
        ts = self.last_refreshed_at.strftime("%Y-%m-%d %H:%M") if self.last_refreshed_at else "never"
        return f"PlatformSnapshot — refreshed {ts}"

    def refresh(self) -> None:
        """
        Recompute all KPIs and save.
        Delegates to analytics.services.compute_platform_snapshot() so
        the aggregation logic is testable independently of the model.
        """
        from apps.analytics.services import compute_platform_snapshot

        data = compute_platform_snapshot()
        for field, value in data.items():
            setattr(self, field, value)
        self.save()


# =============================================================================
# REGIONAL SUMMARY
# =============================================================================

class RegionalSummaryManager(models.Manager):

    def for_region(self, region: str):
        return self.filter(region__iexact=region).order_by("-year", "-month")

    def for_period(self, year: int, month: int):
        return self.filter(year=year, month=month).order_by("region")

    def latest_month(self):
        """Return all regions for the most recently recorded month."""
        from django.db.models import Max
        latest = self.aggregate(
            year=Max("year"), month=Max("month")
        )
        if not latest["year"]:
            return self.none()
        return self.filter(year=latest["year"], month=latest["month"]).order_by("region")

    def trend(self, region: str, months: int = 12):
        """Return the last N months of snapshots for one region."""
        return (
            self.filter(region__iexact=region)
            .order_by("-year", "-month")[:months]
        )

    def leaderboard(self, year: int, month: int, metric: str = "farmer_count"):
        """Rank all regions by a single metric for a given month."""
        return (
            self.for_period(year, month)
            .order_by(f"-{metric}")
        )


class RegionalSummary(BaseModel):
    """
    Monthly regional KPI snapshot — one row per (region, year, month).
    Append-only: rows are never updated after creation.

    Created by the same Celery beat task that refreshes PlatformSnapshot.
    Used for:
      • Regional trend charts (farmer growth, produce volume over time)
      • Region leaderboard on the admin dashboard
      • Impact reporting (women %, verification rate per region)
    """

    region  = models.CharField(max_length=100, db_index=True)
    year    = models.PositiveSmallIntegerField(db_index=True)
    month   = models.PositiveSmallIntegerField(db_index=True)

    # Farmers
    farmer_count   = models.PositiveIntegerField(default=0)
    verified_count = models.PositiveIntegerField(default=0)
    female_count   = models.PositiveIntegerField(default=0)
    new_farmers_mtd = models.PositiveIntegerField(
        default=0, help_text=_("Farmers registered in this specific month.")
    )

    # Farms
    farm_count    = models.PositiveIntegerField(default=0)
    total_area_ha = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    # Supply chain
    batch_count    = models.PositiveIntegerField(default=0)
    total_weight_kg = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    trace_records  = models.PositiveIntegerField(default=0)
    exported_count = models.PositiveIntegerField(default=0)

    # Commerce
    order_count = models.PositiveIntegerField(default=0)
    revenue_ghs = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    # Staff
    officer_count = models.PositiveIntegerField(default=0)
    visit_count   = models.PositiveIntegerField(default=0)
    produce_kg    = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    objects = RegionalSummaryManager()

    class Meta(BaseModel.Meta):
        verbose_name    = _("Regional Summary")
        unique_together = [("region", "year", "month")]
        ordering        = ["-year", "-month", "region"]
        indexes         = [
            models.Index(fields=["region", "year", "month"]),
            models.Index(fields=["year",   "month"]),
        ]

    def __str__(self) -> str:
        return f"{self.region} {self.year}-{self.month:02d}"

    @property
    def verification_rate_pct(self) -> float:
        if not self.farmer_count:
            return 0.0
        return round(self.verified_count / self.farmer_count * 100, 1)

    @property
    def women_pct(self) -> float:
        if not self.farmer_count:
            return 0.0
        return round(self.female_count / self.farmer_count * 100, 1)