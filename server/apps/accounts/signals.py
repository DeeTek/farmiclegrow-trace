"""
accounts/signals.py

Django signals for the accounts app.
Connected in apps.py → AccountsConfig.ready().

Signals handled:

  post_save (User)
    → create_user_profile     — creates a UserProfile row for every new User
    → notify_admin_new_user   — logs new registrations (extend to send email)

  email_confirmed (allauth)
    → on_email_confirmed      — marks user as active after email verification
                                logs the confirmation event

  social_account_added (allauth)
    → on_social_account_added — logs when a user links a new social provider

  social_account_removed (allauth)
    → on_social_account_removed — logs when a social account is unlinked

  password_changed (allauth)
    → on_password_changed     — clears all cached lockout state for the user
                                logs the password change

  user_logged_out (allauth)
    → on_user_logged_out      — logs logout events for audit trail

NOTE: signals that write to the DB (create_user_profile) use
      update_fields or get_or_create to be idempotent — safe to run
      multiple times without creating duplicate rows.
"""

import logging

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from allauth.account.signals import (
    email_confirmed,
    password_changed,
    user_logged_out,
)
from allauth.socialaccount.signals import (
    social_account_added,
    social_account_removed,
)

User   = get_user_model()
logger = logging.getLogger(__name__)


# =============================================================================
# USER CREATION
# =============================================================================

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """
    Creates a UserProfile (or whichever profile model you have) for every
    new User row.

    If you don't have a UserProfile model yet, remove the profile block
    and keep only the logging — the signal is still useful for audit logs.

    Using get_or_create makes this idempotent — safe with fixtures,
    management commands, and tests that call User.objects.create().
    """
    if not created:
        return

    logger.info(
        "user_created | pk=%s | email=%s",
        instance.pk, instance.email,
    )

    # ── Create a linked profile row if you have one ────────────
    # Uncomment and adjust the model import when you add a profile model.
    #
    # from apps.accounts.models import UserProfile
    # UserProfile.objects.get_or_create(
    #     user=instance,
    #     defaults={
    #         'full_name': instance.get_full_name(),
    #     },
    # )


@receiver(post_save, sender=User)
def notify_admin_new_user(sender, instance, created, **kwargs):
    """
    Fires when a brand-new user row is saved.
    Extend this to send an admin notification email, trigger a webhook,
    or push a metric to your analytics service.
    """
    if not created:
        return

    logger.info(
        "new_user_registered | pk=%s | email=%s | social=%s",
        instance.pk,
        instance.email,
        not instance.has_usable_password(),
    )

    # Example: send admin email (uncomment to activate)
    # from django.core.mail import mail_admins
    # mail_admins(
    #     subject=f"New user: {instance.email}",
    #     message=f"User pk={instance.pk} registered at {instance.date_joined}",
    # )


# =============================================================================
# EMAIL VERIFICATION
# =============================================================================

@receiver(email_confirmed)
def on_email_confirmed(request, email_address, **kwargs):
    """
    Fires after allauth marks an email address as verified.

    We activate the user account here rather than at registration time —
    ensures a user cannot log in until they have confirmed they own the email.

    Note: allauth's email flow normally handles this itself via
    ACCOUNT_EMAIL_VERIFICATION = 'mandatory'. This signal is the hook
    if you need side effects (e.g. send a welcome email, create initial data).
    """
    user = email_address.user

    if not user.is_active:
        user.is_active = True
        user.save(update_fields=['is_active'])
        logger.info(
            "user_activated_after_email_confirm | pk=%s | email=%s",
            user.pk, email_address.email,
        )

    logger.info(
        "email_confirmed | pk=%s | email=%s",
        user.pk, email_address.email,
    )

    # Example: send welcome email on first verification
    # from apps.accounts.tasks import send_welcome_email
    # send_welcome_email.delay(user.pk)


# =============================================================================
# SOCIAL ACCOUNT EVENTS
# =============================================================================

@receiver(social_account_added)
def on_social_account_added(request, sociallogin, **kwargs):
    """
    Fires when a user successfully links a new social account.
    Used for audit logging and analytics.
    """
    user     = sociallogin.user
    provider = sociallogin.account.provider

    logger.info(
        "social_account_linked | pk=%s | email=%s | provider=%s",
        user.pk, user.email, provider,
    )


@receiver(social_account_removed)
def on_social_account_removed(request, socialaccount, **kwargs):
    """
    Fires when a user unlinks a social account via DisconnectView.
    Used for audit logging.
    """
    user     = socialaccount.user
    provider = socialaccount.provider

    logger.info(
        "social_account_unlinked | pk=%s | email=%s | provider=%s",
        user.pk, user.email, provider,
    )


# =============================================================================
# PASSWORD EVENTS
# =============================================================================

@receiver(password_changed)
def on_password_changed(request, user, **kwargs):
    """
    Fires after allauth successfully changes the user's password.

    We clear the login-lockout cache counters here so that:
    1. A user who locked themselves out and then resets via email
       can immediately log in with the new password.
    2. Support staff who reset a password on behalf of a user don't
       have to manually clear the lockout.
    """
    from .utils import clear_lockout, get_client_ip, normalize_email

    email = normalize_email(user.email)
    ip    = get_client_ip(request) if request else 'unknown'
    clear_lockout(email, ip)

    logger.info(
        "password_changed | pk=%s | email=%s | ip=%s",
        user.pk, user.email, ip,
    )


# =============================================================================
# LOGOUT
# =============================================================================

@receiver(user_logged_out)
def on_user_logged_out(request, user, **kwargs):
    """
    Fires when allauth logs a user out.
    user may be None if the session had already expired.
    Used for audit logging.
    """
    if user:
        logger.info(
            "user_logged_out | pk=%s | email=%s",
            user.pk, user.email,
        )
    else:
        logger.info("user_logged_out | user=anonymous (session expired)")
