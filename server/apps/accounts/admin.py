from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


from .models import (
    User,
    EmailVerificationAttempt,
    BlacklistedEmailKey,
    PasswordResetAttempt,
    BlacklistedPasswordResetToken,
    AccountLockout,
    OTPAuthenticator,
    OTPCode,
)

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ('-date_joined',)
    list_display = (
        'email', 'first_name', 'last_name', 'role', 'region', 'district',
        'is_active', 'is_staff'
    )
    search_fields = ('email', 'first_name', 'last_name', 'phone')
    list_filter = ('role', 'region', 'district', 'is_active', 'is_staff')

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        (_('Personal info'), {'fields': ('first_name', 'last_name', 'phone')}),
        (_('Role & Branch'), {'fields': ('role', 'region', 'district', 'branch')}),
        (_('Permissions'), {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2', 'role', 'region', 'district', 'branch'),
        }),
    )

    readonly_fields = ('last_login', 'date_joined')

@admin.register(EmailVerificationAttempt)
class EmailVerificationAttemptAdmin(admin.ModelAdmin):
    list_display  = ['email', 'resend_count', 'resend_at']
    list_filter   = ['email']
    search_fields = ['email']


@admin.register(BlacklistedEmailKey)
class BlacklistedEmailKeyAdmin(admin.ModelAdmin):
    list_display  = ['key', 'email', 'blacklisted_at']
    list_filter   = ['email']
    search_fields = ['key', 'email']


@admin.register(PasswordResetAttempt)
class PasswordResetAttemptAdmin(admin.ModelAdmin):
    list_display  = ['email', 'reset_count', 'reset_at']
    list_filter   = ['email']
    search_fields = ['email']


@admin.register(BlacklistedPasswordResetToken)
class BlacklistedPasswordResetTokenAdmin(admin.ModelAdmin):
    list_display  = ['token', 'email', 'blacklisted_at']
    list_filter   = ['email']
    search_fields = ['token', 'email']


@admin.register(AccountLockout)
class AccountLockoutAdmin(admin.ModelAdmin):
    list_display    = ['email', 'is_locked', 'locked_at', 'unlocked_at', 'locked_by', 'reason']
    list_filter     = ['is_locked']
    search_fields   = ['email']
    readonly_fields = ['locked_at', 'locked_by']
    actions         = ['unlock_selected_accounts']

    @admin.action(description='Unlock selected accounts')
    def unlock_selected_accounts(self, request, queryset):
        count = queryset.filter(is_locked=True).update(
            is_locked=False,
            unlocked_at=timezone.now(),
            locked_by=request.user.email,
        )
        self.message_user(request, f"{count} account(s) unlocked.")


@admin.register(OTPAuthenticator)
class OTPAuthenticatorAdmin(admin.ModelAdmin):
    list_display    = ['user', 'channel', 'phone', 'is_active', 'created_at', 'updated_at']
    list_filter     = ['channel', 'is_active']
    search_fields   = ['user__email', 'phone']
    readonly_fields = ['created_at', 'updated_at']
    actions         = ['deactivate_selected']

    @admin.action(description='Deactivate selected OTP methods')
    def deactivate_selected(self, request, queryset):
        count = queryset.filter(is_active=True).update(is_active=False)
        self.message_user(request, f"{count} OTP authenticator(s) deactivated.")


@admin.register(OTPCode)
class OTPCodeAdmin(admin.ModelAdmin):
    list_display    = ['user', 'channel', 'is_used', 'is_expired_display', 'expires_at', 'created_at']
    list_filter     = ['channel', 'is_used']
    search_fields   = ['user__email']
    readonly_fields = ['code', 'created_at', 'expires_at']
    actions         = ['invalidate_selected']

    @admin.display(boolean=True, description='Expired')
    def is_expired_display(self, obj):
        return obj.is_expired

    @admin.action(description='Invalidate selected OTP codes')
    def invalidate_selected(self, request, queryset):
        count = queryset.filter(is_used=False).update(is_used=True)
        self.message_user(request, f"{count} OTP code(s) invalidated.")
