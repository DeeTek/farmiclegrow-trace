"""
apps/accounts/serializers.py

Scope: buyer-facing authentication only.
  • Buyer registration & login
  • JWT / token shape
  • Password reset + email verification
  • Social login (Google / Facebook / Apple)

Staff and farmer serializers live in their own apps:
  apps/staff/serializers.py
  apps/farmers/serializers.py
"""

import logging

from django.contrib.auth import get_user_model
from django.utils.http import urlsafe_base64_decode, int_to_base36
from django.utils.encoding import force_str
from django.utils.translation import gettext_lazy as _

from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from dj_rest_auth.registration.serializers import (
    RegisterSerializer,
    ResendEmailVerificationSerializer,
    SocialLoginSerializer,
)
from dj_rest_auth.serializers import (
    LoginSerializer,
    JWTSerializer,
    PasswordResetConfirmSerializer,
)

from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from allauth.socialaccount.models import SocialAccount

User   = get_user_model()
logger = logging.getLogger(__name__)


# =============================================================================
# BUYER USER REPRESENTATION
# =============================================================================

class UserSerializerMixin:
    """Shared helper methods for social provider + MFA + avatar lookup."""

    def get_social_providers(self, obj) -> list:
        return list(
            SocialAccount.objects.filter(user=obj).values_list("provider", flat=True)
        )

    def get_mfa_enabled(self, obj) -> bool:
        return Authenticator.objects.filter(user=obj).exists()

    def get_avatar(self, obj) -> str | None:
        for provider in ("google", "facebook", "apple"):
            try:
                sa    = SocialAccount.objects.get(user=obj, provider=provider)
                extra = sa.extra_data or {}
                url   = (
                    extra.get("picture")
                    or extra.get("profile_image_url")
                    or (extra.get("image") or {}).get("url")
                )
                if url:
                    return url
            except SocialAccount.DoesNotExist:
                continue
        return None


class UserDetailSerializer(UserSerializerMixin, serializers.ModelSerializer):
    """Read-only profile returned inside JWT responses."""

    social_providers = serializers.SerializerMethodField()
    avatar           = serializers.SerializerMethodField()
    mfa_enabled      = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = [
            "id", "email", "first_name", "last_name",
            "is_active", "date_joined",
            "social_providers", "avatar", "mfa_enabled",
        ]
        read_only_fields = fields


# =============================================================================
# JWT / TOKEN SERIALIZERS
# =============================================================================

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Embeds role, region, district, and MFA flag inside the access token."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["email"]       = user.email
        token["full_name"]   = user.get_full_name()
        token["role"]        = user.role
        token["region"]      = user.region
        token["district"]    = user.district
        token["is_social"]   = SocialAccount.objects.filter(user=user).exists()
        token["mfa_enabled"] = Authenticator.objects.filter(user=user).exists()
        return token


class CustomJWTSerializer(JWTSerializer):
    """
    Returned by all login endpoints after a successful authentication.
    Selects BuyerSerializer or StaffSerializer based on the user's role.
    StaffSerializer is imported lazily to avoid a circular import.
    """

    access  = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)
    user    = serializers.SerializerMethodField()

    def get_user(self, obj):
        from apps.staff.serializers import StaffSerializer  # lazy — avoids circular import

        user     = obj["user"]
        is_buyer = user.role in (None, User.Role.BUYER)
        cls      = BuyerSerializer if is_buyer else StaffSerializer
        return cls(user, context=self.context).data


# =============================================================================
# BUYER REGISTRATION
# =============================================================================

class CustomRegisterSerializer(RegisterSerializer):
    """
    Extends dj-rest-auth RegisterSerializer.
    Removes username field, enforces unique email, assigns BUYER role.
    """

    username = None

    def validate_email(self, email):
        if EmailAddress.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(
                "An account already exists with this email address. "
                "Try signing in instead."
            )
        return email

    def save(self, request):
        user = super().save(request)
        user.role = User.Role.BUYER
        user.save(update_fields=["role"])
        return user

# =============================================================================
# BUYER LOGIN
# =============================================================================

class CustomLoginSerializer(LoginSerializer):

    username    = None
    remember_me = serializers.BooleanField(default=False, required=False)

    def validate(self, attrs):
        email = attrs.get("email", "")

        # 1. Email must exist
        try:
            email_address = EmailAddress.objects.get(email__iexact=email)
        except EmailAddress.DoesNotExist:
            raise serializers.ValidationError("Invalid credentials. Please try again.")

        # 2. Email must be verified
        if not email_address.verified:
            raise serializers.ValidationError(
                "Email is not verified. Please check your inbox."
            )

        # 3. Parent validates password + active flag
        try:
            validated = super().validate(attrs)
        except Exception as e:
            logger.error("Login validation failed | email=%s | reason=%s", email, e)
            raise serializers.ValidationError("Invalid credentials. Please try again.")

        # 4. Signal to LoginView: does this user need MFA?
        user = validated.get("user")
        self.mfa_user = (
            user
            if (user and Authenticator.objects.filter(user=user).exists())
            else None
        )

        return validated


# =============================================================================
# RESEND EMAIL VERIFICATION  (buyers only)
# =============================================================================

class CustomResendEmailVerificationSerializer(ResendEmailVerificationSerializer):
    """Adds upfront validation before dj-rest-auth re-sends the verification email."""

    def validate_email(self, email):
        try:
            email_address = EmailAddress.objects.get(email__iexact=email)
            if email_address.verified:
                raise serializers.ValidationError(
                    "This email address has already been verified. Please log in."
                )
        except EmailAddress.DoesNotExist:
            raise serializers.ValidationError("No account found with this email.")
        return email


# =============================================================================
# PASSWORD RESET CONFIRM
# =============================================================================

class CustomPasswordResetConfirmSerializer(PasswordResetConfirmSerializer):
    """
    Bridges uid encoding between our adapter (urlsafe_base64) and
    allauth's internal expectation (base36).
    """

    def validate(self, attrs):
        try:
            pk           = force_str(urlsafe_base64_decode(attrs["uid"]))
            attrs["uid"] = int_to_base36(int(pk))
        except (TypeError, ValueError, OverflowError):
            pass
        return super().validate(attrs)


# =============================================================================
# SOCIAL LOGIN  (buyers only)
# =============================================================================

class GoogleLoginSerializer(SocialLoginSerializer):
    access_token = serializers.CharField(required=False, allow_blank=True)
    id_token     = serializers.CharField(required=False, allow_blank=True)
    code         = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not any([attrs.get("access_token"), attrs.get("id_token"), attrs.get("code")]):
            raise serializers.ValidationError(
                _("Provide access_token, id_token, or authorization code.")
            )
        return super().validate(attrs)


class FacebookLoginSerializer(SocialLoginSerializer):
    access_token = serializers.CharField(required=True)
    code         = serializers.CharField(required=False, allow_blank=True)


class AppleLoginSerializer(SocialLoginSerializer):
    access_token = serializers.CharField(required=False, allow_blank=True)
    id_token     = serializers.CharField(required=False, allow_blank=True)
    code         = serializers.CharField(required=False, allow_blank=True)
    first_name   = serializers.CharField(required=False, allow_blank=True)
    last_name    = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not any([attrs.get("access_token"), attrs.get("id_token"), attrs.get("code")]):
            raise serializers.ValidationError(
                _("Provide access_token (authorization_code) or id_token.")
            )
        return super().validate(attrs)


class SocialConnectSerializer(serializers.Serializer):
    """Validates the token payload for connecting a social provider to an existing account."""

    access_token = serializers.CharField(required=False, allow_blank=True)
    id_token     = serializers.CharField(required=False, allow_blank=True)
    code         = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not any([attrs.get("access_token"), attrs.get("id_token"), attrs.get("code")]):
            raise serializers.ValidationError(_("Provide access_token, id_token, or code."))
        return attrs


class SocialAccountSerializer(serializers.ModelSerializer):
    """Read-only shape for a linked social account (used in the profile endpoint)."""

    class Meta:
        model            = SocialAccount
        fields           = ["id", "provider", "uid", "date_joined", "last_login", "extra_data"]
        read_only_fields = fields