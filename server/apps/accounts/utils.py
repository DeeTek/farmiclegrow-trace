"""
accounts/utils.py

All non-view helper code:
  - Login lockout (email permanent DB lock + IP cache lock)
  - Ephemeral MFA token (cache-backed, 5-min, one-time-use)
  - JWT issuance helper
  - TOTP helpers (secret lookup, QR code generation)
  - Recovery code generation
  - jwt_response helper (builds the final HTTP response with cookies)
"""

from __future__ import annotations

import io
import logging

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.conf import settings as django_settings

from rest_framework import status
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from dj_rest_auth.jwt_auth import set_jwt_cookies

from allauth.mfa.models import Authenticator
from allauth.socialaccount.models import SocialAccount

User   = get_user_model()
logger = logging.getLogger(__name__)


# =============================================================================
# LOCKOUT CONSTANTS
# =============================================================================

MAX_ATTEMPTS    : int = 5       # per email
IP_MAX_ATTEMPTS : int = 20      # per IP
LOCKOUT_SECONDS : int = 15 * 60 # 15 minutes (IP lock only)
ATTEMPTS_TTL    : int = 60 * 60 # 1 hour sliding window


# =============================================================================
# REMEMBER ME CONSTANTS
# =============================================================================

REMEMBER_ME_REFRESH_LIFETIME = timedelta(days=30)
NORMAL_REFRESH_LIFETIME      = timedelta(days=7)


# =============================================================================
# EMAIL / IP NORMALISATION
# =============================================================================

def normalize_email(raw: str) -> str:
    """
    Lowercase and strip whitespace from email.
    Prevents lockout bypass via case variation (John@ vs john@).
    Returns empty string if input has no @.
    """
    raw = (raw or "").strip()
    if "@" not in raw:
        return ""
    local, _, domain = raw.partition("@")
    return f"{local.lower()}@{domain.lower()}"


def get_client_ip(request) -> str:
    """
    Extract real client IP, honouring X-Forwarded-For from proxies.
    Returns 'unknown' as a safe fallback.
    """
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


# =============================================================================
# LOCKOUT — cache key helpers
# =============================================================================

def _email_attempts_key(email: str) -> str:
    return f"login_attempts:email:{email}"

def _ip_attempts_key(ip: str) -> str:
    return f"login_attempts:ip:{ip}"

def _ip_lock_key(ip: str) -> str:
    return f"login_locked:ip:{ip}"


# =============================================================================
# LOCKOUT — check
# =============================================================================

def is_email_locked(email: str) -> bool:
    """
    Queries the DATABASE for a permanent email lock.
    Only admin/support can remove a DB lock.
    """
    from apps.accounts.models import AccountLockout
    return AccountLockout.objects.filter(email=email, is_locked=True).exists()


def is_ip_locked(ip: str) -> bool:
    """
    IP lock is cache-based — auto-expires after LOCKOUT_SECONDS (15 min).
    """
    return bool(cache.get(_ip_lock_key(ip)))


# =============================================================================
# LOCKOUT — record failure
# =============================================================================

def record_failure(email: str, ip: str) -> dict[str, int]:
    """
    Increment failure counters for both email and IP.
    When email reaches MAX_ATTEMPTS → permanent DB lock.
    When IP reaches IP_MAX_ATTEMPTS → 15-min cache lock.
    Returns { email_attempts, ip_attempts } so LoginView can warn the user.
    """
    from apps.accounts.models import AccountLockout

    # ── Email counter ──────────────────────────────────────────
    email_att_key = _email_attempts_key(email)
    cache.add(email_att_key, 0, ATTEMPTS_TTL)          # init to 0 if new
    email_attempts: int = cache.incr(email_att_key)    # atomic increment

    if email_attempts >= MAX_ATTEMPTS:
        # Create or re-lock the DB record
        AccountLockout.objects.get_or_create(
            email=email,
            defaults={
                'reason':    'Too many failed login attempts',
                'locked_by': 'system',
                'is_locked': True,
            },
        )
        AccountLockout.objects.filter(email=email).update(
            is_locked=True,
            locked_at=timezone.now(),
            unlocked_at=None,
            locked_by='system',
        )
        logger.warning(
            "login_email_permanently_locked | email=%s | attempts=%d",
            email, email_attempts,
        )

    # ── IP counter ────────────────────────────────────────────
    ip_att_key = _ip_attempts_key(ip)
    cache.add(ip_att_key, 0, ATTEMPTS_TTL)
    ip_attempts: int = cache.incr(ip_att_key)

    if ip_attempts >= IP_MAX_ATTEMPTS:
        cache.set(_ip_lock_key(ip), "1", LOCKOUT_SECONDS)
        logger.warning(
            "login_ip_locked | ip=%s | attempts=%d", ip, ip_attempts
        )

    return {"email_attempts": email_attempts, "ip_attempts": ip_attempts}


# =============================================================================
# LOCKOUT — clear on success
# =============================================================================

def clear_lockout(email: str, ip: str) -> None:
    """
    Clear all cache-based state on successful login.
    Does NOT touch the DB email lock — only admin can clear that.
    """
    cache.delete_many([
        _email_attempts_key(email),
        _ip_attempts_key(ip),
        _ip_lock_key(ip),
    ])


# =============================================================================
# LOCKOUT — admin unlock
# =============================================================================

def unlock_account(email: str, unlocked_by: str = 'admin') -> bool:
    """
    Unlocks a permanently locked email account.
    Called from Django admin action or a support endpoint.
    Returns True if an account was unlocked, False if not found.
    """
    from apps.accounts.models import AccountLockout
    updated = AccountLockout.objects.filter(email=email, is_locked=True).update(
        is_locked=False,
        unlocked_at=timezone.now(),
        locked_by=unlocked_by,
    )
    cache.delete(_email_attempts_key(email))  # reset attempt counter
    if updated:
        logger.info("account_unlocked | email=%s | by=%s", email, unlocked_by)
    return bool(updated)


# =============================================================================
# EPHEMERAL MFA TOKEN
# =============================================================================

_MFA_TOKEN_PREFIX = 'mfa_ephemeral:'
_MFA_TOKEN_TTL    = 300  # 5 minutes


def make_ephemeral_token(user, remember_me: bool = False) -> str:
    """
    Generate a short-lived token stored in cache that proves the user
    passed password auth (step 1) but hasn't completed MFA (step 2) yet.

    Stores { user_pk, remember_me } so the MFA completion views can
    apply the correct JWT lifetime when issuing the final token.

    Called by:
      - LoginView (email login with MFA)
      - SocialJWTResponseMixin (social login with MFA)
    """
    token = get_random_string(64)
    cache.set(
        f"{_MFA_TOKEN_PREFIX}{token}",
        {'user_pk': user.pk, 'remember_me': remember_me},
        _MFA_TOKEN_TTL,
    )
    return token


def resolve_ephemeral_token(token: str) -> tuple:
    """
    Return (User, remember_me) for a valid ephemeral token.
    Does NOT delete the token — use consume_ephemeral_token for that.
    Returns (None, False) if the token is invalid or expired.

    Called by WebAuthnAuthBeginView — needs the user but flow isn't done yet.
    """
    data = cache.get(f"{_MFA_TOKEN_PREFIX}{token}")
    if data is None:
        return None, False
    try:
        user = User.objects.get(pk=data['user_pk'])
        return user, data.get('remember_me', False)
    except User.DoesNotExist:
        return None, False


def consume_ephemeral_token(token: str) -> tuple:
    """
    Return (User, remember_me) and immediately delete the token (one-time use).
    Returns (None, False) if the token is invalid or expired.

    Called by all MFA login completion views:
      TOTPLoginView, RecoveryCodeLoginView, WebAuthnAuthCompleteView
    """
    user, remember_me = resolve_ephemeral_token(token)
    if user:
        cache.delete(f"{_MFA_TOKEN_PREFIX}{token}")
    return user, remember_me


# =============================================================================
# JWT ISSUANCE
# =============================================================================

def get_jwt_for_user(user, remember_me: bool = False) -> dict:
    from apps.accounts.serializers import UserDetailSerializer, ClientSerializer

    refresh = RefreshToken.for_user(user)

    if remember_me:
        refresh.set_exp(lifetime=REMEMBER_ME_REFRESH_LIFETIME)

    is_staff = user.role is not None

    # Custom JWT claims
    refresh['email']       = user.email
    refresh['full_name']   = user.get_full_name()
    refresh['role']        = user.role
    refresh['branch']      = user.branch_id  # ← FK id, no extra DB hit
    refresh['is_social']   = SocialAccount.objects.filter(user=user).exists()
    refresh['mfa_enabled'] = Authenticator.objects.filter(user=user).exists()

    user_data = UserDetailSerializer(user).data if is_staff else ClientSerializer(user).data

    return {
        'access':      str(refresh.access_token),
        'refresh':     str(refresh),
        'user':        user_data,
        'remember_me': remember_me,
    }

# =============================================================================
# JWT RESPONSE — used by all MFA login completion views
# =============================================================================

def jwt_response(user, remember_me: bool = False) -> Response:

    token_data = get_jwt_for_user(user, remember_me=remember_me)
    response   = Response(token_data, status=status.HTTP_200_OK)

    cookie_secure   = not django_settings.DEBUG
    cookie_samesite = getattr(django_settings, 'JWT_AUTH_COOKIE_SAMESITE', 'Lax')
    refresh_max_age = int(
        REMEMBER_ME_REFRESH_LIFETIME.total_seconds() if remember_me
        else NORMAL_REFRESH_LIFETIME.total_seconds()
    )

    response.set_cookie(
        key='af-access', value=token_data['access'],
        httponly=True, secure=cookie_secure, samesite=cookie_samesite,
        max_age=30 * 60,
    )
    response.set_cookie(
        key='af-refresh', value=token_data['refresh'],
        httponly=True, secure=cookie_secure, samesite=cookie_samesite,
        max_age=refresh_max_age,
    )

    return response
# =============================================================================
# TOTP HELPERS
# =============================================================================

def get_totp_authenticator(user):
    """Return the user's active TOTP Authenticator record, or None."""
    try:
        return Authenticator.objects.get(user=user, type=Authenticator.Type.TOTP)
    except Authenticator.DoesNotExist:
        return None


def generate_qr_svg(totp_url: str) -> str:
    """
    Render a TOTP URL as an inline SVG QR code string.
    Returns empty string on failure — client falls back to raw totp_url.
    """
    import qrcode
    import qrcode.image.svg
    factory = qrcode.image.svg.SvgPathImage
    qr  = qrcode.make(totp_url, image_factory=factory, box_size=10)
    buf = io.BytesIO()
    qr.save(buf)
    return buf.getvalue().decode('utf-8')


def generate_recovery_codes(user) -> list:
    """
    Generate a fresh set of recovery codes (count set by MFA_RECOVERY_CODE_COUNT).
    Invalidates any existing codes for this user first.
    Returns a list of plaintext code strings.
    """
    from allauth.mfa.recovery_codes import RecoveryCodes
    rc = RecoveryCodes.activate(user)
    return rc.get_unused_codes()


OTP_TTL_MINUTES : int = 10
OTP_MAX_ATTEMPTS: int = 5   # wrong-code attempts before invalidation


def generate_otp_code() -> str:
    """6-digit numeric OTP using cryptographically secure randomness."""
    import secrets
    return str(secrets.randbelow(900000) + 100000)   # 100000–999999


def send_otp(user, channel: str) -> None:
    """
    Generate a fresh OTP, persist it, and deliver via the requested channel.
    Any existing unused code for this user+channel is deleted first.
    """
    from datetime import timedelta
    from apps.accounts.models import OTPCode

    # Invalidate previous codes
    OTPCode.objects.filter(user=user, channel=channel, is_used=False).delete()

    code       = generate_otp_code()
    expires_at = timezone.now() + timedelta(minutes=OTP_TTL_MINUTES)

    OTPCode.objects.create(
        user=user,
        channel=channel,
        code=code,
        expires_at=expires_at,
    )

    if channel == 'sms':
        _send_sms_otp(user, code)
    else:
        _send_email_otp(user, code)


def _send_sms_otp(user, code: str) -> None:
    """Deliver OTP via Africa's Talking SMS."""
    try:
        import africastalking
        from apps.accounts.models import OTPAuthenticator

        at_username = getattr(django_settings, 'AT_USERNAME', 'sandbox')
        at_api_key  = getattr(django_settings, 'AT_API_KEY',  '')
        at_sender   = getattr(django_settings, 'AT_SENDER_ID', 'AfricanMutual')

        africastalking.initialize(at_username, at_api_key)

        otp_auth = OTPAuthenticator.objects.get(user=user, channel='sms')
        phone    = otp_auth.phone

        message = (
            f"Your African Mutual code: {code}\n"
            f"Valid for {OTP_TTL_MINUTES} minutes. Never share this code."
        )
        africastalking.SMS.send(message, [phone], sender_id=at_sender)
        logger.info("sms_otp_sent | email=%s | phone=***%s", user.email, phone[-4:])

    except Exception as exc:
        logger.error("sms_otp_failed | email=%s | reason=%s", user.email, exc)
        raise


def _send_email_otp(user, code: str) -> None:
    """Deliver OTP via Django email backend."""
    try:
        from django.core.mail import send_mail

        send_mail(
            subject="Your African Mutual verification code",
            message=(
                f"Your verification code is: {code}\n\n"
                f"Valid for {OTP_TTL_MINUTES} minutes. Do not share this code.\n\n"
                "If you did not request this, please ignore this email."
            ),
            from_email=django_settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        logger.info("email_otp_sent | email=%s", user.email)

    except Exception as exc:
        logger.error("email_otp_failed | email=%s | reason=%s", user.email, exc)
        raise


def verify_otp(user, channel: str, code: str) -> bool:
    """
    Validate an OTP code.
    Returns True on success and marks the code as used.
    Returns False if code is wrong, expired, or already used.
    """
    from apps.accounts.models import OTPCode

    try:
        otp = OTPCode.objects.get(user=user, channel=channel, code=code, is_used=False)
    except OTPCode.DoesNotExist:
        return False

    if otp.is_expired:
        otp.delete()
        return False

    otp.is_used = True
    otp.save(update_fields=['is_used'])
    return True


def get_otp_authenticator(user):
    """Return the user's active OTPAuthenticator or None."""
    from apps.accounts.models import OTPAuthenticator
    try:
        return OTPAuthenticator.objects.get(user=user, is_active=True)
    except OTPAuthenticator.DoesNotExist:
        return None
