/**
 * lib/django-proxy.ts — FarmicleGrow-Trace BFF Proxy
 *
 * Features:
 *  • Dual auth:  HttpOnly cookie (access_token) → Bearer header fallback
 *  • Streaming passthrough for file downloads (CSV, PDF, octet-stream)
 *  • Multipart passthrough for document/image uploads
 *  • Cache-Control injection per route type
 *  • Rate-limit header forwarding from Django
 *  • Request ID propagation for distributed tracing
 *  • Structured dev logging
 *  • 502 normalization with request ID on upstream failure
 */
import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

const DJANGO_BASE = process.env.DJANGO_API_URL ?? "http://localhost:8000";
const IS_DEV      = process.env.NODE_ENV !== "production";

// ── Unique request ID ────────────────────────────────────────────────────────
function newRequestId(): string {
  return `bff-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

// ── Auth resolution ──────────────────────────────────────────────────────────
async function resolveToken(request: NextRequest): Promise<string | null> {
  const cookieStore = await cookies();
  const cookieToken = cookieStore.get("access_token")?.value;
  if (cookieToken) return cookieToken;
  const auth = request.headers.get("authorization");
  if (auth?.startsWith("Bearer ")) return auth.slice(7);
  return null;
}

// ── Body resolution ──────────────────────────────────────────────────────────
type ResolvedBody = { body: BodyInit | null; contentType: string | null };

async function resolveBody(request: NextRequest, method: string): Promise<ResolvedBody> {
  if (!["POST", "PUT", "PATCH"].includes(method)) return { body: null, contentType: null };
  const ct = request.headers.get("content-type") ?? "";
  if (ct.includes("multipart/form-data")) {
    return { body: await request.formData(), contentType: null }; // fetch sets boundary
  }
  const text = await request.text();
  return { body: text || "{}", contentType: "application/json" };
}

// ── Cache-Control by route ────────────────────────────────────────────────────
function cacheHeader(djangoPath: string, status: number): string | null {
  if (status !== 200) return null;
  if (djangoPath.includes("/analytics/"))  return "public, s-maxage=60, stale-while-revalidate=300";
  if (djangoPath.includes("/scan/"))       return "public, s-maxage=120, stale-while-revalidate=600";
  if (djangoPath.includes("/version"))     return "public, s-maxage=3600";
  if (djangoPath.includes("/health"))      return "no-store";
  return "private, no-cache";
}

// ── Rate-limit header forwarding ─────────────────────────────────────────────
const RL_HEADERS = ["X-RateLimit-Limit","X-RateLimit-Remaining","X-RateLimit-Reset","Retry-After"];
function forwardRateLimitHeaders(upstream: Response, res: NextResponse): void {
  RL_HEADERS.forEach((h) => {
    const v = upstream.headers.get(h);
    if (v) res.headers.set(h, v);
  });
}

// ── Core proxy ────────────────────────────────────────────────────────────────
export async function djangoProxy(
  request: NextRequest,
  djangoPath: string,
  overrideMethod?: string,
): Promise<NextResponse> {
  const requestId = newRequestId();
  const method    = (overrideMethod ?? request.method).toUpperCase();
  const start     = Date.now();

  // Build upstream URL with forwarded query params
  const url = new URL(`${DJANGO_BASE}${djangoPath}`);
  request.nextUrl.searchParams.forEach((v, k) => url.searchParams.set(k, v));

  const token                  = await resolveToken(request);
  const { body, contentType }  = await resolveBody(request, method);

  const headers: Record<string, string> = {
    Accept:          "application/json",
    "X-Request-ID":  requestId,
    "X-BFF-Version": "1.0",
  };
  if (token)       headers["Authorization"] = `Bearer ${token}`;
  if (contentType) headers["Content-Type"]  = contentType;

  if (IS_DEV) console.log(`[BFF →] ${method} ${url.toString()}`);

  try {
    const upstream = await fetch(url.toString(), {
      method,
      headers,
      body: body ?? undefined,
      cache: "no-store",
    });

    if (IS_DEV) {
      console.log(`[BFF ←] ${upstream.status} ${method} ${djangoPath} (${Date.now() - start}ms)`);
    }

    // Streaming passthrough for downloads
    const upCT = upstream.headers.get("content-type") ?? "";
    const isStream =
      upCT.includes("application/octet-stream") ||
      upCT.includes("application/pdf") ||
      upCT.includes("text/csv") ||
      upCT.includes("application/vnd.");
    if (isStream) {
      return new NextResponse(upstream.body, {
        status: upstream.status,
        headers: {
          "Content-Type":        upCT,
          "Content-Disposition": upstream.headers.get("content-disposition") ?? "attachment",
          "X-Request-ID":        requestId,
        },
      });
    }

    // JSON / empty response
    let data: unknown = null;
    if (upstream.status !== 204) {
      try { data = await upstream.json(); } catch { /* non-JSON */ }
    }

    const res = NextResponse.json(data, { status: upstream.status });
    const cc  = cacheHeader(djangoPath, upstream.status);
    if (cc) res.headers.set("Cache-Control", cc);
    forwardRateLimitHeaders(upstream, res);
    res.headers.set("X-Request-ID", requestId);
    return res;

  } catch (err) {
    console.error(`[BFF ✗] ${method} ${djangoPath} — ${Date.now() - start}ms`, err);
    return NextResponse.json(
      { message: "Service temporarily unavailable." },
      { status: 502, headers: { "X-Request-ID": requestId } },
    );
  }
}
