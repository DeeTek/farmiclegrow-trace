"""
apps/core/base.py  —  FarmicleGrow-Trace Platform
(also referred to as concrete.py)

Concrete base model classes built from abstract mixins.
Import these in domain apps — not the raw abstract mixins.

Usage:
    from apps.core.base import (
        BaseTracedModel,
        BasePersonModel,
        BaseOrganisationModel,
        BaseDocumentModel,
        BaseTransactionModel,
    )
"""
from django.db import models
from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _

from .abstract import (
    BaseModel, CodedModel, GeoModel,
    VerifiableModel, AuditedModel, StatusModel,
)


# =============================================================================
# BASE TRACED MODEL  (UUID + Timestamps + SoftDelete + Code + Audit)
# =============================================================================

class BaseTracedModel(BaseModel, CodedModel, AuditedModel):
    """
    Base for any entity that needs traceability:
    UUID PK · Timestamps · SoftDelete · Auto-code · Audit (created_by/updated_by)

    Extend for: TraceRecord, CropSeason, FarmVisit, WarehouseIntake, etc.
    """
    notes = models.TextField(blank=True)

    class Meta:
        abstract = True


# =============================================================================
# BASE PERSON MODEL  (UUID + Code + Verifiable + Personal fields)
# =============================================================================

class BasePersonModel(BaseModel, CodedModel, VerifiableModel):
    """
    Base for any human entity: Farmer, FieldOfficer, StaffMember.

    Provides:
    ─ UUID PK + timestamps + soft-delete
    ─ Auto-generated code (set CODE_PREFIX on subclass)
    ─ Verification workflow (pending → verified / rejected)
    ─ Personal fields: name, gender, DOB, national_id, photo, phone
    ─ full_name property
    """

    class Gender(models.TextChoices):
        MALE   = "male",   _("Male")
        FEMALE = "female", _("Female")
        OTHER  = "other",  _("Other")

    phone_regex = RegexValidator(
        regex=r"^\+?1?\d{9,15}$",
        message=_("Phone number must be E.164 format: +233XXXXXXXXX"),
    )

    # Identity
    first_name    = models.CharField(max_length=100)
    last_name     = models.CharField(max_length=100)
    gender        = models.CharField(max_length=10, choices=Gender.choices, default=Gender.MALE)
    date_of_birth = models.DateField(null=True, blank=True)
    national_id   = models.CharField(max_length=50, blank=True)
    profile_photo = models.ImageField(
        upload_to="profiles/photos/%Y/%m/", null=True, blank=True,
    )

    # Contact
    phone_number    = models.CharField(validators=[phone_regex], max_length=17, db_index=True)
    alternate_phone = models.CharField(validators=[phone_regex], max_length=17, blank=True)
    email           = models.EmailField(blank=True)

    notes = models.TextField(blank=True)

    class Meta:
        abstract = True

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self):
        code = getattr(self, "code", "")
        return f"{code} — {self.full_name}" if code else self.full_name


# =============================================================================
# BASE ORGANISATION MODEL  (UUID + Code + Verifiable + Company fields)
# =============================================================================

class BaseOrganisationModel(BaseModel, CodedModel, VerifiableModel):
    """
    Base for any organisational entity: Buyer, Cooperative, Warehouse, etc.
    """

    phone_regex = RegexValidator(regex=r"^\+?1?\d{9,15}$")

    # Organisation identity
    company_name        = models.CharField(max_length=200, db_index=True)
    registration_number = models.CharField(max_length=100, blank=True)
    vat_number          = models.CharField(max_length=50, blank=True)
    website             = models.URLField(blank=True)
    logo                = models.ImageField(
        upload_to="organisations/logos/", null=True, blank=True,
    )

    # Primary contact
    contact_person = models.CharField(max_length=150, blank=True)
    phone_number   = models.CharField(validators=[phone_regex], max_length=17, blank=True)
    email          = models.EmailField(blank=True)

    # Address
    country       = models.CharField(max_length=100, db_index=True)
    city          = models.CharField(max_length=100, blank=True)
    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    postal_code   = models.CharField(max_length=20, blank=True)

    notes = models.TextField(blank=True)

    class Meta:
        abstract = True

    def __str__(self):
        code = getattr(self, "code", "")
        return f"{code} — {self.company_name}" if code else self.company_name


# =============================================================================
# BASE DOCUMENT MODEL  (UUID + File + Status workflow)
# =============================================================================

class BaseDocumentModel(BaseModel):
    """
    Base for any uploaded document requiring review:
    BuyerDocument, CertificationAudit, PaymentProof, etc.
    """

    class DocumentStatus(models.TextChoices):
        PENDING  = "pending",  _("Pending Review")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        EXPIRED  = "expired",  _("Expired")

    title            = models.CharField(max_length=200)
    file             = models.FileField(upload_to="documents/%Y/%m/")
    document_type    = models.CharField(max_length=50, blank=True, db_index=True)
    status           = models.CharField(
        max_length=20,
        choices=DocumentStatus.choices,
        default=DocumentStatus.PENDING,
        db_index=True,
    )
    expiry_date      = models.DateField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    notes            = models.TextField(blank=True)

    class Meta:
        abstract = True

    @property
    def is_expired(self) -> bool:
        if self.expiry_date:
            from django.utils import timezone
            return timezone.now().date() > self.expiry_date
        return False

    @property
    def is_valid(self) -> bool:
        return self.status == self.DocumentStatus.APPROVED and not self.is_expired

    def approve(self):
        self.status = self.DocumentStatus.APPROVED
        self.save(update_fields=["status"])

    def reject(self, reason: str):
        self.status           = self.DocumentStatus.REJECTED
        self.rejection_reason = reason
        self.save(update_fields=["status", "rejection_reason"])


# =============================================================================
# BASE TRANSACTION MODEL  (UUID + Code + Currency + Status)
# =============================================================================

class BaseTransactionModel(BaseModel, CodedModel, StatusModel):
    """
    Base for financial transactions: Payment, Payout, Refund.

    Provides: UUID PK, timestamps, soft-delete, auto-reference code,
    currency, amount, status workflow.
    """

    class TransactionStatus(models.TextChoices):
        PENDING   = "pending",   _("Pending")
        COMPLETED = "completed", _("Completed")
        FAILED    = "failed",    _("Failed")
        REFUNDED  = "refunded",  _("Refunded")
        CANCELLED = "cancelled", _("Cancelled")

    STATUS_CHOICES = TransactionStatus.choices
    status         = models.CharField(
        max_length=20,
        choices=TransactionStatus.choices,
        default=TransactionStatus.PENDING,
        db_index=True,
    )

    currency = models.CharField(max_length=5, default="GHS")
    amount   = models.DecimalField(max_digits=14, decimal_places=2)
    notes    = models.TextField(blank=True)

    class Meta:
        abstract = True

    def __str__(self):
        code = getattr(self, "code", "")
        return f"{code} — {self.currency} {self.amount}"