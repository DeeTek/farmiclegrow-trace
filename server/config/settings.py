import os
import pytz
from pathlib import Path
from decouple import config
from datetime import timezone
from datetime import timedelta


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config("SECRET_KEY")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config("DEBUG", cast=bool, default=False)

ALLOWED_HOSTS = str(config("ALLOWED_HOSTS", default="localhost,127.0.0.1")).split(',')


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    
    'dj_rest_auth',
    'dj_rest_auth.registration',
    
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    
    'allauth.mfa',
    'allauth.mfa.totp',
    'allauth.mfa.recovery_codes',
    
    'drf_yasg',
    
    'corsheaders',
    'django_filters',
    
    'django_celery_beat',
    'django_celery_results',
    
    'apps.core',
    'apps.accounts',
    'apps.farmers',
    'apps.buyers',
    'apps.staff',
    'apps.analytics',
    'apps.traceability',
    'apps.reports',
]

SITE_ID = 1

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / "templates"],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases
"""
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}"""

DATABASES = {
  'default': {
    'ENGINE': 'django.db.backends.postgresql',
    'NAME': config('DB_NAME'),
    'USER':  config('DB_USER'),
    'PASSWORD': config('DB_PASSWORD'),
    'HOST': config('DB_HOST'),
    'PORT': config('DB_PORT'),
  }
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

DEFAULT_TIMEZONE = pytz.timezone(TIME_ZONE)

USE_TZ = True

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'

"""SWAGGER_SETTINGS = {
  "SECURITY_DEFINITIONS": {
    "Bearer": {
      "type": "apiKey",
      "name": "Authorization",
      "in":   "header",
      "description": "Enter: Bearer <token>  e.g. Bearer eyJhbGci...",
        }
    },
    "USE_SESSION_AUTH":   False,
    "PERSIST_AUTH":   True, 
    "REFETCH_SCHEMA_WITH_AUTH": True,
    "REFETCH_SCHEMA_ON_LOGOUT": True,
    "JSON_EDITOR": True,
}
"""
REST_FRAMEWORK = {
  "DEFAULT_AUTHENTICATION_CLASSES": [
    'apps.accounts.authentication.HybridJWTAuthentication'
  ],
  'DEFAULT_FILTER_BACKENDS': [
    'django_filters.rest_framework.DjangoFilterBackend',
    'rest_framework.filters.SearchFilter',
    'rest_framework.filters.OrderingFilter',
        
  ],
  "DEFAULT_THROTTLE_CLASSES": [
    'rest_framework.throttling.AnonRateThrottle',
    'rest_framework.throttling.UserRateThrottle',
  ],
  "DEFAULT_THROTTLE_RATES": {
    'anon': '100/day',
    'user': '1000/day',
    'resend_email': '3/hour',
    'password_reset': '3/hour',
    'search': '60/min',
    'search_autocomplete': '120/min',
  },
}

REST_AUTH = {
  "USE_JWT": True,
  "TOKEN_MODEL": None,
  "JWT_AUTH_COOKIE": "fg-access",
  "JWT_AUTH_REFRESH_COOKIE": "fg-refresh",
  "JWT_AUTH_HTTPONLY": True,
  "JWT_AUTH_SECURE": False,
  "JWT_AUTH_SAMESITE": "Lax",
  "JWT_AUTH_RETURN_EXPIRATION": True,
  
  "REGISTER_SERIALIZER": "apps.accounts.serializers.RegisterSerializer",
  "RESEND_EMAIL_SERIALIZER": 'apps.accounts.serializers.CustomResendEmailVerificationSerializer',
  "PASSWORD_RESET_CONFIRM_SERIALIZER": "apps.accounts.serializers.PasswordResetConfirmSerializer",
  #"USER_DETAILS_SERIALIZER": "apps.accounts.serializers.UserDetailSerializer",
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME':  timedelta(minutes=30),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS':  True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,

    'ALGORITHM': 'HS256',
    'SIGNING_KEY': config("SECRET_KEY"),

    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',

    'TOKEN_OBTAIN_SERIALIZER': 'apps.accounts.serializers.CustomTokenObtainPairSerializer',
}

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# =============================================================================
# CELERY
# =============================================================================

CELERY_BROKER_URL        = config('REDIS_URL', default='redis://127.0.0.1:6379/0')
CELERY_RESULT_BACKEND    = config('REDIS_URL', default='redis://127.0.0.1:6379/0')
CELERY_ACCEPT_CONTENT    = ['json']
CELERY_TASK_SERIALIZER   = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE          = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT   = 30 * 60   # 30 minutes hard limit
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60  # 25 minutes soft limit

# Beat scheduler (periodic tasks)
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

if DEBUG:
  # Development
  CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
  }
else:
  # Production — replace with Redis
  CACHES = {
    'default': {
      'BACKEND': 'django.core.cache.backends.redis.RedisCache',
      'LOCATION': config('REDIS_URL', default='redis://127.0.0.1:6379/1'),
    } 
  }

ACCOUNT_LOGIN_METHODS = {"email"}

ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_USER_MODEL_USERNAME_FIELD = None

ACCOUNT_EMAIL_CONFIRMATION_HMAC = False

ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS = 1

ACCOUNT_ADAPTER = "apps.accounts.adapter.CustomAccountAdapter"

SOCIALACCOUNT_EMAIL_REQUIRED       = True
SOCIALACCOUNT_EMAIL_VERIFICATION   = 'none'   # already verified by provider
SOCIALACCOUNT_AUTO_SIGNUP          = True
SOCIALACCOUNT_STORE_TOKENS         = True

IMPERSONATION_TOKEN_LIFETIME = timedelta(minutes=30)

SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'APP': {
            'client_id': config('GOOGLE_CLIENT_ID',     default=''),
            'secret': config('GOOGLE_CLIENT_SECRET', default=''),
            'key':           '',
        },
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
        'FETCH_USERINFO': True,
        'VERIFIED_EMAIL': True,
    },
    'facebook': {
        'APP': {
            'client_id': config('FACEBOOK_APP_ID',     default=''),
            'secret':    config('FACEBOOK_APP_SECRET', default=''),
            'key':       '',
        },
        'METHOD':        'oauth2',
        'SCOPE':         ['email', 'public_profile'],
        'FIELDS': [
            'id', 'email', 'name',
            'first_name', 'last_name', 'picture',
        ],
        'VERIFIED_EMAIL': False,
    },
    'apple': {
        'APP': {
            'client_id': config('APPLE_CLIENT_ID',      default=''),
            'secret':    config('APPLE_PRIVATE_KEY',    default=''),
            'key':       config('APPLE_KEY_ID',         default=''),
            'settings': {
                'certificate_key': config('APPLE_PRIVATE_KEY', default=''),
            },
        },
        'SCOPE': ['email', 'name'],
        'VERIFIED_EMAIL': True,
    },
}

GOOGLE_OAUTH_CALLBACK_URL=config("GOOGLE_OAUTH_CALLBACK_URL")
FACEBOOK_OAUTH_CALLBACK_URL=config("FACEBOOK_OAUTH_CALLBACK_URL")
APPLE_OAUTH_CALLBACK_URL=config("APPLE_OAUTH_CALLBACK_URL")

AT_USERNAME  = config('AT_USERNAME',  default='sandbox')
AT_API_KEY = config('AT_API_KEY',   default='')
AT_SENDER_ID = config('AT_SENDER_ID', default='FarmiclegrowAgric')

MFA_SUPPORTED_TYPES = ['totp', 'recovery_codes']

MFA_TOTP_PERIOD  = 30 # seconds per code window
MFA_TOTP_DIGITS  = 6 # code length
MFA_TOTP_ISSUER  = 'African Mutual' # shown in authenticator app

MFA_RECOVERY_CODE_COUNT  = 10       # how many codes are generated
MFA_RECOVERY_CODE_LENGTH = 10       # characters per code


CORS_ALLOWED_ORIGINS = [
  'http://localhost:3000',
  'http://localhost:8000',
]

CORS_ALLOW_CREDENTIALS = True


LOGGING = {
  'version': 1,
  'disable_existing_loggers': False,
  'formatters': {
    'verbose': {
      'format': "[{asctime}] {levelname:<8} {name} | {message}",
      'style': "{",
      'datefmt': "%Y-%m-%d %H:%M:%S"
    },
    'simple': {
      'format': "{levelname} {message}",
      'style': "{",
    }
  },
  'handlers': {
    'console':{
      'class': "logging.StreamHandler",
      'formatter': "verbose"
    },
    'account_file':{
      'class': "logging.handlers.RotatingFileHandler",
      'filename': str(LOGS_DIR / "account.log"),
      'maxBytes': 1024 * 1024 * 5,
      'backupCount': 5,
      'formatter': "verbose"
    },
    'error_file':{
      'class': "logging.handlers.RotatingFileHandler",
      'filename': str(LOGS_DIR / "errors.log"),
      'maxBytes': 1024 * 1024 * 5,
      'backupCount': 5,
      'formatter': "verbose",
      'level': "ERROR",
    },
  },
  'loggers': {
    'accounts': {
      'handlers': ["console", "account_file", "error_file"],
      'level': "DEBUG",
      "propagate": False
    },
    'django': {
      'handlers': ["console", "error_file"],
      'level': "INFO",
      "propagate": False
    },
    'django.request': {
      'handlers': ["console", "error_file"],
      'level': "WARNING",
      "propagate": False
    },
    'django.security': {
      'handlers': ["console", "error_file"],
      'level': "WARNING",
      "propagate": False
    },
  },
  'root': {
    'handlers': ["console", "error_file"],
    'level': "WARNING",
    
  },
}

if DEBUG:
  EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
  EMAIL_BACKEND = config('EMAIL_BACKEND')
  EMAIL_HOST = config('EMAIL_HOST')
  EMAIL_HOST_USER = config('EMAIL_USER')
  EMAIL_HOST_PASSWORD = config('EMAIL_PASSWORD')
  EMAIL_PORT = config('EMAIL_PORT')
  DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER)
  