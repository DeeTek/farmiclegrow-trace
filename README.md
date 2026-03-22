# 🌿 FarmicleGrow-Trace

> **Farm-to-buyer agricultural traceability platform** — connecting farmers,
> warehouses, field officers, and buyers across Ghana's agricultural supply chain.

FarmicleGrow-Trace provides end-to-end visibility of produce from harvest to
export. Every batch is traceable via QR code, every farmer is KYC-verified,
and every supply chain event is recorded with full audit history.

---

## 📁 Monorepo Structure

```
farmiclegrow-trace/
├── client/          # Next.js 15 frontend (BFF architecture)
├── server/          # Django 5 + DRF backend
├── .gitignore
└── README.md
```

---

## 🧱 System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Next.js 15 (Vercel)                  │
│  App Router  │  BFF /api/*  │  RTK  │  React Query     │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTP (server-to-server)
┌──────────────────────▼──────────────────────────────────┐
│              Django 5 + DRF (Railway/Render)            │
│  REST API  │  Celery  │  JWT + MFA  │  Swagger          │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│           Supabase PostgreSQL  │  Redis                  │
└─────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- Node.js 20+
- PostgreSQL 15+
- Redis (for Celery)

### 1. Clone
```bash
git clone https://github.com/your-username/farmiclegrow-trace.git
cd farmiclegrow-trace
```

### 2. Start Backend
```bash
cd server
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in your values
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
# API available at http://localhost:8000
# Swagger at    http://localhost:8000/swagger/
```

### 3. Start Frontend
```bash
cd client
npm install
cp .env.local.example .env.local   # set DJANGO_API_URL=http://localhost:8000
npm run dev
# App available at http://localhost:3000
```

---

## 🌍 Deployment

| Layer | Service | Notes |
|-------|---------|-------|
| Frontend | Vercel | Auto-deploy from `main` |
| Backend | Railway / Render / Koyeb | Dockerfile or buildpack |
| Database | Supabase | PostgreSQL 15 |
| Cache / Queue | Upstash Redis | Celery broker |
| Media / Files | Cloudinary / S3 | Report + document storage |

---

## 📦 Apps Overview

| App | Responsibility |
|-----|---------------|
| `accounts` | Custom user model, JWT auth, MFA (OTP/TOTP/WebAuthn), social login |
| `analytics` | KPI dashboards, supply chain trends, export maps |
| `buyers` | Buyer profiles, cart, orders, payments, wishlists |
| `core` | Abstract base models, mixins, signals, managers |
| `farmers` | Farmer KYC, farms, products, reviews, field visits |
| `reports` | Async report generation + scheduling via Celery |
| `staff` | Staff applications and profiles |
| `traceability` | Batches, trace records, QR scan, warehouse intake |

---

## 👨‍💻 Author

**Damduu** — Full-Stack Developer & Data Engineer
Tamale, Ghana

---

## 📄 License

MIT
