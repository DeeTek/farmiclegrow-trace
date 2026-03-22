from django.urls import path, include
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView
from dj_rest_auth.views import PasswordChangeView

from apps.accounts.views import (

    # ── Registration ──────────────────────────────────────────
    CustomRegisterView,
    CustomVerifyEmailView,
    CustomResendEmailVerificationView,

    # ── Auth ──────────────────────────────────────────────────
    LoginView,
    LogoutView,

    # ── Password ──────────────────────────────────────────────
    PasswordResetView,
    PasswordResetConfirmView,

    # ── Social ────────────────────────────────────────────────
    GoogleLoginView,
    GoogleConnectView,
    FacebookLoginView,
    FacebookConnectView,
    AppleLoginView,
    AppleConnectView,
    LinkedAccountsView,
    DisconnectView,

    # ── MFA ───────────────────────────────────────────────────
    MFAStatusView,
    OTPSetupSMSView,
    OTPSetupEmailView,
    OTPDeactivateView,
    OTPLoginView,
    OTPResendView,
    TOTPSetupView,
    TOTPVerifyView,
    TOTPDeactivateView,
    TOTPLoginView,
    RecoveryCodesView,
    RecoveryCodeLoginView,
    WebAuthnRegisterBeginView,
    WebAuthnRegisterCompleteView,
    WebAuthnKeyListView,
    WebAuthnAuthBeginView,
    WebAuthnAuthCompleteView,
)


# =============================================================================
# v1 URL groups
# =============================================================================

# ── Registration ──────────────────────────────────────────────────────────────
registration_urlpatterns = [
    path('register/',              CustomRegisterView.as_view(),                name='register'),
    path('register/verify-email/', CustomVerifyEmailView.as_view(),             name='verify_email'),
    path('register/resend-email/', CustomResendEmailVerificationView.as_view(), name='resend_email'),
]

# ── Auth ──────────────────────────────────────────────────────────────────────
auth_urlpatterns = [
    path('login/',         LoginView.as_view(),       name='login'),
    path('logout/',        LogoutView.as_view(),       name='logout'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('token/verify/',  TokenVerifyView.as_view(),  name='token_verify'),
]

# ── Password ──────────────────────────────────────────────────────────────────
password_urlpatterns = [
    path('password/change/',        PasswordChangeView.as_view(),       name='password_change'),
    path('password/reset/',         PasswordResetView.as_view(),        name='password_reset'),
    path('password/reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
]

# ── Social ────────────────────────────────────────────────────────────────────
social_urlpatterns = [
    path('social/google/',            GoogleLoginView.as_view(),     name='google_login'),
    path('social/google/connect/',    GoogleConnectView.as_view(),   name='google_connect'),
    path('social/facebook/',          FacebookLoginView.as_view(),   name='facebook_login'),
    path('social/facebook/connect/',  FacebookConnectView.as_view(), name='facebook_connect'),
    path('social/apple/',             AppleLoginView.as_view(),      name='apple_login'),
    path('social/apple/connect/',     AppleConnectView.as_view(),    name='apple_connect'),
    path('social/accounts/',          LinkedAccountsView.as_view(),  name='social_accounts_list'),
    path('social/accounts/<int:pk>/', DisconnectView.as_view(),      name='social_account_disconnect'),
]

# ── MFA ───────────────────────────────────────────────────────────────────────
mfa_urlpatterns = [
    # Status
    path('mfa/status/',                          MFAStatusView.as_view(),                name='mfa_status'),

    # TOTP
    path('mfa/totp/setup/',                      TOTPSetupView.as_view(),                name='mfa_totp_setup'),
    path('mfa/totp/verify/',                     TOTPVerifyView.as_view(),               name='mfa_totp_verify'),
    path('mfa/totp/deactivate/',                 TOTPDeactivateView.as_view(),           name='mfa_totp_deactivate'),
    path('mfa/totp/login/',                      TOTPLoginView.as_view(),                name='mfa_totp_login'),

    # Recovery codes
    path('mfa/recovery-codes/',                  RecoveryCodesView.as_view(),            name='mfa_recovery_codes'),
    path('mfa/recovery-codes/verify/',           RecoveryCodeLoginView.as_view(),        name='mfa_recovery_verify'),

    # WebAuthn
    path('mfa/webauthn/register/begin/',         WebAuthnRegisterBeginView.as_view(),    name='mfa_webauthn_reg_begin'),
    path('mfa/webauthn/register/complete/',      WebAuthnRegisterCompleteView.as_view(), name='mfa_webauthn_reg_complete'),
    path('mfa/webauthn/keys/',                   WebAuthnKeyListView.as_view(),          name='mfa_webauthn_keys'),
    path('mfa/webauthn/keys/<int:pk>/',          WebAuthnKeyListView.as_view(),          name='mfa_webauthn_key_delete'),
    path('mfa/webauthn/authenticate/begin/',     WebAuthnAuthBeginView.as_view(),        name='mfa_webauthn_auth_begin'),
    path('mfa/webauthn/authenticate/complete/',  WebAuthnAuthCompleteView.as_view(),     name='mfa_webauthn_auth_complete'),

    # OTP (SMS / Email)
    path('mfa/otp/setup/sms/',   OTPSetupSMSView.as_view(),   name='mfa_otp_setup_sms'),
    path('mfa/otp/setup/email/', OTPSetupEmailView.as_view(),  name='mfa_otp_setup_email'),
    path('mfa/otp/deactivate/',  OTPDeactivateView.as_view(),  name='mfa_otp_deactivate'),
    path('mfa/otp/login/',       OTPLoginView.as_view(),       name='mfa_otp_login'),
    path('mfa/otp/resend/',      OTPResendView.as_view(),      name='mfa_otp_resend'),
]

# =============================================================================
# Wire everything under the v1/ prefix
# Allauth's own internal URLs (email confirmation redirects etc.) stay at
# account/ root so allauth's internals keep working correctly.
# =============================================================================

v1_urlpatterns = (
    registration_urlpatterns
    + auth_urlpatterns
    + password_urlpatterns
    + social_urlpatterns
    + mfa_urlpatterns
)

urlpatterns = [
    path('', include('allauth.urls')), # allauth internals (no v1 prefix — required by allauth)
    path('v1/', include((v1_urlpatterns, 'v1'))),  # all our API endpoints
]
