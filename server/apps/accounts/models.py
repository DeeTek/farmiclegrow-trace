from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.utils.translation import gettext_lazy as _

# Create your models here.

class UserManager(BaseUserManager):
  use_in_migration = True
  
  def _create_user(self, email, password, **extra_fields):
    
    if not email:
      raise ValueError("Email address is required.")
      
    emaio = self.normalize_email(email)
    user = self.model(email=email, **extra_fields)
    user.set_password(password)
    user.save(using=self._db)
    return
  
  def create_user(self, email, password=None, **extra_fields):
    extra_fields.setdefault('is_staff', False)
    extra_fields.setdefault('is_superuser', False)
    return self._create_user(email, password, **extra_fields)
  
  def create_superuser(self, email, password=None, **extra_fields):
    extra_fields.setdefault('is_staff', True)
    extra_fields.setdefault('is_superuser', True)
    extra_fields.setdefault('role', 'sa')
    
    if not extra_fields['is_staff']:
      raise ValueError('Superuser must have is_staff=True.')
    
    if not extra_fields['is_superuser']:
      raise ValueError('Superuser must have is_superuser=True.')
    
    return self._create_user(email, password, **extra_fields)
    
class User(AbstractUser):
  
  class Role(models.TextChoices):
    SUPER_ADMIN = 'sa', _('Super Admin')
    FIELD_OFFICER = 'fo', _('Field Officer')
    WAREHOUSE_MANAGER = 'wm', _('Warehouse Manager')
    BUYER = 'by', _('Buyer')
    FARMER = 'fm', _('Farmer')

  username = None
  email = models.EmailField(_("email address"), unique=True)
  first_name = models.CharField(_("first name"), max_length=150, blank=True)
  last_name  = models.CharField(_("last name"),  max_length=150, blank=True)
  phone = models.CharField(_("phone number"), max_length=20, null=True, blank=True,
        unique=True, help_text=_("E.164 format e.g. +233241234567. Unique per user. Enables phone-based login."),
  )

  role = models.CharField(max_length=2, choices=Role.choices, null=True, blank=True, db_index=True, help_text=_("Platform role. " "Null or 'by' = sel-registered buyer. " "'fo' = field officer. " "'wm' = warehouse manager. " "'fm' = farmer (registered by field officer). " "'sa' = super admin."),
    )
  region = models.CharField(_("region"), max_length=100, null=True, blank=True, help_text=_("Operational region (e.g. Brong-Ahafo, Upper West)."),
    )
  district = models.CharField(_("district"), max_length=100, null=True, blank=True, help_text=_("Operational district within the region."),
  )

  USERNAME_FIELD  = "email"
  REQUIRED_FIELDS = []

  objects = UserManager()

  class Meta:
    verbose_name = _("user")
    verbose_name_plural = _("users")
    ordering = ["-date_joined"]

  def __str__(self):
    return self.email

  @property
  def full_name(self):
    return f"{self.first_name} {self.last_name}".strip() or self.email

  @property
  def is_staff_role(self):
    return self.role in (self.Role.SUPER_ADMIN, self.Role.FIELD_OFFICER, self.Role.WAREHOUSE_MANAGER,)

  @property
  def is_field_agent(self):
    return self.role in (self.Role.SUPER_ADMIN, self.Role.FIELD_OFFICER)

class EmailVerificationAttempt(models.Model):
  email = models.EmailField(unique=True)
  resend_count = models.PositiveIntegerField(default=0)
  resend_at = models.DateTimeField(auto_now=True)

  def __str__(self):
    return f"{self.email} — {self.resend_count} attempts"

class BlacklistedEmailKey(models.Model):
  
  key = models.CharField(max_length=100, unique=True)
  email = models.EmailField()
  blacklisted_at = models.DateTimeField(auto_now_add=True)

  def __str__(self):
    return f"{self.email} — {self.key}"

class PasswordResetAttempt(models.Model):
  
  email = models.EmailField(unique=True)
  reset_count = models.PositiveIntegerField(default=0)
  reset_at = models.DateTimeField(auto_now=True)

  def __str__(self):
    return f"{self.email} — {self.reset_count} attempts"

class BlacklistedPasswordResetToken(models.Model):
  
  token = models.CharField(max_length=100, unique=True)
  email = models.EmailField()
  blacklisted_at = models.DateTimeField(auto_now_add=True)

  def __str__(self):
    return f"{self.email} — {self.token}"

class AccountLockout(models.Model):
  
  email = models.EmailField(unique=True)
  locked_at = models.DateTimeField(auto_now_add=True)
  reason = models.CharField(max_length=255, default='Too many failed login attempts')
  locked_by = models.CharField(max_length=50,  default='system')
  unlocked_at = models.DateTimeField(null=True, blank=True)
  is_locked = models.BooleanField(default=True)

  class Meta:
    verbose_name = 'Account Lockout'

  def __str__(self):
    return f"{self.email} — {'locked' if self.is_locked else 'unlocked'}"

class OTPAuthenticator(models.Model):

  class Channel(models.TextChoices):
    SMS = 'sms',  _('SMS')
    EMAIL = 'email', _('Email')

  user = models.OneToOneField(
        'User', on_delete=models.CASCADE, related_name='otp_authenticator',
  )
  channel = models.CharField(max_length=5, choices=Channel.choices)
  phone = models.CharField(max_length=20, null=True, blank=True, help_text=_("E.164 format e.g. +233241234567. Required when channel=sms."),
    )
  is_active  = models.BooleanField(default=True)
  created_at = models.DateTimeField(auto_now_add=True)
  updated_at = models.DateTimeField(auto_now=True)

  class Meta:
    verbose_name = 'OTP Authenticator'

  def __str__(self):
    return f"{self.user.email} — {self.channel}"

class OTPCode(models.Model):
  user = models.ForeignKey('User',
        on_delete=models.CASCADE,
        related_name='otp_codes',)
  channel    = models.CharField(max_length=5)
  code = models.CharField(max_length=6)
  expires_at = models.DateTimeField()
  is_used = models.BooleanField(default=False)
  created_at = models.DateTimeField(auto_now_add=True)

  class Meta:
    verbose_name = 'OTP Code'
    indexes = [models.Index(fields=['user', 'channel']),]

  def __str__(self):
    return f"{self.user.email} — {self.channel} — {'used' if self.is_used else 'active'}"

  @property
  def is_expired(self):
    from django.utils import timezone
    return timezone.now() > self.expires_at

class AdminImpersonationLog(models.Model):
   
  admin = models.ForeignKey('User',on_delete=models.PROTECT, related_name='impersonation_actions',)
  impersonated = models.ForeignKey('User',
        on_delete=models.CASCADE,
        related_name='impersonation_events',
    )
  ip_address   = models.GenericIPAddressField(null=True, blank=True)
  user_agent   = models.TextField(blank=True)
  token_key    = models.CharField(
        max_length=16,
        help_text=_("First 16 chars of impersonation token — for log correlation only."),
    )
  created_at   = models.DateTimeField(auto_now_add=True, db_index=True)

  class Meta:
    verbose_name = 'Admin Impersonation Log'
    ordering     = ['-created_at']

  def __str__(self):
    return (f"Admin {self.admin.email} → Farmer {self.impersonated.email} "
            f"@ {self.created_at:%Y-%m-%d %H:%M}")






