"""
apps/accounts/adapters.py

Two adapters that hook into django-allauth's extension points.

CustomAccountAdapter
    Extends DefaultAccountAdapter (email login, registration, password reset).
    - get_email_confirmation_url  → points to the Next.js frontend
    - get_password_reset_url      → points to the Next.js frontend with base64 uid
    - send_mail                   → converts allauth's base36 uid to base64,
                                    then dispatches via Celery (non-blocking)
    - is_open_for_signup          → toggle via settings.ACCOUNT_ALLOW_REGISTRATION
    - clean_email                 → normalise to lowercase

CustomSocialAccountAdapter
    Extends DefaultSocialAccountAdapter (Google / Facebook / Apple).
    - pre_social_login  → auto-connects social account to an existing
                          email-based account (prevents duplicate users)
    - populate_user     → splits provider "name" → first / last name;
                          reads Apple name from session on first login
    - save_user         → marks email verified for trusted providers
                          (Google, Apple) — skips confirmation email
    - is_open_for_signup → same ACCOUNT_ALLOW_REGISTRATION gate

Settings required in settings.py:
    ACCOUNT_ADAPTER       = 'apps.accounts.adapters.CustomAccountAdapter'
    SOCIALACCOUNT_ADAPTER = 'apps.accounts.adapters.CustomSocialAccountAdapter'
    FRONTEND_BASE_URL     = 'https://yourapp.com'
"""

import logging

from django.conf import settings
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.account.models import EmailAddress
from allauth.exceptions import ImmediateHttpResponse

from rest_framework.response import Response
from rest_framework import status

logger = logging.getLogger(__name__)


# =============================================================================
# EMAIL / PASSWORD ADAPTER
# =============================================================================

class CustomAccountAdapter(DefaultAccountAdapter):
    """
    Customises allauth's email and password flow.

    Email templates live at:
        templates/accounts/<prefix>_subject.txt
        templates/accounts/<prefix>_message.txt
        templates/accounts/<prefix>.html          ← optional HTML version

    The uid in password-reset emails is converted from allauth's internal
    base36 format to urlsafe_base64 so PasswordResetConfirmSerializer and
    the Next.js frontend use the same uid encoding Django REST Framework
    expects (urlsafe_base64_encode → urlsafe_base64_decode → int pk).
    """

    # ── Email confirmation URL ────────────────────────────────

    def get_email_confirmation_url(self, request, emailconfirmation):
        """
        URL embedded in the verification email the user clicks.
        key  = allauth's EmailConfirmation.key (UUID-style string).

        The Next.js /verify-email page calls
        POST /auth/register/verify-email/ with this key in the body.
        """
        return f"{settings.FRONTEND_BASE_URL}/verify-email?key={emailconfirmation.key}"

    # ── Password reset URL ────────────────────────────────────

    def get_password_reset_url(self, request, user, temp_key):
        """
        URL embedded in the password-reset email the user clicks.
        temp_key = allauth's one-time password reset token.
        uid      = urlsafe_base64-encoded user pk (NOT allauth's base36).

        The Next.js /reset-password page calls
        POST /auth/password/reset/confirm/ with { uid, token, new_password }.
        CustomPasswordResetConfirmSerializer decodes uid back to base36 for allauth.
        """
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        return (
            f"{settings.FRONTEND_BASE_URL}/reset-password"
            f"?uid={uid}&token={temp_key}"
        )

    # ── Email sending ─────────────────────────────────────────

    def send_mail(self, template_prefix, email, context):
        """
        Replaces allauth's default synchronous send_mail with a Celery task.

        Key transformation: allauth puts uid in context as base36
        (e.g. "1k" for pk=56).  We swap it to urlsafe_base64 so the
        frontend URL, email template, and our serializer all use the
        same encoding consistently.

        After normalising context the email is handed off to
        tasks.dispatch_email which wraps the Celery task in on_commit —
        so email is never sent for a transaction that later rolls back.
        """
        from .tasks import dispatch_email

        # Swap allauth's base36 uid → urlsafe_base64
        if "uid" in context and context.get("user"):
            context["uid"] = urlsafe_base64_encode(force_bytes(context["user"].pk))

        context.setdefault("frontend_url", settings.FRONTEND_BASE_URL)

        dispatch_email(
            template_prefix = template_prefix,
            to_email        = email,
            context         = _make_serialisable(context),
        )

    # ── Registration gate ─────────────────────────────────────

    def is_open_for_signup(self, request):
        """
        Set ACCOUNT_ALLOW_REGISTRATION = False in settings.py to close
        new signups without a code deploy (useful for invite-only periods
        or maintenance windows).
        """
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)

    # ── Email normalisation ───────────────────────────────────

    def clean_email(self, email):
        """
        Lowercase + strip before allauth stores the email.
        Prevents duplicate accounts from case variations (John@ vs john@).
        """
        return super().clean_email(email).lower().strip()


# =============================================================================
# SOCIAL AUTH ADAPTER
# =============================================================================

class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Customises allauth's social login flow for Google, Facebook, Apple.

    Three key behaviours:

    1. pre_social_login  — auto-connects a social account to an existing
                           password-based account with the same email.
                           Without this, a user who signed up with
                           john@example.com + password, then tries
                           "Sign in with Google", would get a collision
                           error instead of a smooth account merge.

    2. populate_user     — Facebook often provides only a combined "name"
                           field. This hook splits it into first + last.
                           Apple sends name parts only on the very first
                           login; we read them from session where
                           AppleLoginView cached them.

    3. save_user         — Google and Apple verify email ownership before
                           granting a token, so we mark the allauth
                           EmailAddress as verified immediately. Facebook
                           is excluded because it allows unverified emails.
    """

    TRUSTED_PROVIDERS = {"google", "apple"}

    # ── Auto-connect social → existing account ────────────────

    def pre_social_login(self, request, sociallogin):
        """
        If any email from the social provider already exists in our DB,
        silently connect the social account to that existing user.

        Flow:
          LoginView receives the social token → allauth creates a temporary
          SocialLogin object → this hook fires → we find the matching user →
          call sociallogin.connect() → raise ImmediateHttpResponse to
          short-circuit allauth's normal path and return JSON to the view.

        After connect(), the user has both their password account AND the
        social account linked. Subsequent social logins go through the
        normal is_existing=True path.
        """
        if sociallogin.is_existing:
            return  # already linked — nothing to do

        if not sociallogin.email_addresses:
            return  # provider gave no email — can't auto-connect

        for email_obj in sociallogin.email_addresses:
            try:
                existing_user = EmailAddress.objects.get(
                    email__iexact=email_obj.email
                ).user
            except EmailAddress.DoesNotExist:
                continue

            logger.info(
                "social_auto_connect | provider=%s | user_pk=%s",
                sociallogin.account.provider,
                existing_user.pk,
            )
            sociallogin.connect(request, existing_user)

            # Short-circuit allauth — return a success response immediately.
            raise ImmediateHttpResponse(
                Response(
                    {"message": "Social account linked to your existing account.", "connected": True},
                    status=status.HTTP_200_OK,
                )
            )

    # ── Name splitting ────────────────────────────────────────

    def populate_user(self, request, sociallogin, data):
        """
        Splits a provider's combined 'name' into first_name + last_name
        when the provider doesn't supply them separately.

        Provider notes:
          Google   → gives given_name + family_name separately ✓
          Facebook → often gives only 'name' (combined) — needs splitting
          Apple    → gives givenName + familyName on FIRST login only;
                     AppleLoginView.post() caches them in request.session.
        """
        user = super().populate_user(request, sociallogin, data)

        if user.first_name or user.last_name:
            return user  # provider gave separate fields — use as-is

        full_name = data.get("name", "").strip()
        if full_name:
            parts           = full_name.split(" ", 1)
            user.first_name = parts[0]
            user.last_name  = parts[1] if len(parts) > 1 else ""

        # Apple: name available in session only on very first login
        if sociallogin.account.provider == "apple":
            first = request.session.pop("apple_first_name", None)
            last  = request.session.pop("apple_last_name", None)
            if first:
                user.first_name = first
            if last:
                user.last_name  = last

        return user

    # ── Email verified flag for trusted providers ─────────────

    def save_user(self, request, sociallogin, form=None):
        """
        After creating/updating the user row, mark their EmailAddress as
        verified for trusted providers — skipping allauth's confirmation email.

        Google and Apple require the user to own the email before granting
        an OAuth token. Facebook allows accounts with unverified emails,
        so we don't auto-verify for that provider.
        """
        user     = super().save_user(request, sociallogin, form=form)
        provider = sociallogin.account.provider

        if provider in self.TRUSTED_PROVIDERS and user.email:
            email_address, created = EmailAddress.objects.get_or_create(
                user          = user,
                email__iexact = user.email,
                defaults      = {
                    "email":    user.email,
                    "primary":  True,
                    "verified": True,
                },
            )
            if not email_address.verified:
                email_address.verified = True
                email_address.save(update_fields=["verified"])

            if created:
                logger.info(
                    "social_email_auto_verified | provider=%s | user_pk=%s",
                    provider, user.pk,
                )

        return user

    # ── Registration gate ─────────────────────────────────────

    def is_open_for_signup(self, request, sociallogin):
        """
        Same ACCOUNT_ALLOW_REGISTRATION flag applies to social signups.
        Existing users can still log in via social even when signup is closed
        (allauth only calls this hook for new account creation).
        """
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)


# =============================================================================
# Helpers
# =============================================================================

def _make_serialisable(context: dict) -> dict:
    """
    Celery serialises task arguments as JSON.  Strip any non-serialisable
    objects (Django model instances, request objects) from the context dict
    so the task payload is safe to queue.

    Only primitive types, dicts, and lists pass through.
    Model instances should be passed as plain dicts by the caller — this
    function is a safety net, not the primary contract.
    """
    import json

    safe = {}
    for key, value in context.items():
        try:
            json.dumps(value)
            safe[key] = value
        except (TypeError, ValueError):
            logger.debug(
                "adapter_context_strip | key=%s | type=%s — not JSON-serialisable, dropped",
                key, type(value).__name__,
            )
    return safe