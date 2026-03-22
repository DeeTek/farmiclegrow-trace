# 🖥️ FarmicleGrow-Trace — Client

Next.js 15 frontend with a Backend-For-Frontend (BFF) architecture.
All Django API calls are routed through Next.js `/api/*` route handlers —
the browser never talks to Django directly.

---

## Tech Stack

| Tool | Purpose |
|------|---------|
| **Next.js 15** | App Router, Server Components, BFF route handlers |
| **TypeScript** | Full type safety across all layers |
| **React Query** | Server state, caching, background refetch |
| **Redux Toolkit** | Auth state, UI toggles (modals/drawers), active selections |
| **Preact Signals** | Optimistic counters, per-component loading flags, form dirty state |
| **React Hook Form** | Form state management |
| **Valibot** | Schema validation (forms + API payloads) |
| **Tailwind CSS** | Utility-first styling |

---

## Project Structure

```
client/
├── app/
│   ├── api/                    # BFF route handlers (proxy to Django)
│   │   ├── auth/               # /account/v1/* — login, MFA, social
│   │   ├── analytics/          # /api/v1/analytics/*
│   │   ├── buyers/             # /api/v1/buyers/*
│   │   ├── farmers/            # /api/v1/farmers/*
│   │   ├── reports/            # /api/v1/reports/*
│   │   ├── staff/              # /api/v1/staff/*
│   │   ├── traceability/       # /api/v1/traceability/*
│   │   └── core/               # health, search, version
│   └── (routes)/               # UI pages
├── hooks/
│   ├── trace-records/          # useTraceRecords, useUpdateTraceStatus…
│   ├── warehouse-intakes/      # useWarehouseIntakes, useAcceptIntake…
│   └── wishlists/              # useWishlists, useAddWishlistItem…
├── store/
│   ├── index.ts                # RTK store + typed useAppDispatch/Selector
│   └── slices/
│       ├── auth.slice.ts       # user, accessToken, isHydrated
│       ├── ui.slice.ts         # modals, drawers, modalContext
│       └── selection.slice.ts  # activeTraceId, selectedTraceIds…
├── signals/
│   ├── trace-records.ts        # optimistic status counters, loading map
│   ├── warehouse.ts            # intake loading flags, weight counter
│   └── wishlists.ts            # item count signals per wishlist
├── schemas/
│   ├── trace-record.schema.ts  # Valibot: create, status update, certify
│   ├── warehouse-intake.schema.ts
│   └── wishlist.schema.ts
├── lib/
│   ├── django-proxy.ts         # Core BFF proxy (auth, streaming, cache)
│   ├── api-client.ts           # Typed GET/POST/PUT/PATCH/DELETE wrapper
│   ├── query-client.ts         # React Query singleton
│   └── query-keys.ts           # Key factories for cache invalidation
└── types/
    └── index.ts                # All shared domain types
```

---

## BFF Proxy Design

```
Browser → Next.js /api/traceability/trace-records/ → Django /api/v1/traceability/trace-records/
```

`lib/django-proxy.ts` handles:
- ✅ **Dual auth** — HttpOnly cookie (`access_token`) → Bearer header fallback
- ✅ **Streaming** — passthrough for CSV/PDF/octet-stream downloads
- ✅ **Multipart** — passthrough for document/image uploads
- ✅ **Cache-Control** — injected per route type (analytics, scan, static)
- ✅ **Rate limit headers** — forwarded from Django to client
- ✅ **Request ID** — `X-Request-ID` propagated for distributed tracing
- ✅ **502 normalization** — consistent error shape on upstream failure

---

## State Architecture

```
┌─────────────────────────────────────────────────────┐
│  React Query — server state (remote data + cache)   │
│  • useTraceRecords()   • useWishlists()             │
│  • useTracePipeline()  • useWarehouseIntakes()      │
└────────────────────────┬────────────────────────────┘
                         │ onSuccess → dispatch()
┌────────────────────────▼────────────────────────────┐
│  Redux Toolkit — global persistent UI state         │
│  • auth.slice     — user, token, hydration          │
│  • ui.slice       — modals, drawers                 │
│  • selection.slice — active IDs, multi-select       │
└────────────────────────┬────────────────────────────┘
                         │ onMutate → signal.value =
┌────────────────────────▼────────────────────────────┐
│  Preact Signals — reactive local component state    │
│  • Loading flags per record + action                │
│  • Optimistic counters (increment before request)   │
│  • Form dirty flags per domain                      │
└─────────────────────────────────────────────────────┘
```

---

## Environment Variables

Create `.env.local` in this directory:

```env
# URL of Django backend (server-side only — never exposed to browser)
DJANGO_API_URL=http://localhost:8000

# Optional: Next.js public URL (used for absolute URLs in emails etc.)
NEXT_PUBLIC_APP_URL=http://localhost:3000
```

---

## Available Scripts

```bash
npm run dev        # Start dev server (localhost:3000)
npm run build      # Production build
npm run start      # Start production server
npm run lint       # ESLint
npm run type-check # tsc --noEmit
```

---

## Adding a New API Endpoint

1. Add the route handler in `app/api/<namespace>/route.ts`
2. Add the query key factory in `lib/query-keys.ts`
3. Add the hook in `hooks/<domain>/index.ts`
4. Add Valibot schema in `schemas/<domain>.schema.ts` if it has a form
5. Add signal in `signals/<domain>.ts` if it needs optimistic UI

---

## Deployment (Vercel)

```bash
# Set environment variable in Vercel dashboard:
DJANGO_API_URL=https://your-django-backend.railway.app
```

Push to `main` — Vercel auto-deploys.
