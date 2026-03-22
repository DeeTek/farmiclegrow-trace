"""
apps/staff/services/application_service.py

Business logic for StaffApplication approval and rejection.

Two public functions:
  approve_application(application, approved_by)  → StaffProfile
  reject_application(application, rejected_by, reason)

Design rules followed:
  - Everything inside a single database transaction (atomic).
  - Emails are sent via transaction.on_commit() so they never fire
    on a rolled-back transaction.
  - Admin keys in nothing — the profile is built entirely from the
    data the applicant already submitted on their application.
  - The applicant sets their own password via the setup link email.
    No password is ever generated or stored in plaintext here.
  - Application records are never deleted.
"""
from __future__ import annotations

import logging
from datetime import date

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.staff.models import (
    ApplicationStatus,
    ProfileStatus,
    StaffApplication,
    StaffProfile,
    StaffRole,
)

User   = get_user_model()
logger = logging.getLogger("apps.staff")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVE
# ─────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def approve_application(
    application: StaffApplication,
    approved_by: User,
) -> StaffProfile:
    """
    Approve a StaffApplication.

    What this does, in order:
      1. Guard — makes sure we are not approving twice or re-approving a rejection.
      2. Guard — makes sure no User account already exists for this email.
      3. Create a User account for the applicant.
           - Password is set to unusable (blank) — the applicant will set
             their own via the setup link.
           - The User.role field is set to match the intended_role on
             the application so JWT tokens carry the right claim immediately.
      4. Generate a staff_id.
           Format: FG-{REGION_CODE}-{YEAR}-{SEQ:04d}
           e.g. FG-ASH-2024-0001
      5. Create the StaffProfile linked to both the new User and the
         source application. Status starts at PENDING (admin activates
         separately when the person is ready to start).
      6. Mark the application APPROVED, stamp reviewed_at, record who did it.
      7. Build a signed password setup link (via accounts.services).
      8. Send the approval email on_commit — never fires if the DB rolls back.

    Returns the created StaffProfile.
    """
    from apps.staff.tasks import send_approval_email

    # ── Guards ────────────────────────────────────────────────────────────────
    if application.status == ApplicationStatus.APPROVED:
        raise ValueError("This application has already been approved.")

    if application.status == ApplicationStatus.REJECTED:
        raise ValueError("A rejected application cannot be approved.")

    if User.objects.filter(email__iexact=application.email).exists():
        raise ValueError(
            f"A user account with the email '{application.email}' already exists."
        )

    if StaffProfile.objects.filter(ghana_card_number=application.ghana_card_number).exists():
        raise ValueError(
            "A staff profile with this Ghana Card number is already registered."
        )

    # ── Create User ───────────────────────────────────────────────────────────
    first, *rest = application.full_name.strip().split()
    user = User.objects.create(
        email      = application.email.lower(),
        first_name = first,
        last_name  = " ".join(rest),
        is_active  = True,
        # role is a CharField on the User model (from the accounts app).
        # We keep it in sync with the staff role so JWT claims are correct.
        role       = application.intended_role,
    )
    user.set_unusable_password()
    user.save(update_fields=["password"])

    # ── Generate staff_id ─────────────────────────────────────────────────────
    staff_id = _generate_staff_id(application.preferred_region)

    # ── Create StaffProfile ───────────────────────────────────────────────────
    profile = StaffProfile.objects.create(
        user               = user,
        staff_id           = staff_id,
        role               = application.intended_role,
        ghana_card_number  = application.ghana_card_number,
        phone              = application.phone,
        status             = ProfileStatus.PENDING,
        source_application = application,
    )

    # ── Mark application approved ─────────────────────────────────────────────
    application.status      = ApplicationStatus.APPROVED
    application.reviewed_by = approved_by
    application.save(update_fields=["status", "reviewed_by"])

    # ── Email (on_commit — safe from rollback) ────────────────────────────────
    setup_link = _build_setup_link(user)

    transaction.on_commit(lambda: send_approval_email(
        to_email   = user.email,
        full_name  = application.full_name,
        staff_id   = staff_id,
        role       = profile.get_role_display(),
        setup_link = setup_link,
    ))

    logger.info(
        "application_approved | application=%s | staff_id=%s | role=%s | approved_by=%s",
        application.pk, staff_id, profile.role, approved_by.pk,
    )

    return profile


# ─────────────────────────────────────────────────────────────────────────────
# REJECT
# ─────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def reject_application(
    application: StaffApplication,
    rejected_by: User,
    reason:      str,
) -> None:
    """
    Reject a StaffApplication.

    What this does:
      1. Guard — cannot reject an already-approved application.
      2. Validates that a reason was provided (required, not optional).
      3. Marks the application REJECTED, stamps reviewed_by.
      4. Sends a rejection email on_commit with the admin's reason.

    The application record is kept permanently in the database.
    No account is created. Nothing is deleted.
    """
    from apps.staff.tasks import send_rejection_email

    # ── Guards ────────────────────────────────────────────────────────────────
    if application.status == ApplicationStatus.APPROVED:
        raise ValueError("An approved application cannot be rejected.")

    if not reason or not reason.strip():
        raise ValueError("A rejection reason is required.")

    # ── Mark application rejected ─────────────────────────────────────────────
    application.status      = ApplicationStatus.REJECTED
    application.reviewed_by = rejected_by
    application.save(update_fields=["status", "reviewed_by"])

    # ── Email (on_commit) ─────────────────────────────────────────────────────
    transaction.on_commit(lambda: send_rejection_email(
        to_email  = application.email,
        full_name = application.full_name,
        reason    = reason,
    ))

    logger.info(
        "application_rejected | application=%s | rejected_by=%s",
        application.pk, rejected_by.pk,
    )


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _generate_staff_id(preferred_region: str) -> str:
    """
    Generate a unique, sequential staff ID.

    Format:  FG-{REGION_CODE}-{YEAR}-{SEQ:04d}
    Example: FG-ASH-2024-0001

    REGION_CODE is the first three letters of preferred_region, uppercased.
    SEQ restarts at 1 each calendar year, scoped to the same region+year prefix.
    """
    year        = date.today().year
    region_code = (preferred_region[:3] if preferred_region else "GH").upper()
    prefix      = f"FG-{region_code}-{year}-"

    last = (
        StaffProfile.objects
        .filter(staff_id__startswith=prefix)
        .order_by("-staff_id")
        .values_list("staff_id", flat=True)
        .first()
    )

    seq = 1
    if last:
        try:
            seq = int(last.rsplit("-", 1)[-1]) + 1
        except (ValueError, IndexError):
            seq = 1

    return f"{prefix}{seq:04d}"


def _build_setup_link(user: User) -> str:

    try:
        from apps.accounts.services import build_setup_link
        return build_setup_link(user)
    except ImportError:
        logger.warning(
            "_build_setup_link: accounts.services not available — using placeholder"
        )
        return f"https://example.com/setup-password/?user={user.pk}"