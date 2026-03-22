"""
apps/accounts/services.py

Business logic layer for account creation, staff/farmer onboarding,
and field-officer application review.

Design principles:
  • No plaintext passwords stored or logged anywhere.
  • Staff / field-officer accounts use a signed setup-link (token), not a password.
  • Farmers receive a generated password stored only in FarmerCredential (not User).
  • All multi-step DB operations are wrapped in atomic transactions.
  • Email delivery is dispatched via tasks.dispatch_email (Celery + on_commit).
"""

from __future__ import annotations

import logging
import secrets
import string

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.conf import settings

from allauth.account.models import EmailAddress

User   = get_user_model()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _generate_password(length: int = 14) -> str:
    """
    Cryptographically random password.

    Used ONLY for farmers who may receive credentials on paper / verbally
    in low-connectivity areas.  Never generated for staff or field officers.
    Never stored on the User model or written to logs.
    """
    alphabet = (
        string.ascii_uppercase.replace("O", "").replace("I", "")
        + string.ascii_lowercase.replace("l", "")
        + string.digits.replace("0", "").replace("1", "")
    )
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _build_setup_link(user: User) -> str:
    """
    Return a signed password-setup URL for the given user.

    Valid for settings.PASSWORD_RESET_TIMEOUT seconds (Django default: 3 days).
    The user clicks this link to set their own password — no credential is
    generated or stored server-side.
    """
    uid   = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    base  = getattr(settings, "FRONTEND_BASE_URL", "").rstrip("/")
    return f"{base}/setup-password/{uid}/{token}/"


# ---------------------------------------------------------------------------
# Farmer onboarding
# ---------------------------------------------------------------------------

@transaction.atomic
def onboard_farmer(validated_data: dict, registered_by: User) -> dict:
    """
    Create a FARMER user + FarmerCredential record.

    A generated password is used here because farmers may receive credentials
    on paper / verbally in low-connectivity rural areas.

    Security posture:
      - Plain password is stored only in FarmerCredential (farmer domain).
      - It is NEVER written to User.password in plaintext — only the hash is.
      - It is NEVER logged.
      - It is returned once to the caller (the view) for display/handover.

    Returns a dict the view serializes into the API response.
    """
    from apps.farmers.models import FarmerCredential  # lazy — avoids circular import

    email      = validated_data.get("email")
    phone      = validated_data.get("phone")
    ghana_card = validated_data["ghana_card_number"].strip()

    stored_email     = email or (
        f"farmer.{phone.replace('+', '').replace(' ', '')}@farmiclegrow.internal"
    )
    login_identifier = email if email else phone
    plain_password   = _generate_password(length=10)

    user = User(
        email      = stored_email,
        first_name = validated_data.get("first_name", ""),
        last_name  = validated_data.get("last_name", ""),
        phone      = phone,
        role       = User.Role.FARMER,
        region     = validated_data.get("region") or None,
        district   = validated_data.get("district") or None,
        is_active  = True,
    )
    user.set_password(plain_password)   # hashed — plaintext not on User model
    user.save()

    # allauth email row — pre-verified, no confirmation email needed
    EmailAddress.objects.get_or_create(
        user     = user,
        email    = stored_email,
        defaults = {"verified": True, "primary": True},
    )

    FarmerCredential.objects.create(
        farmer               = user,
        ghana_card_number    = ghana_card,
        generated_password   = plain_password,  # farmer-domain record only
        login_identifier     = login_identifier,
        registered_by        = registered_by,
        must_change_password = True,
    )

    logger.info(
        "farmer_onboarded | pk=%s | identifier_type=%s | by=%s",
        user.pk,
        "email" if email else "phone",
        registered_by.pk,
    )

    return {
        "user":               user,
        "login_identifier":   login_identifier,
        "generated_password": plain_password,   # returned once to caller
        "ghana_card_number":  ghana_card,
    }


# ---------------------------------------------------------------------------
# Field officer application review
# ---------------------------------------------------------------------------

@transaction.atomic
def approve_field_officer(application, approved_by: User) -> User:
    """
    Approve a pending FieldOfficerApplication:
      1. Create the User with an unusable password.
      2. Mark application approved.
      3. Dispatch a setup-link email so the user sets their own password.

    Email fires via Celery after the transaction commits (on_commit safety).
    """
    from apps.accounts.models import FieldOfficerApplication
    from .tasks import dispatch_email

    if application.status != FieldOfficerApplication.Status.PENDING:
        raise ValueError("Only pending applications can be approved.")

    user = User(
        email      = application.email,
        first_name = application.first_name,
        last_name  = application.last_name or "",
        phone      = application.phone or None,
        role       = User.Role.FIELD_OFFICER,
        region     = application.region or None,
        district   = application.district or None,
        is_active  = True,
    )
    user.set_unusable_password()  # no credential generated — user sets via link
    user.save()

    EmailAddress.objects.get_or_create(
        user     = user,
        email    = user.email,
        defaults = {"verified": True, "primary": True},
    )

    application.status      = FieldOfficerApplication.Status.APPROVED
    application.reviewed_at = timezone.now()
    application.reviewed_by = approved_by
    application.user        = user
    application.save(update_fields=["status", "reviewed_at", "reviewed_by", "user"])

    dispatch_email(
        template_prefix = "accounts/field_officer_approved",
        to_email        = user.email,
        context         = {
            "first_name": user.first_name,
            "email":      user.email,
            "setup_link": _build_setup_link(user),
        },
    )

    logger.info(
        "field_officer_approved | application=%s | user_pk=%s | by=%s",
        application.pk, user.pk, approved_by.pk,
    )
    return user


@transaction.atomic
def reject_field_officer(application, rejected_by: User, rejection_reason: str) -> None:
    """
    Reject a pending FieldOfficerApplication and notify the applicant.
    No user account is created.
    Email fires via Celery after the transaction commits.
    """
    from apps.accounts.models import FieldOfficerApplication
    from .tasks import dispatch_email

    if application.status != FieldOfficerApplication.Status.PENDING:
        raise ValueError("Only pending applications can be rejected.")

    application.status           = FieldOfficerApplication.Status.REJECTED
    application.reviewed_at      = timezone.now()
    application.reviewed_by      = rejected_by
    application.rejection_reason = rejection_reason
    application.save(update_fields=["status", "reviewed_at", "reviewed_by", "rejection_reason"])

    dispatch_email(
        template_prefix = "accounts/field_officer_rejected",
        to_email        = application.email,
        context         = {
            "first_name":       application.first_name,
            "rejection_reason": rejection_reason,
        },
    )

    logger.info(
        "field_officer_rejected | application=%s | by=%s",
        application.pk, rejected_by.pk,
    )


# ---------------------------------------------------------------------------
# Direct staff creation  (admin bypasses application flow)
# ---------------------------------------------------------------------------

@transaction.atomic
def create_staff_member(validated_data: dict) -> User:
    """
    Admin directly creates a field officer or warehouse manager.

    Account is created with an unusable password.
    A setup-link email is dispatched (Celery, post-commit) so the user
    sets their own password on first login.
    No plaintext password is generated, stored, or transmitted.
    """
    from .tasks import dispatch_email

    user = User(
        email      = validated_data["email"],
        first_name = validated_data.get("first_name", ""),
        last_name  = validated_data.get("last_name", ""),
        phone      = validated_data.get("phone") or None,
        role       = validated_data["role"],
        region     = validated_data.get("region") or None,
        district   = validated_data.get("district") or None,
        is_active  = True,
    )
    user.set_unusable_password()
    user.save()

    EmailAddress.objects.get_or_create(
        user     = user,
        email    = user.email,
        defaults = {"verified": True, "primary": True},
    )

    dispatch_email(
        template_prefix = "accounts/staff_created",
        to_email        = user.email,
        context         = {
            "first_name": user.first_name,
            "email":      user.email,
            "setup_link": _build_setup_link(user),
        },
    )

    logger.info(
        "staff_manually_created | pk=%s | role=%s",
        user.pk, user.role,
    )
    return user


# ---------------------------------------------------------------------------
# Farmer password reset  (admin-initiated)
# ---------------------------------------------------------------------------

@transaction.atomic
def reset_farmer_password(farmer: User, reset_by: User) -> str:
    """
    Generate a new credential for a farmer and update FarmerCredential.

    Security posture:
      - Plain password is NOT logged.
      - It is returned once to the caller (view) for display/handover.
      - must_change_password is reset to True.

    Returns the new plain password (caller surfaces it in the API response).
    """
    from apps.farmers.models import FarmerCredential

    plain_password = _generate_password(length=10)
    farmer.set_password(plain_password)
    farmer.save(update_fields=["password"])

    try:
        cred = FarmerCredential.objects.get(farmer=farmer)
        cred.generated_password   = plain_password
        cred.must_change_password = True
        cred.save(update_fields=["generated_password", "must_change_password"])
    except FarmerCredential.DoesNotExist:
        pass  # legacy farmer without a credential record — acceptable

    logger.info(
        "farmer_password_reset | farmer_pk=%s | by=%s",
        farmer.pk, reset_by.pk,
    )
    return plain_password