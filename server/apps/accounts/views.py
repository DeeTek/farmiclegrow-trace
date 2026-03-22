from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils.http import urlsafe_base64_decode, int_to_base36
from django.utils.encoding import force_str
from django.utils.translation import gettext_lazy as _

from rest_framework import status, generics, permissions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import AnonRateThrottle
from rest_framework.exceptions import Throttled
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from dj_rest_auth.registration.views import (
    RegisterView,
    ResendEmailVerificationView,
    VerifyEmailView,
    SocialLoginView,
    SocialConnectView,
)
from dj_rest_auth.views import (
    LoginView    as BaseLoginView,
    LogoutView   as BaseLogoutView,
    PasswordResetView        as BasePasswordResetView,
    PasswordResetConfirmView as BasePasswordResetConfirmView,
)
from dj_rest_auth.registration.serializers import (
    RegisterSerializer,
    ResendEmailVerificationSerializer,
)
from dj_rest_auth.jwt_auth import set_jwt_cookies

from allauth.account.models import EmailConfirmation, EmailAddress
from allauth.mfa.models import Authenticator
import pyotp  # replaces allauth.mfa.totp — secret generation + code verification
from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.providers.google.views   import GoogleOAuth2Adapter
from allauth.socialaccount.providers.facebook.views import FacebookOAuth2Adapter
from allauth.socialaccount.providers.apple.views    import AppleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client  import OAuth2Client

from .serializers import (
   CustomLoginSerializer,
   CustomRegisterSerializer,
   CustomTokenObtainPairSerializer,
   CustomJWTSerializer,
   UserDetailSerializer,
   CustomRegisterSerializer,
   CustomResendEmailVerificationSerializer,
   PasswordResetConfirmSerializer,
   SocialLoginSerializer,
   GoogleLoginSerializer,
   FacebookLoginSerializer,
   AppleLoginSerializer,
   SocialConnectSerializer,
   SocialAccountSerializer,
)

from .models import (
    EmailVerificationAttempt,
    BlacklistedEmailKey,
    PasswordResetAttempt,
    BlacklistedPasswordResetToken,
)
from .utils import (
    MAX_ATTEMPTS,
    clear_lockout,
    get_client_ip,
    get_jwt_for_user,
    get_totp_authenticator,
    generate_qr_svg,
    generate_recovery_codes,
    get_otp_authenticator,
    send_otp,
    verify_otp,
    is_email_locked,
    is_ip_locked,
    jwt_response,
    make_ephemeral_token,
    normalize_email,
    record_failure,
    resolve_ephemeral_token,
    consume_ephemeral_token,
)

User   = get_user_model()
logger = logging.getLogger(__name__)

MAX_RESEND_ATTEMPTS = 20
MAX_RESET_ATTEMPTS  = 20

class ResendEmailRateThrottle(AnonRateThrottle):
    scope = "resend_email"

    def throttle_failure(self):
        raise Throttled(detail={"message": "Too many resend attempts. Please try again later."})


class PasswordResetThrottle(AnonRateThrottle):
    scope = "password_reset"

    def throttle_failure(self):
        raise Throttled(detail={"message": "Too many reset attempts. Please try again later."})

class CustomRegisterView(RegisterView):
    serializer_class = CustomRegisterSerializer

    def post(self, request, *args, **kwargs):
        super().post(request, *args, **kwargs)
        return Response(
            {"message": "Account created successfully. Please check your email to verify your account."},
            status=status.HTTP_201_CREATED,
        )

class LoginView(BaseLoginView):
    """
    POST /account/v1/login/
    Body: { "email", "password", "remember_me" (optional, default false) }

    No MFA  → { "access", "refresh", "user", ... } + HttpOnly cookies
    Has MFA → { "mfa_required": true, "ephemeral_token": "...", "available_methods": [...] }
    """
    serializer_class = CustomLoginSerializer

    def post(self, request, *args, **kwargs):
        raw_email   = request.data.get("email", "")
        email       = normalize_email(raw_email)
        ip          = get_client_ip(request)
        remember_me = bool(request.data.get("remember_me", False))

        # ── Lockout checks ──────────────────────────────────────────────
        if ip and is_ip_locked(ip):
            logger.info("login_blocked_ip | ip=%s", ip)
            return Response(
                {"message": "Too many login attempts. Please try again later."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        if email and is_email_locked(email):
            logger.info("login_blocked_email | email=%s | ip=%s", email, ip)
            return Response(
                {"message": "Too many login attempts. Please try again later."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        # ── Validate credentials ────────────────────────────────────────

        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            if email:
                counts         = record_failure(email, ip)
                email_attempts = counts["email_attempts"]
                remaining      = max(MAX_ATTEMPTS - email_attempts, 0)
                logger.info(
                    "login_failure | email=%s | ip=%s | attempts=%d | remaining=%d",
                    email, ip, email_attempts, remaining,
                )
                errors = dict(serializer.errors)
                if 0 < remaining <= 2:
                    errors["warning"] = (
                        f"{remaining} attempt(s) remaining before your "
                        "account is temporarily locked."
                    )
                return Response(errors, status=status.HTTP_400_BAD_REQUEST)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = serializer.validated_data["user"]
        clear_lockout(email, ip)

        # ── MFA detection ───────────────────────────────────────────────
        totp_active     = Authenticator.objects.filter(user=user, type=Authenticator.Type.TOTP).exists()
        webauthn_active = Authenticator.objects.filter(user=user, type=Authenticator.Type.WEBAUTHN).exists()
        otp_auth        = get_otp_authenticator(user)
        has_mfa         = totp_active or webauthn_active or otp_auth

        if has_mfa:
            ephemeral_token = make_ephemeral_token(user, remember_me=remember_me)

            methods = []
            if totp_active:
                methods.append("totp")
            if webauthn_active:
                methods.append("webauthn")
            if otp_auth:
                methods.append(f"otp_{otp_auth.channel}")   # "otp_sms" or "otp_email"

            otp_message = ""
            if otp_auth:
                # Auto-send OTP immediately — like a bank
                try:
                    send_otp(user, otp_auth.channel)
                    otp_message = (
                        "A verification code has been sent to your phone."
                        if otp_auth.channel == "sms"
                        else "A verification code has been sent to your email."
                    )
                except Exception as exc:
                    logger.error(
                        "login_otp_send_failed | email=%s | channel=%s | reason=%s",
                        email, otp_auth.channel, exc,
                    )
                    return Response(
                        {"message": "Failed to send verification code. Please try again."},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    )

            logger.info(
                "login_mfa_required | email=%s | ip=%s | methods=%s",
                email, ip, methods,
            )
            return Response(
                {
                    "mfa_required":      True,
                    "ephemeral_token":   ephemeral_token,
                    "available_methods": methods,
                    "message": otp_message or (
                        "MFA verification required. "
                        "Complete verification with your chosen method."
                    ),
                },
                status=status.HTTP_200_OK,
            )

        # ── No MFA — issue JWT ──────────────────────────────────────────
        logger.info(
            "login_success | email=%s | ip=%s | remember_me=%s",
            email, ip, remember_me,
        )
        return jwt_response(user, remember_me=remember_me)

class LogoutView(BaseLogoutView):
    def post(self, request, *args, **kwargs):
        super().post(request, *args, **kwargs)
        return Response(
            {"message": "You have been signed out successfully."},
            status=status.HTTP_200_OK,
        )

class CustomResendEmailVerificationView(ResendEmailVerificationView):
    serializer_class = CustomResendEmailVerificationSerializer
    throttle_classes = [ResendEmailRateThrottle]

    def post(self, request, *args, **kwargs):
        email = request.data.get("email", "")

        attempts, _ = EmailVerificationAttempt.objects.get_or_create(email=email)
        if attempts.resend_count >= MAX_RESEND_ATTEMPTS:
            logger.info("Max resend attempts reached | email=%s", email)
            return Response(
                {"message": "Maximum resend attempts reached. Please contact support."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        old_confirmation = EmailConfirmation.objects.filter(
            email_address__email__iexact=email,
            email_address__verified=False,
        ).first()

        if old_confirmation:
            BlacklistedEmailKey.objects.create(key=old_confirmation.key, email=email)
            old_confirmation.delete()
            attempts.resend_count += 1
            attempts.save()
            logger.info(
                "Resend email requested | email=%s | attempts=%s/%s | ip=%s",
                email, attempts.resend_count, MAX_RESEND_ATTEMPTS,
                request.META.get("REMOTE_ADDR"),
            )

        super().post(request, *args, **kwargs)

        return Response(
            {"message": "Verification email has been resent. Please check your inbox."},
            status=status.HTTP_200_OK,
        )

class CustomVerifyEmailView(VerifyEmailView):

    def post(self, request, *args, **kwargs):
        key = request.data.get("key", "")

        if BlacklistedEmailKey.objects.filter(key=key).exists():
            return Response(
                {"message": "This verification link has expired. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        confirmation = EmailConfirmation.objects.filter(key=key).first()
        if not confirmation:
            return Response(
                {"message": "Invalid or expired verification link. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            super().post(request, *args, **kwargs)
        except Exception:
            return Response(
                {"message": "This email is already verified. Please log in."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"message": "Email verified successfully. You can now log in."},
            status=status.HTTP_200_OK,
        )

class PasswordResetView(BasePasswordResetView):

    def post(self, request, *args, **kwargs):
        email = request.data.get("email", "")

        attempts, _ = PasswordResetAttempt.objects.get_or_create(email=email)
        if attempts.reset_count >= MAX_RESET_ATTEMPTS:
            logger.info("Max password reset attempts reached | email=%s", email)
            return Response(
                {"message": "Maximum password reset attempts reached. Please contact support."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            serializer.save()
            attempts.reset_count += 1
            attempts.save()
            logger.info(
                "Password reset requested | email=%s | attempts=%s/%s | ip=%s",
                email, attempts.reset_count, MAX_RESET_ATTEMPTS,
                get_client_ip(request),
            )
        except Exception as e:
            logger.error("Password reset email failed | email=%s | reason=%s", email, e)

        return Response(
            {"message": "If an account with that email exists, a password reset link has been sent."},
            status=status.HTTP_200_OK,
        )

class PasswordResetConfirmView(BasePasswordResetConfirmView):
    serializer_class = PasswordResetConfirmSerializer

    def post(self, request, *args, **kwargs):
        token = request.data.get("token", "")

        if BlacklistedPasswordResetToken.objects.filter(token=token).exists():
            return Response(
                {"message": "This reset link has expired. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        user = serializer.user

        try:
            BlacklistedPasswordResetToken.objects.create(email=user.email, token=token)
            PasswordResetAttempt.objects.filter(email=user.email).delete()
            logger.info(
                "Password reset successful | email=%s | ip=%s",
                user.email, get_client_ip(request),
            )
        except Exception as e:
            logger.warning("Post-reset cleanup failed | reason=%s", e)

        return Response(
            {"message": "Password has been reset successfully. You can now log in."},
            status=status.HTTP_200_OK,
        )


class SocialJWTResponseMixin:

    def get_response(self):
        user     = self.user
        provider = getattr(self, '_provider_name', 'social')

        if Authenticator.objects.filter(user=user).exists():
            ephemeral_token = make_ephemeral_token(user)
            methods = list(
                Authenticator.objects.filter(user=user)
                .values_list('type', flat=True)
            )
            logger.info(
                "social_login_mfa_required | provider=%s | email=%s | methods=%s",
                provider, user.email, methods,
            )
            return Response(
                {
                    "mfa_required":      True,
                    "ephemeral_token":   ephemeral_token,
                    "available_methods": methods,
                    "message": (
                        "MFA verification required. "
                        "Complete verification with your chosen method."
                    ),
                },
                status=status.HTTP_200_OK,
            )

        token_data = get_jwt_for_user(user)
        response   = Response(token_data, status=status.HTTP_200_OK)
        set_jwt_cookies(response, token_data['access'], token_data['refresh'])

        logger.info("social_login_success | provider=%s | email=%s", provider, user.email)
        return response

class GoogleLoginView(SocialJWTResponseMixin, SocialLoginView):
    adapter_class    = GoogleOAuth2Adapter
    callback_url = settings.GOOGLE_OAUTH_CALLBACK_URL
    client_class     = OAuth2Client
    serializer_class = GoogleLoginSerializer
    _provider_name   = 'google'

class GoogleConnectView(SocialConnectView):
    adapter_class      = GoogleOAuth2Adapter
    callback_url       = settings.GOOGLE_OAUTH_CALLBACK_URL
    client_class       = OAuth2Client
    serializer_class   = GoogleLoginSerializer
    permission_classes = [permissions.IsAuthenticated]

class FacebookLoginView(SocialJWTResponseMixin, SocialLoginView):
    adapter_class    = FacebookOAuth2Adapter
    callback_url     = settings.FACEBOOK_OAUTH_CALLBACK_URL
    client_class     = OAuth2Client
    serializer_class = FacebookLoginSerializer
    _provider_name   = 'facebook'

class FacebookConnectView(SocialConnectView):
    adapter_class      = FacebookOAuth2Adapter
    callback_url       = settings.FACEBOOK_OAUTH_CALLBACK_URL
    client_class       = OAuth2Client
    serializer_class   = FacebookLoginSerializer
    permission_classes = [permissions.IsAuthenticated]

class AppleLoginView(SocialJWTResponseMixin, SocialLoginView):
    adapter_class    = AppleOAuth2Adapter
    callback_url     = settings.APPLE_OAUTH_CALLBACK_URL
    client_class     = OAuth2Client
    serializer_class = AppleLoginSerializer
    _provider_name   = 'apple'

    def post(self, request, *args, **kwargs):
        # Apple sends name ONLY on the first login ever — cache it immediately.
        first_name = request.data.get('first_name')
        last_name  = request.data.get('last_name')
        if first_name or last_name:
            request.session['apple_first_name'] = first_name
            request.session['apple_last_name']  = last_name
        return super().post(request, *args, **kwargs)

class AppleConnectView(SocialConnectView):
    adapter_class      = AppleOAuth2Adapter
    callback_url       = settings.APPLE_OAUTH_CALLBACK_URL
    client_class       = OAuth2Client
    serializer_class   = AppleLoginSerializer
    permission_classes = [permissions.IsAuthenticated]

class LinkedAccountsView(generics.ListAPIView):
    """GET /auth/social/accounts/ — all providers linked to the current user."""
    serializer_class   = SocialAccountSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return SocialAccount.objects.filter(user=self.request.user)

class DisconnectView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, pk):
        try:
            social_account = SocialAccount.objects.get(pk=pk, user=request.user)
        except SocialAccount.DoesNotExist:
            return Response(
                {"message": _("Social account not found.")},
                status=status.HTTP_404_NOT_FOUND,
            )

        user             = request.user
        remaining_social = SocialAccount.objects.filter(user=user).exclude(pk=pk).count()
        has_password     = user.has_usable_password()

        if not has_password and remaining_social == 0:
            return Response(
                {
                    "message": _(
                        "Cannot disconnect your only login method. "
                        "Set a password first, or connect another social account."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        provider = social_account.provider
        social_account.delete()

        logger.info("social_disconnect | user=%s | provider=%s", user.email, provider)
        return Response(
            {"message": _(f"{provider.title()} account disconnected.")},
            status=status.HTTP_200_OK,
        )

class MFAStatusView(APIView):
    """GET /auth/mfa/status/ — current MFA setup for the authenticated user."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        totp_enabled  = get_totp_authenticator(user) is not None
        webauthn_keys = Authenticator.objects.filter(
            user=user, type=Authenticator.Type.WEBAUTHN
        ).count()

        recovery_codes_left = 0
        try:
            rc_auth = Authenticator.objects.get(
                user=user, type=Authenticator.Type.RECOVERY_CODES
            )
            recovery_codes_left = len(rc_auth.wrap().get_unused_codes())
        except Authenticator.DoesNotExist:
            pass

        otp_auth   = get_otp_authenticator(user)
        otp_method = otp_auth.channel if otp_auth else None  # 'sms', 'email', or None

        return Response({
            'totp_enabled':        totp_enabled,
            'webauthn_keys':       webauthn_keys,
            'recovery_codes_left': recovery_codes_left,
            'otp_method':          otp_method,
            'mfa_active':          totp_enabled or webauthn_keys > 0 or bool(otp_auth),
        })

class TOTPSetupView(APIView):
    """
    GET /auth/mfa/totp/setup/
    Returns secret + otpauth:// URL + inline SVG QR code.
    Secret is cached for 10 min — NOT saved to DB until TOTPVerifyView confirms it.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        if get_totp_authenticator(user):
            return Response(
                {'message': _('TOTP is already enabled on your account.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        secret   = pyotp.random_base32()
        totp_url = (
            f"otpauth://totp/African%20Mutual:{user.email}"
            f"?secret={secret}&issuer=African%20Mutual"
            f"&algorithm=SHA1&digits=6&period=30"
        )

        cache.set(f"totp_pending:{user.pk}", secret, 600)

        try:
            qr_svg = generate_qr_svg(totp_url)
        except Exception:
            qr_svg = ''

        return Response({
            'secret':      secret,
            'totp_url':    totp_url,
            'qr_code_svg': qr_svg,
        })

class TOTPVerifyView(APIView):
    """
    POST /auth/mfa/totp/verify/
    Body: { "code": "123456" }
    On success: saves TOTP to DB + returns 10 recovery codes.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        code = request.data.get('code', '').strip()
        if not code:
            return Response(
                {'message': _('A 6-digit code is required.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user   = request.user
        secret = cache.get(f"totp_pending:{user.pk}")

        if not secret:
            return Response(
                {'message': _('TOTP setup session expired. Please start setup again.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not pyotp.TOTP(secret).verify(code, valid_window=1):
            return Response(
                {'message': _('Invalid code. Check your authenticator app and try again.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        Authenticator.objects.get_or_create(
            user=user,
            type=Authenticator.Type.TOTP,
            defaults={'data': {'secret': secret}},
        )
        cache.delete(f"totp_pending:{user.pk}")

        recovery_codes = generate_recovery_codes(user)
        logger.info("totp_activated | email=%s", user.email)

        return Response({
            'message':        _('TOTP authentication has been enabled.'),
            'recovery_codes': recovery_codes,
        })

class TOTPDeactivateView(APIView):
    """
    POST /auth/mfa/totp/deactivate/
    Body: { "code": "123456" }
    Requires a valid current code — prevents unauthorised deactivation.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        code = request.data.get('code', '').strip()
        if not code:
            return Response(
                {'message': _('A 6-digit code is required to confirm deactivation.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user          = request.user
        authenticator = get_totp_authenticator(user)

        if not authenticator:
            return Response(
                {'message': _('TOTP is not enabled on your account.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not pyotp.TOTP(authenticator.data['secret']).verify(code, valid_window=1):
            return Response(
                {'message': _('Invalid code. TOTP was not deactivated.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        authenticator.delete()
        logger.info("totp_deactivated | email=%s", user.email)

        return Response({'message': _('TOTP authentication has been disabled.')})

class TOTPLoginView(APIView):
    """
    POST /auth/mfa/totp/login/
    Body: { "code": "123456", "ephemeral_token": "xxx" }
    AllowAny — user has no JWT yet. ephemeral_token is the auth proof.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        code            = request.data.get('code', '').strip()
        ephemeral_token = request.data.get('ephemeral_token', '').strip()

        if not code or not ephemeral_token:
            return Response(
                {'message': _('Both code and ephemeral_token are required.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user, remember_me = consume_ephemeral_token(ephemeral_token)
        if not user:
            return Response(
                {'message': _('Invalid or expired MFA session. Please log in again.')},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        authenticator = get_totp_authenticator(user)
        if not authenticator:
            return Response(
                {'message': _('TOTP is not enabled for this account.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not pyotp.TOTP(authenticator.data['secret']).verify(code, valid_window=1):
            return Response(
                {'message': _('Invalid TOTP code.')},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        logger.info(
            "mfa_totp_login_success | email=%s | remember_me=%s",
            user.email, remember_me,
        )
        return jwt_response(user, remember_me=remember_me)

class RecoveryCodesView(APIView):
    """
    GET  /auth/mfa/recovery-codes/ → list remaining unused codes
    POST /auth/mfa/recovery-codes/ → generate fresh set (old invalidated)
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            rc_auth = Authenticator.objects.get(
                user=request.user,
                type=Authenticator.Type.RECOVERY_CODES,
            )
            codes = rc_auth.wrap().get_unused_codes()
        except Authenticator.DoesNotExist:
            codes = []

        return Response({'codes': codes, 'remaining': len(codes)})

    def post(self, request):
        codes = generate_recovery_codes(request.user)
        logger.info("recovery_codes_regenerated | email=%s", request.user.email)
        return Response({
            'codes':     codes,
            'remaining': len(codes),
            'message':   _('New recovery codes generated. Old codes are now invalid.'),
        })

class RecoveryCodeLoginView(APIView):
    """
    POST /auth/mfa/recovery-codes/verify/
    Body: { "code": "XXXXX-XXXXX", "ephemeral_token": "xxx" }
    Each recovery code is single-use.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        code            = request.data.get('code', '').strip().upper()
        ephemeral_token = request.data.get('ephemeral_token', '').strip()

        if not code or not ephemeral_token:
            return Response(
                {'message': _('Both code and ephemeral_token are required.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user, remember_me = consume_ephemeral_token(ephemeral_token)
        if not user:
            return Response(
                {'message': _('Invalid or expired MFA session. Please log in again.')},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            rc_auth = Authenticator.objects.get(
                user=user,
                type=Authenticator.Type.RECOVERY_CODES,
            )
            if not rc_auth.wrap().validate_code(code):
                return Response(
                    {'message': _('Invalid or already used recovery code.')},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
        except Authenticator.DoesNotExist:
            return Response(
                {'message': _('No recovery codes found for this account.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info("mfa_recovery_login_success | email=%s", user.email)
        return jwt_response(user, remember_me=remember_me)

class WebAuthnRegisterBeginView(APIView):
    """
    POST /auth/mfa/webauthn/register/begin/
    Body: { "name": "My YubiKey" }
    Returns PublicKeyCredentialCreationOptions for navigator.credentials.create().
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        name = request.data.get('name', '').strip()
        if not name:
            return Response(
                {'message': _('A name for the security key is required.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from allauth.mfa.webauthn import internal as wn
            creation_options, state = wn.begin_registration(request, request.user, name)
            request.session['webauthn_register_state'] = state
            return Response({'creation_options': creation_options})
        except Exception as e:
            logger.error("webauthn_reg_begin_error | error=%s", e)
            return Response(
                {'message': _('WebAuthn registration could not be started.')},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

class WebAuthnRegisterCompleteView(APIView):
    """
    POST /auth/mfa/webauthn/register/complete/
    Body: { "name": "My YubiKey", "credential": { ...browser response... } }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        name       = request.data.get('name', '').strip()
        credential = request.data.get('credential')
        state      = request.session.pop('webauthn_register_state', None)

        if not name or not credential:
            return Response(
                {'message': _('name and credential are required.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not state:
            return Response(
                {'message': _('WebAuthn registration session expired. Please try again.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from allauth.mfa.webauthn import internal as wn
            wn.complete_registration(request, request.user, name, credential, state)
            return Response(
                {'message': _(f'Security key "{name}" has been registered.')},
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            logger.error("webauthn_reg_complete_error | error=%s", e)
            return Response(
                {'message': _('Security key registration failed. Please try again.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

class WebAuthnKeyListView(APIView):
    """
    GET    /auth/mfa/webauthn/keys/       → list keys
    DELETE /auth/mfa/webauthn/keys/{pk}/  → remove a key
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk=None):
        keys = Authenticator.objects.filter(
            user=request.user,
            type=Authenticator.Type.WEBAUTHN,
        )
        data = [
            {
                'id':         k.pk,
                'name':       k.data.get('name', 'Security Key'),
                'created_at': k.created_at,
                'last_used':  k.data.get('last_used'),
            }
            for k in keys
        ]
        return Response(data)

    def delete(self, request, pk=None):
        if not pk:
            return Response(
                {'message': _('Key ID is required.')},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            key  = Authenticator.objects.get(
                pk=pk, user=request.user, type=Authenticator.Type.WEBAUTHN
            )
            name = key.data.get('name', 'Security Key')
            key.delete()
            return Response({'message': _(f'Security key "{name}" has been removed.')})
        except Authenticator.DoesNotExist:
            return Response(
                {'message': _('Security key not found.')},
                status=status.HTTP_404_NOT_FOUND,
            )

class WebAuthnAuthBeginView(APIView):
    """
    POST /auth/mfa/webauthn/authenticate/begin/
    Body: { "ephemeral_token": "xxx" }
    Uses resolve (not consume) — token must survive until complete/.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        ephemeral_token = request.data.get('ephemeral_token', '').strip()
        if not ephemeral_token:
            return Response(
                {'message': _('ephemeral_token is required.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user, _ = resolve_ephemeral_token(ephemeral_token)
        if not user:
            return Response(
                {'message': _('Invalid or expired MFA session.')},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            from allauth.mfa.webauthn import internal as wn
            request_options, state = wn.begin_authentication(request, user)
            request.session['webauthn_auth_state'] = state
            request.session['webauthn_auth_token'] = ephemeral_token
            return Response({'request_options': request_options})
        except Exception as e:
            logger.error("webauthn_auth_begin_error | error=%s", e)
            return Response(
                {'message': _('WebAuthn authentication could not be started.')},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

class WebAuthnAuthCompleteView(APIView):
    """
    POST /auth/mfa/webauthn/authenticate/complete/
    Body: { "ephemeral_token": "xxx", "credential": { ...browser response... } }
    Consumes token, verifies key signature, issues full JWT.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        ephemeral_token = request.data.get('ephemeral_token', '').strip()
        credential      = request.data.get('credential')
        state           = request.session.pop('webauthn_auth_state', None)

        if not ephemeral_token or not credential:
            return Response(
                {'message': _('ephemeral_token and credential are required.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user, remember_me = consume_ephemeral_token(ephemeral_token)
        if not user or not state:
            return Response(
                {'message': _('Invalid or expired MFA session. Please log in again.')},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            from allauth.mfa.webauthn import internal as wn
            wn.complete_authentication(request, user, credential, state)
            logger.info("mfa_webauthn_login_success | email=%s", user.email)
            return jwt_response(user, remember_me=remember_me)
        except Exception as e:
            logger.error(
                "webauthn_auth_complete_error | email=%s | error=%s", user.email, e
            )
            return Response(
                {'message': _('Security key verification failed.')},
                status=status.HTTP_401_UNAUTHORIZED,
            )


# =============================================================================
# OTP MFA VIEWS
# =============================================================================

class OTPSetupSMSView(APIView):
    """
    POST /account/v1/mfa/otp/setup/sms/

    Two-step — call without code first (sends OTP), then with code to activate.
    Body step 1: { "phone": "+233241234567" }
    Body step 2: { "phone": "+233241234567", "code": "483921" }
    Replaces any existing OTP method (sms or email).
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from apps.accounts.models import OTPAuthenticator, OTPCode
        from django.core.cache import cache as _cache

        phone = request.data.get("phone", "").strip()
        code  = request.data.get("code",  "").strip()
        user  = request.user

        if not phone:
            return Response(
                {"message": _("Phone number is required.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Step 1: no code — send OTP to the given phone for verification ──
        if not code:
            _cache.set(f"otp_setup_phone:{user.pk}", phone, 600)
            try:
                import africastalking
                from django.conf import settings as _s
                from .utils import generate_otp_code, OTP_TTL_MINUTES
                from datetime import timedelta
                from django.utils import timezone as _tz

                africastalking.initialize(
                    getattr(_s, 'AT_USERNAME', 'sandbox'),
                    getattr(_s, 'AT_API_KEY',  ''),
                )
                otp_code   = generate_otp_code()
                expires_at = _tz.now() + timedelta(minutes=OTP_TTL_MINUTES)
                OTPCode.objects.filter(user=user, channel='sms', is_used=False).delete()
                OTPCode.objects.create(user=user, channel='sms', code=otp_code, expires_at=expires_at)

                africastalking.SMS.send(
                    f"Your African Mutual code: {otp_code}\nValid {OTP_TTL_MINUTES} min. Never share.",
                    [phone],
                    sender_id=getattr(_s, 'AT_SENDER_ID', 'AfricanMutual'),
                )
                logger.info("sms_otp_setup_sent | email=%s | phone=***%s", user.email, phone[-4:])

            except Exception as exc:
                logger.error("sms_otp_setup_failed | email=%s | reason=%s", user.email, exc)
                return Response(
                    {"message": _("Failed to send verification code. Check the number and try again.")},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response(
                {"message": _("Verification code sent. Submit phone + code to activate.")},
                status=status.HTTP_200_OK,
            )

        # ── Step 2: verify + activate ─────────────────────────────────────────
        pending_phone = _cache.get(f"otp_setup_phone:{user.pk}") or phone

        if not verify_otp(user, 'sms', code):
            return Response(
                {"message": _("Invalid or expired verification code.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        OTPAuthenticator.objects.filter(user=user).delete()
        OTPAuthenticator.objects.create(user=user, channel='sms', phone=pending_phone)
        _cache.delete(f"otp_setup_phone:{user.pk}")

        user.phone = pending_phone
        user.save(update_fields=['phone'])

        logger.info("sms_otp_activated | email=%s | phone=***%s", user.email, pending_phone[-4:])
        return Response({"message": _("SMS OTP authentication has been enabled.")})


class OTPSetupEmailView(APIView):
    """
    POST /account/v1/mfa/otp/setup/email/

    Two-step — call without code first (sends OTP to user's email), then with code.
    Body step 1: {}
    Body step 2: { "code": "483921" }
    Replaces any existing OTP method.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from apps.accounts.models import OTPAuthenticator

        code = request.data.get("code", "").strip()
        user = request.user

        # ── Step 1 ───────────────────────────────────────────────────────────
        if not code:
            try:
                send_otp(user, 'email')
            except Exception as exc:
                logger.error("email_otp_setup_failed | email=%s | reason=%s", user.email, exc)
                return Response(
                    {"message": _("Failed to send verification code. Please try again.")},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response(
                {"message": _("Verification code sent to your email. Submit the code to activate.")},
                status=status.HTTP_200_OK,
            )

        # ── Step 2 ───────────────────────────────────────────────────────────
        if not verify_otp(user, 'email', code):
            return Response(
                {"message": _("Invalid or expired verification code.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        OTPAuthenticator.objects.filter(user=user).delete()
        OTPAuthenticator.objects.create(user=user, channel='email')

        logger.info("email_otp_activated | email=%s", user.email)
        return Response({"message": _("Email OTP authentication has been enabled.")})


class OTPDeactivateView(APIView):
    """
    POST /account/v1/mfa/otp/deactivate/
    Body: { "code": "483921" }
    Requires a valid current OTP to prevent unauthorised deactivation.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from apps.accounts.models import OTPAuthenticator

        code = request.data.get("code", "").strip()
        user = request.user

        if not code:
            return Response(
                {"message": _("A verification code is required to confirm deactivation.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        otp_auth = get_otp_authenticator(user)
        if not otp_auth:
            return Response(
                {"message": _("OTP authentication is not enabled on your account.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not verify_otp(user, otp_auth.channel, code):
            return Response(
                {"message": _("Invalid or expired verification code.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        otp_auth.delete()
        logger.info("otp_deactivated | email=%s", user.email)
        return Response({"message": _("OTP authentication has been disabled.")})


class OTPLoginView(APIView):
  
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        code            = request.data.get("code",            "").strip()
        ephemeral_token = request.data.get("ephemeral_token", "").strip()

        if not code or not ephemeral_token:
            return Response(
                {"message": _("Both code and ephemeral_token are required.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # resolve (not consume) so token survives wrong-code retries
        user, remember_me = resolve_ephemeral_token(ephemeral_token)
        if not user:
            return Response(
                {"message": _("Invalid or expired MFA session. Please log in again.")},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        otp_auth = get_otp_authenticator(user)
        if not otp_auth:
            return Response(
                {"message": _("OTP is not enabled for this account.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not verify_otp(user, otp_auth.channel, code):
            return Response(
                {"message": _("Invalid or expired verification code.")},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        consume_ephemeral_token(ephemeral_token)
        logger.info(
            "mfa_otp_login_success | email=%s | channel=%s | remember_me=%s",
            user.email, otp_auth.channel, remember_me,
        )
        return jwt_response(user, remember_me=remember_me)


class OTPResendView(APIView):

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        from django.core.cache import cache as _cache

        ephemeral_token = request.data.get("ephemeral_token", "").strip()
        if not ephemeral_token:
            return Response(
                {"message": _("ephemeral_token is required.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user, _ = resolve_ephemeral_token(ephemeral_token)
        if not user:
            return Response(
                {"message": _("Invalid or expired MFA session. Please log in again.")},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        otp_auth = get_otp_authenticator(user)
        if not otp_auth:
            return Response(
                {"message": _("OTP is not enabled for this account.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        rate_key = f"otp_resend_rate:{user.pk}"
        if _cache.get(rate_key):
            return Response(
                {"message": _("Please wait before requesting another code.")},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        try:
            send_otp(user, otp_auth.channel)
            _cache.set(rate_key, True, 60)
        except Exception as exc:
            logger.error("otp_resend_failed | email=%s | reason=%s", user.email, exc)
            return Response(
                {"message": _("Failed to send verification code. Please try again.")},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        channel_label = "phone" if otp_auth.channel == "sms" else "email"
        logger.info("otp_resent | email=%s | channel=%s", user.email, otp_auth.channel)
        return Response(
            {"message": _(f"A new verification code has been sent to your {channel_label}.")},
        )
