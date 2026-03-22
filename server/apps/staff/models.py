from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models.abstract import BaseModel


# ═══════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════

class StaffRole(models.TextChoices):
    AGRONOMIST    = "agronomist", _("Agronomist")
    FIELD_OFFICER = "field_officer", _("Field Officer")
    SUPERVISOR    = "supervisor", _("Supervisor")


class ProfileStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    ACTIVE  = "active", _("Active")
    SUSPENDED = "suspended", _("Suspended")
    TERMINATED = "terminated", _("Terminated")


class ApplicationStatus(models.TextChoices):
    SUBMITTED    = "submitted", _("Submitted")
    APPROVED     = "approved", _("Approved")
    REJECTED     = "rejected", _("Rejected")


class DeploymentStatus(models.TextChoices):
    ACTIVE    = "active", _("Active")
    COMPLETED = "completed", _("Completed")


# ═══════════════════════════════════════
# STAFF PROFILE
# ═══════════════════════════════════════

class StaffProfile(BaseModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="staff_profile",
    )

    staff_id = models.CharField(max_length=30, unique=True, db_index=True)

    role = models.CharField(max_length=25, choices=StaffRole.choices)

    ghana_card_number = models.CharField(max_length=30, unique=True)
    gender = models.CharField(
        max_length=10,
        choices=[("male", "Male"), ("female", "Female"), ("other", "Other")],
        blank=True,
    )
    phone = models.CharField(max_length=20, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    profile_photo = models.ImageField(
        upload_to="staff/photos/%Y/%m/", null=True, blank=True
    )

    status = models.CharField(
        max_length=20,
        choices=ProfileStatus.choices,
        default=ProfileStatus.PENDING,
    )

    source_application = models.OneToOneField(
        "StaffApplication",
        on_delete=models.PROTECT,
        related_name="resulting_profile",
    )

    class Meta(BaseModel.Meta):
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.staff_id} [{self.role}]"


# ═══════════════════════════════════════
# STAFF APPLICATION
# ═══════════════════════════════════════

class StaffApplication(BaseModel):
    full_name = models.CharField(max_length=200)

    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20)

    ghana_card_number = models.CharField(max_length=30, unique=True, db_index=True, help_text=_("Ghana National ID card number (GHA-XXXXXXXXX-X)."),)

    intended_role = models.CharField(
        max_length=25,
        choices=[
            (StaffRole.AGRONOMIST, "Agronomist"),
            (StaffRole.FIELD_OFFICER, "Field Officer"),
        ],
    )
    educational_level  = models.CharField(
        max_length=30,
        choices=[
            ("none",       _("No Formal Education")),
            ("primary",    _("Primary")),
            ("jhs",        _("Junior High School")),
            ("shs",        _("Senior High School")),
            ("vocational", _("Vocational / Technical")),
            ("tertiary",   _("Tertiary")),
        ],
    )

    preferred_region = models.CharField(max_length=100)

    status = models.CharField(
        max_length=20,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.SUBMITTED,
    )

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_staff_applications",
    )

    class Meta(BaseModel.Meta):
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.full_name} → {self.intended_role} [{self.status}]"





