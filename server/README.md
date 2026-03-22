# ⚙️ FarmicleGrow-Trace — Server

Django 5 + Django REST Framework backend.
Provides a versioned REST API consumed exclusively by the Next.js BFF layer.

---

## Tech Stack

| Tool | Purpose |
|------|---------|
| **Django 5** | Web framework |
| **Django REST Framework** | REST API |
| **PostgreSQL 15** | Primary database |
| **Celery + Redis** | Async tasks (report generation, webhooks) |
| **django-allauth** | Auth + social login (Google, Facebook, Apple) |
| **djangorestframework-simplejwt** | JWT access/refresh tokens |
| **dj-rest-auth** | Auth REST endpoints |
| **drf-yasg** | Swagger / OpenAPI docs |
| **django-celery-beat** | Periodic task scheduling |
| **django-filter** | Queryset filtering |

---

## Project Structure

```
server/
├── apps/
│   ├── accounts/           # Custom user model, JWT, MFA, social auth
│   │   ├── models.py
│   │   ├── serializers.py
│   │   ├── views.py
│   │   ├── adapters.py     # Custom allauth adapter
│   │   └── urls.py
│   ├── analytics/          # KPI dashboards, trends, export maps
│   ├── buyers/             # Buyer profiles, cart, orders, payments, wishlists
│   │   ├── models.py
│   │   ├── serializers.py
│   │   ├── views.py
│   │   ├── services.py     # All business logic lives here
│   │   ├── tasks.py        # Celery tasks (webhooks, notifications)
│   │   ├── permissions.py
│   │   └── signals.py
│   ├── core/               # Shared infrastructure
│   │   └── models/
│   │       ├── abstract.py     # TimeStampedModel, SoftDeleteModel…
│   │       ├── base.py         # BaseModel with UUID pk
│   │       ├── mixins.py       # RoleQuerySetMixin, SoftDeleteMixin…
│   │       ├── managers.py     # CodedManager, SoftDeleteManager…
│   │       └── querysets.py    # TraceabilityQuerySet, BatchQuerySet…
│   ├── farmers/            # Farmer KYC, farms, products, reviews, visits
│   ├── reports/            # Async report generation + scheduling
│   ├── staff/              # Staff applications + profiles
│   └── traceability/       # Batches, trace records, QR scan, warehouse intake
│       ├── models.py
│       ├── serializers.py
│       ├── views.py
│       ├── querysets.py    # build_chain() for QR scan response
│       └── signals.py
├── config/
│   ├── settings/
│   │   ├── base.py
│   │   ├── development.py
│   │   └── production.py
│   ├── urls.py
│   ├── celery.py
│   └── wsgi.py
├── manage.py
├── requirements.txt
└── .env.example
```

---

## Core Architecture Patterns

### 1. Abstract Base Models (`apps/core/models/`)
Every model inherits from `TimeStampedModel` or `SoftDeleteModel`:
```python
class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        abstract = True
```

### 2. Soft Deletes
```python
# Never hard-delete — always soft delete
def perform_destroy(self, instance):
    instance.perform_destroy(deleted_by=self.request.user)
```

### 3. Status Transitions
```python
# Always go through transition_status() — never set .status directly
record.transition_status("exported", changed_by=request.user)
```

### 4. Atomic Mutations
```python
# All writes are wrapped in @transaction.atomic
@transaction.atomic
def perform_create(self, serializer):
    ...
```

### 5. One-Directional Imports
```
core → (no imports from other apps)
accounts → core
farmers → core, accounts
buyers → core, accounts
traceability → core, accounts, farmers
analytics → core, accounts, farmers, buyers, traceability
reports → core, accounts
staff → core, accounts
```

### 6. Service Layer
Views contain zero business logic. All mutations delegate to `services.py`:
```python
# views.py
def perform_create(self, serializer):
    buyer = services.create_buyer(serializer.validated_data, user=request.user)

# services.py
@transaction.atomic
def create_buyer(data, user):
    buyer = Buyer.objects.create(**data, user=user)
    send_event("buyer.registered", buyer)
    return buyer
```

---

## Environment Variables

Create `.env` in this directory:

```env
SECRET_KEY=your-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Database
DATABASE_URL=postgresql://user:password@localhost:5432/farmiclegrow_db

# Redis
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# Email
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=your@email.com
EMAIL_HOST_PASSWORD=your-app-password

# JWT
ACCESS_TOKEN_LIFETIME_MINUTES=60
REFRESH_TOKEN_LIFETIME_DAYS=7

# Payments
PAYSTACK_SECRET_KEY=sk_test_xxx
FLUTTERWAVE_SECRET_KEY=FLWSECK_TEST-xxx

# Social Auth
GOOGLE_CLIENT_ID=xxx
GOOGLE_CLIENT_SECRET=xxx

# Storage (production)
CLOUDINARY_URL=cloudinary://xxx
```

---

## Setup (Development)

```bash
# 1. Create and activate virtualenv
python -m venv venv
source venv/bin/activate          # Linux/Mac/Termux
# venv\Scripts\activate           # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Environment
cp .env.example .env
# Edit .env with your values

# 4. Database
python manage.py migrate

# 5. Create superuser
python manage.py createsuperuser

# 6. Run server
python manage.py runserver

# 7. Run Celery (separate terminal)
celery -A config worker -l info
celery -A config beat -l info     # for scheduled tasks
```

---

## API Documentation

| URL | Description |
|-----|-------------|
| `/swagger/` | Swagger UI |
| `/redoc/` | ReDoc UI |
| `/swagger/?format=openapi` | Raw OpenAPI JSON |

---

## Running Tests

```bash
python manage.py test apps
# or with coverage
coverage run manage.py test apps && coverage report
```

---

## Deployment (Railway / Render)

**Build command:**
```bash
pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate
```

**Start command:**
```bash
gunicorn config.wsgi:application --bind 0.0.0.0:$PORT
```

**Environment variables** — set all `.env` keys in the platform dashboard.

---

