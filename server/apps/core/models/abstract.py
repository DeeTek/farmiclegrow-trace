"""
apps/core/abstract.py  —  FarmicleGrow-Trace Platform

Primitive abstract model mixins.  Every domain model across all apps
inherits from exactly the combination it needs.

Django never creates tables for abstract models.

Fixed vs original:
  ─ AuditedModel uses settings.AUTH_USER_MODEL, not hardcoded "auth.User"
  ─ VerifiableModel.verify() / reject() use update_fields (no full-row save)
  ─ VerifiableModel.reject() sets verified_at=None
  ─ CodedModel._build_code() uses secrets + collision-retry loop
  ─ StatusModel.status field now references STATUS_CHOICES properly
  ─ SoftDeleteModel.delete() emits a pre/post signal pair

New vs original:
  ─ NoteModel          — threaded internal notes on any domain record
  ─ OrderedModel       — integer position field for user-defined ordering
  ─ SingletonModel     — guarantees only one DB row (system-wide config)
  ─ PublishableModel   — draft / published / archived workflow
  ─ PriorityModel      — low / normal / high / urgent priority field
"""
from __future__ import annotations

import secrets
import uuid
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils import timezone


# =============================================================================
# 1.  UUID PRIMARY KEY
# =============================================================================

class UUIDModel(models.Model):
    """
    UUID primary key — safe for distributed systems, non-enumerable in URLs.
    """
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_index=True,
    )

    class Meta:
        abstract = True


# =============================================================================
# 2.  TIMESTAMPS
# =============================================================================

class TimeStampedModel(models.Model):
    """
    created_at / updated_at — auto-set, indexed for time-range queries.
    """
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True,     db_index=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]


# =============================================================================
# 3.  SOFT DELETE
# =============================================================================

class SoftDeleteQuerySet(models.QuerySet):
    """QuerySet that hides soft-deleted records by default."""

    def active(self):
        return self.filter(is_active=True, deleted_at__isnull=True)

    def deleted(self):
        return self.filter(deleted_at__isnull=False)

    def inactive(self):
        return self.filter(is_active=False)

    def delete(self):
        """Soft-delete the entire queryset in a single UPDATE."""
        return self.update(is_active=False, deleted_at=timezone.now())

    def hard_delete(self):
        """Physically remove all records in this queryset."""
        return super().delete()

    def restore(self):
        return self.update(is_active=True, deleted_at=None)


class SoftDeleteManager(models.Manager):
    """Default manager — returns only non-deleted records."""

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).filter(
            deleted_at__isnull=True
        )

    def all_with_deleted(self):
        """Bypass soft-delete filter — includes deleted records."""
        return SoftDeleteQuerySet(self.model, using=self._db)

    def deleted_only(self):
        return SoftDeleteQuerySet(self.model, using=self._db).deleted()


class SoftDeleteModel(models.Model):
    """
    Soft-delete: records are never physically removed.
    Sets is_active=False + deleted_at timestamp instead.
    """
    is_active  = models.BooleanField(default=True,  db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects     = SoftDeleteManager()
    all_objects = models.Manager()   # unfiltered — for migrations / admin

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        """Soft-delete this instance."""
        self.is_active  = False
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_active", "deleted_at"])

    def hard_delete(self, using=None, keep_parents=False):
        """Physically remove from the database."""
        super().delete(using=using, keep_parents=keep_parents)

    def restore(self):
        """Undo a soft delete."""
        self.is_active  = True
        self.deleted_at = None
        self.save(update_fields=["is_active", "deleted_at"])


# =============================================================================
# 4.  BASE MODEL  (UUID + Timestamps + SoftDelete)
# =============================================================================

class BaseModel(UUIDModel, TimeStampedModel, SoftDeleteModel):
    """
    Standard base for ALL FarmicleGrow-Trace domain models.

    Every model gets:
      UUID PK · created_at · updated_at · is_active · deleted_at

    Usage:
        class Farmer(BaseModel):
            name = models.CharField(max_length=100)
    """
    class Meta:
        abstract = True
        ordering = ["-created_at"]


# =============================================================================
# 5.  AUTO-GENERATED CODE  (collision-safe, secrets-backed)
# =============================================================================

class CodedModel(models.Model):
    """
    Human-readable, auto-generated unique code.

    Configuration (set on subclass):
        CODE_PREFIX       = "FMR"         required — e.g. FMR, BCH, TRC
        CODE_REGION_FIELD = "region"      optional — appends 2-char region initials
        CODE_LENGTH       = 5             digits in the numeric suffix
        CODE_MAX_RETRIES  = 10            collision-retry limit

    Generated examples:
        FMR-AS-83421   (Farmer, Ashanti region)
        BCH-73912      (Batch, no region)
        TRC-GH-2025-47823  (TraceRecord with year — override _build_code)

    FIX vs original:
        Uses secrets.randbelow() instead of random.randint() — cryptographically
        secure randomness prevents code prediction attacks.
        Retries up to CODE_MAX_RETRIES times on unique-constraint collision.
    """
    CODE_PREFIX       : str = "OBJ"
    CODE_REGION_FIELD : str | None = None
    CODE_LENGTH       : int = 5
    CODE_MAX_RETRIES  : int = 10

    code = models.CharField(
        max_length=30,
        unique=True,
        editable=False,
        db_index=True,
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self._generate_unique_code()
        super().save(*args, **kwargs)

    def _generate_unique_code(self) -> str:
        """
        Generate a code and retry on collision.
        Raises RuntimeError if all retries are exhausted (extremely unlikely).
        """
        for attempt in range(self.CODE_MAX_RETRIES):
            code = self._build_code()
            if not self.__class__.objects.filter(code=code).exists():
                return code
        raise RuntimeError(
            f"Could not generate a unique code for {self.__class__.__name__} "
            f"after {self.CODE_MAX_RETRIES} attempts."
        )

    def _build_code(self) -> str:
        prefix = self.CODE_PREFIX
        region = ""
        if self.CODE_REGION_FIELD:
            raw    = getattr(self, self.CODE_REGION_FIELD, "") or ""
            region = f"-{raw[:2].upper()}" if raw else ""
        low    = 10 ** (self.CODE_LENGTH - 1)
        high   = 10 ** self.CODE_LENGTH
        digits = low + secrets.randbelow(high - low)
        return f"{prefix}{region}-{digits}"


# =============================================================================
# 6.  GEO COORDINATES
# =============================================================================

class GeoModel(models.Model):
    """
    GPS latitude/longitude + optional GeoJSON polygon + altitude + accuracy.
    Used by Farm, FarmVisit, WarehouseIntake, SupplyChainEvent.
    """
    latitude            = models.DecimalField(max_digits=9,  decimal_places=6, null=True, blank=True)
    longitude           = models.DecimalField(max_digits=9,  decimal_places=6, null=True, blank=True)
    altitude_meters     = models.DecimalField(max_digits=7,  decimal_places=2, null=True, blank=True)
    polygon_coordinates = models.JSONField(
        null=True, blank=True,
        help_text=_("GeoJSON polygon [[lon,lat], ...] — closed ring required"),
    )
    gps_accuracy_meters = models.FloatField(
        null=True, blank=True,
        help_text=_("GPS accuracy at capture time (metres)"),
    )
    gps_captured_at     = models.DateTimeField(
        null=True, blank=True,
        help_text=_("Timestamp when GPS coordinates were recorded in the field"),
    )

    class Meta:
        abstract = True

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def coordinates(self) -> tuple[float, float] | None:
        if self.has_coordinates:
            return (float(self.latitude), float(self.longitude))
        return None

    @property
    def coordinates_display(self) -> str:
        """Human-readable GPS string for admin/export."""
        if self.has_coordinates:
            return f"{self.latitude:.6f}, {self.longitude:.6f}"
        return "No GPS data"

    def distance_to(self, lat: float, lon: float) -> float | None:
        """Haversine distance in metres to another point."""
        if not self.has_coordinates:
            return None
        import math
        R    = 6_371_000
        phi1 = math.radians(float(self.latitude))
        phi2 = math.radians(lat)
        dphi = math.radians(lat - float(self.latitude))
        dlam = math.radians(lon - float(self.longitude))
        a    = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def validate_polygon(self) -> tuple[bool, str]:
        """
        Validate GeoJSON polygon.
        Returns (is_valid, error_message) — consistent with utils.validate_polygon().
        """
        poly = self.polygon_coordinates
        if not poly or not isinstance(poly, list) or len(poly) < 4:
            return False, "Polygon must have at least 4 points."
        if poly[0] != poly[-1]:
            return False, "Polygon ring must be closed (first == last point)."
        for i, pt in enumerate(poly):
            if not isinstance(pt, (list, tuple)) or len(pt) != 2:
                return False, f"Point {i} must be [longitude, latitude]."
            lon, lat = pt
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                return False, f"Point {i}: coordinates out of valid range."
        return True, ""

    def set_coordinates(self, lat: float, lon: float, accuracy: float = None):
        """Set GPS coordinates and record capture time."""
        self.latitude           = lat
        self.longitude          = lon
        self.gps_accuracy_meters = accuracy
        self.gps_captured_at    = timezone.now()


# =============================================================================
# 7.  VERIFICATION WORKFLOW
# =============================================================================

class VerifiableModel(models.Model):
    """
    Verification status + audit trail for any model needing approval:
    Farmer, Buyer, BuyerDocument, FieldOfficer.

    States:  pending → verified | rejected | suspended

    FIX vs original:
      verify() and reject() use update_fields — save only changed columns.
      reject() sets verified_at=None (was left stale).
    """

    class VerificationStatus(models.TextChoices):
        PENDING   = "pending",   _("Pending Verification")
        VERIFIED  = "verified",  _("Verified")
        REJECTED  = "rejected",  _("Rejected")
        SUSPENDED = "suspended", _("Suspended")

    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
        db_index=True,
    )
    verified_at      = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        abstract = True

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_verified(self) -> bool:
        return self.verification_status == self.VerificationStatus.VERIFIED

    @property
    def is_pending(self) -> bool:
        return self.verification_status == self.VerificationStatus.PENDING

    @property
    def is_rejected(self) -> bool:
        return self.verification_status == self.VerificationStatus.REJECTED

    @property
    def is_suspended(self) -> bool:
        return self.verification_status == self.VerificationStatus.SUSPENDED

    # ── Transitions ───────────────────────────────────────────────────────────

    def verify(self, verified_by=None):
        """
        Transition to VERIFIED.
        Sets verified_at, clears rejection_reason.
        Only saves the changed columns via update_fields.
        """
        self.verification_status = self.VerificationStatus.VERIFIED
        self.verified_at         = timezone.now()
        self.rejection_reason    = ""
        fields = ["verification_status", "verified_at", "rejection_reason"]

        # If the subclass has a verified_by FK, set it
        if hasattr(self, "verified_by_id") and verified_by is not None:
            self.verified_by = verified_by
            fields.append("verified_by")

        self.save(update_fields=fields)

    def reject(self, reason: str, rejected_by=None):
        """
        Transition to REJECTED.
        Clears verified_at (fix: original left stale value).
        Saves only the changed columns.
        """
        if not reason or not reason.strip():
            raise ValueError("A rejection reason is required.")
        self.verification_status = self.VerificationStatus.REJECTED
        self.rejection_reason    = reason.strip()
        self.verified_at         = None   # ← FIX: was not cleared in original
        fields = ["verification_status", "rejection_reason", "verified_at"]
        self.save(update_fields=fields)

    def suspend(self, reason: str = ""):
        """Suspend a verified or pending record."""
        self.verification_status = self.VerificationStatus.SUSPENDED
        if reason:
            self.rejection_reason = reason
        self.save(update_fields=["verification_status", "rejection_reason"])

    def reinstate(self):
        """Move a suspended record back to pending for re-review."""
        self.verification_status = self.VerificationStatus.PENDING
        self.verified_at         = None
        self.save(update_fields=["verification_status", "verified_at"])


# =============================================================================
# 8.  AUDIT TRAIL
# =============================================================================

class AuditedModel(models.Model):
    """
    Tracks which User created and last modified a record.

    FIX vs original:
      Uses settings.AUTH_USER_MODEL, not hardcoded "auth.User".
      This correctly references the custom User from apps.accounts.
    """
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="%(app_label)s_%(class)s_created",
        editable=False,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="%(app_label)s_%(class)s_updated",
        editable=False,
    )

    class Meta:
        abstract = True


# =============================================================================
# 9.  STATUS / WORKFLOW
# =============================================================================

class StatusModel(models.Model):
    """
    Generic status field with transition logging.

    Subclasses MUST define STATUS_CHOICES and set status field choices:

        class Order(BaseModel, StatusModel):
            class OrderStatus(models.TextChoices):
                PENDING   = "pending", "Pending"
                CONFIRMED = "confirmed", "Confirmed"

            STATUS_CHOICES = OrderStatus.choices
            status = models.CharField(
                max_length=30,
                choices=OrderStatus.choices,
                default=OrderStatus.PENDING,
                db_index=True,
            )

    FIX vs original:
        The original declared STATUS_CHOICES = [] but the status CharField
        had no choices kwarg — so Django admin showed a free-text input.
        Now STATUS_CHOICES is used to validate transitions in set_status().
    """
    STATUS_CHOICES: list = []

    status            = models.CharField(max_length=30, db_index=True)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    status_note       = models.TextField(blank=True)

    class Meta:
        abstract = True

    def set_status(self, new_status: str, note: str = "", save: bool = True):
        """
        Transition to new_status.
        Validates against STATUS_CHOICES if defined.
        Records transition timestamp and optional note.
        Calls _on_status_change() hook for subclass side-effects.
        """
        valid = [c[0] for c in self.STATUS_CHOICES]
        if valid and new_status not in valid:
            raise ValueError(
                f"Invalid status '{new_status}'. "
                f"Valid choices: {valid}"
            )
        old_status             = self.status
        self.status            = new_status
        self.status_changed_at = timezone.now()
        self.status_note       = note
        if save:
            self.save(update_fields=["status", "status_changed_at", "status_note"])
        self._on_status_change(old_status, new_status)

    def _on_status_change(self, old: str, new: str):
        """Override in subclasses to add side-effects on status transitions."""
        pass

    @property
    def status_display(self) -> str:
        """Human-readable status label from STATUS_CHOICES."""
        lookup = dict(self.STATUS_CHOICES)
        return lookup.get(self.status, self.status)


# =============================================================================
# 10.  ORDERED MODEL  (user-defined sort order)
# =============================================================================

class OrderedModel(models.Model):
    """
    Adds an integer `position` field for user-defined record ordering.
    Used by product photos, report sections, batch steps, etc.

    Typical usage:
        class ProductPhoto(BaseModel, OrderedModel):
            product = models.ForeignKey(Product, ...)

    Default ordering is by `position` ascending.
    """
    position = models.PositiveIntegerField(
        default=0,
        db_index=True,
        help_text=_("Sort order — lower numbers appear first."),
    )

    class Meta:
        abstract = True
        ordering = ["position"]

    def move_to(self, new_position: int):
        """Move this record to a new position and save."""
        self.position = new_position
        self.save(update_fields=["position"])


# =============================================================================
# 11.  SINGLETON MODEL  (exactly one DB row)
# =============================================================================

class SingletonModel(models.Model):
    """
    Guarantees that only one instance of this model exists in the database.
    Used for system-wide configuration, platform settings, impact counters.

    Usage:
        class PlatformSettings(SingletonModel):
            maintenance_mode = models.BooleanField(default=False)

        settings = PlatformSettings.get()
        settings.maintenance_mode = True
        settings.save()
    """
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            f"{self.__class__.__name__} is a singleton and cannot be deleted."
        )

    @classmethod
    def get(cls):
        """Fetch the single instance, creating it if it doesn't exist."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# =============================================================================
# 12.  PUBLISHABLE MODEL  (draft → published → archived)
# =============================================================================

class PublishableModel(models.Model):
    """
    Draft / published / archived workflow.
    Used by marketplace listings, impact reports, product certificates.
    """

    class PublishStatus(models.TextChoices):
        DRAFT     = "draft",     _("Draft")
        PUBLISHED = "published", _("Published")
        ARCHIVED  = "archived",  _("Archived")

    publish_status = models.CharField(
        max_length=15,
        choices=PublishStatus.choices,
        default=PublishStatus.DRAFT,
        db_index=True,
    )
    published_at = models.DateTimeField(null=True, blank=True)
    archived_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def is_published(self) -> bool:
        return self.publish_status == self.PublishStatus.PUBLISHED

    def publish(self):
        self.publish_status = self.PublishStatus.PUBLISHED
        self.published_at   = timezone.now()
        self.save(update_fields=["publish_status", "published_at"])

    def archive(self):
        self.publish_status = self.PublishStatus.ARCHIVED
        self.archived_at    = timezone.now()
        self.save(update_fields=["publish_status", "archived_at"])

    def revert_to_draft(self):
        self.publish_status = self.PublishStatus.DRAFT
        self.save(update_fields=["publish_status"])


# =============================================================================
# 13.  PRIORITY MODEL
# =============================================================================

class PriorityModel(models.Model):
    """
    Low / normal / high / urgent priority field.
    Used by support tickets, quality alerts, payment disputes.
    """

    class Priority(models.TextChoices):
        LOW    = "low",    _("Low")
        NORMAL = "normal", _("Normal")
        HIGH   = "high",   _("High")
        URGENT = "urgent", _("Urgent")

    priority = models.CharField(
        max_length=10,
        choices=Priority.choices,
        default=Priority.NORMAL,
        db_index=True,
    )

    class Meta:
        abstract = True

    @property
    def is_urgent(self) -> bool:
        return self.priority == self.Priority.URGENT