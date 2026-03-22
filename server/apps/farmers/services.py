"""
apps/farmers/services.py

Business logic for farmer account management.

Scope:
  • onboard_farmer         — field officer registers a new farmer
  • reset_farmer_password  — admin resets a farmer's credential
  • impersonate_farmer     — admin acquires a short-lived token to act as a farmer

Key type contract (matches models.py):
  • FarmerCredential.farmer  → FK to Farmer (not User)
  • Farmer.ghana_card_number → unique field on Farmer (not on FarmerCredential)
  • onboard_farmer creates:  User → Farmer → FarmerCredential (in that order)
  • reset_farmer_password receives a Farmer instance (not User)
  • impersonate_farmer receives a Farmer instance (not User)
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

from allauth.account.models import EmailAddress

from apps.accounts.services import generate_password

User   = get_user_model()
logger = logging.getLogger(__name__)

_INTERNAL_EMAIL_DOMAIN = "farmiclegrow.internal"


# ---------------------------------------------------------------------------
# Farmer onboarding
# ---------------------------------------------------------------------------

@transaction.atomic
def onboard_farmer(validated_data: dict, registered_by: User) -> dict:
    """
    Create a User → Farmer profile → FarmerCredential, in that order.

    Flow:
      1. Create User (auth account — phone login, internal email placeholder).
      2. Create Farmer profile linked to the User (holds ghana_card_number,
         community, district, region, GPS, etc.).
      3. Create FarmerCredential (holds generated_password + login_identifier).
      4. Create allauth EmailAddress row (pre-verified, no email sent).

    Type contract:
      FarmerCredential.farmer → Farmer instance (not User).
      Farmer.ghana_card_number → stored on Farmer, not on FarmerCredential.

    Returns a dict the view serializes into the API response for credential
    card printing.
    """
    from apps.farmers.models import Farmer, FarmerCredential

    email      = validated_data.get("email")
    phone      = validated_data.get("phone")
    ghana_card = validated_data["ghana_card_number"].strip().upper()

    # allauth requires a unique email on every User row — use internal
    # placeholder when no real email is provided.
    stored_email     = email or (
        f"farmer.{phone.replace('+', '').replace(' ', '')}@{_INTERNAL_EMAIL_DOMAIN}"
    )
    login_identifier = email if email else phone
    plain_password   = generate_password(length=10)

    # ── Step 1: User (auth account) ───────────────────────────────────────────
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
    user.set_password(plain_password)  # only the hash lands on User.password
    user.save()

    # allauth email row — pre-verified, no confirmation email
    EmailAddress.objects.get_or_create(
        user     = user,
        email    = stored_email,
        defaults = {"verified": True, "primary": True},
    )

    # ── Step 2: Farmer profile ────────────────────────────────────────────────
    farmer = Farmer.objects.create(
        user              = user,
        registered_by     = registered_by,
        ghana_card_number = ghana_card,
        first_name        = validated_data.get("first_name", ""),
        last_name         = validated_data.get("last_name", ""),
        phone_number      = phone or "",
        community         = validated_data.get("community", ""),
        district          = validated_data.get("district", ""),
        region            = validated_data.get("region", ""),
    )

    # ── Step 3: FarmerCredential ──────────────────────────────────────────────
    # FarmerCredential.farmer is a FK to Farmer, not User.
    # ghana_card_number lives on Farmer — not repeated here.
    FarmerCredential.objects.create(
        farmer               = farmer,           # Farmer instance
        login_identifier     = login_identifier,
        generated_password   = plain_password,   # plain text for credential card only
        registered_by        = registered_by,
        must_change_password = False,            # farmers use printed cards
    )

    logger.info(
        "farmer_onboarded | farmer_pk=%s | user_pk=%s | identifier_type=%s | by=%s",
        farmer.pk, user.pk,
        "email" if email else "phone",
        registered_by.pk,
    )

    return {
        "farmer":             farmer,
        "user":               user,
        "login_identifier":   login_identifier,
        "generated_password": plain_password,  # returned once — printed on credential card
        "ghana_card_number":  ghana_card,
    }


# ---------------------------------------------------------------------------
# Farmer password reset  (admin-initiated)
# ---------------------------------------------------------------------------

@transaction.atomic
def reset_farmer_password(farmer: "Farmer", reset_by: User) -> str:
    """
    Generate a new credential for a farmer and update FarmerCredential.

    Args:
        farmer:   Farmer instance (not User) — as returned by get_object()
                  in FarmerViewSet.
        reset_by: The admin User who initiated the reset.

    Returns the new plain password for credential card printing.
    Plain password is NOT logged.
    """
    from apps.farmers.models import FarmerCredential

    plain_password = generate_password(length=10)

    # Update password on the linked User account
    farmer.user.set_password(plain_password)
    farmer.user.save(update_fields=["password"])

    # Update credential record — farmer FK is Farmer instance
    try:
        cred = FarmerCredential.objects.get(farmer=farmer)
        cred.generated_password = plain_password
        cred.save(update_fields=["generated_password"])
    except FarmerCredential.DoesNotExist:
        # Legacy farmer without a credential record — create one now
        FarmerCredential.objects.create(
            farmer             = farmer,
            login_identifier   = farmer.user.phone or farmer.user.email,
            generated_password = plain_password,
            registered_by      = reset_by,
        )

    logger.info(
        "farmer_password_reset | farmer_pk=%s | by=%s",
        farmer.pk, reset_by.pk,
    )
    return plain_password


# ---------------------------------------------------------------------------
# Admin impersonation
# ---------------------------------------------------------------------------

def impersonate_farmer(farmer: "Farmer", admin: User) -> dict:
    """
    Issue a short-lived JWT scoped to the farmer's linked User account.

    Args:
        farmer: Farmer instance (not User) — as returned by get_object()
                in FarmerViewSet.
        admin:  The admin User who initiated impersonation.

    Token claims use farmer.user (the auth User) for user_id, email, etc.

    Returns:
        {
            "access":          "<short-lived JWT>",
            "farmer_pk":       farmer.pk,
            "impersonated_by": admin.pk,
            "expires_in":      <seconds>,
        }
    """
    from rest_framework_simplejwt.tokens import AccessToken

    lifetime = getattr(
        settings,
        "IMPERSONATION_TOKEN_LIFETIME",
        timedelta(minutes=30),
    )

    # Token is scoped to the farmer's User account
    user = farmer.user

    token = AccessToken()
    token.set_exp(lifetime=lifetime)

    # Standard JWT claims
    token["user_id"]  = str(user.pk)
    token["email"]    = user.email
    token["role"]     = user.role
    token["region"]   = user.region
    token["district"] = user.district

    # Impersonation audit claims — used by AuditMiddleware and ImpersonationStatusView
    token["impersonated_by"] = str(admin.pk)
    token["impersonation"]   = True

    logger.info(
        "farmer_impersonation_started | farmer_pk=%s | user_pk=%s | admin_pk=%s",
        farmer.pk, user.pk, admin.pk,
    )

    return {
        "access":          str(token),
        "farmer_pk":       str(farmer.pk),
        "impersonated_by": str(admin.pk),
        "expires_in":      int(lifetime.total_seconds()),
    }