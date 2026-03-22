/**
 * lib/api-client.ts
 *
 * Typed BFF API client — calls Next.js /api/* route handlers (not Django directly).
 * Handles: serialization, error normalization, multipart forms.
 */

import type { ApiError, PaginatedResponse } from "@/types";

const BFF_BASE = "";   // same origin — Next.js handles the proxy

// ── Custom error ──────────────────────────────────────────────────────────────
export class ApiRequestError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: ApiError,
  ) {
    super(body.message ?? `Request failed with status ${status}`);
    this.name = "ApiRequestError";
  }
}

// ── Core fetch wrapper ────────────────────────────────────────────────────────
async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${BFF_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...init.headers,
    },
    credentials: "include",   // send HttpOnly cookie
  });

  if (!res.ok) {
    let body: ApiError = { message: res.statusText };
    try { body = await res.json(); } catch { /* non-JSON error */ }
    throw new ApiRequestError(res.status, body);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Convenience methods ───────────────────────────────────────────────────────
export const apiClient = {
  get<T>(path: string, params?: Record<string, string>): Promise<T> {
    const url = params
      ? `${path}?${new URLSearchParams(params)}`
      : path;
    return request<T>(url, { method: "GET" });
  },

  post<T>(path: string, body?: unknown): Promise<T> {
    return request<T>(path, {
      method: "POST",
      body: body instanceof FormData ? body : JSON.stringify(body ?? {}),
    });
  },

  put<T>(path: string, body?: unknown): Promise<T> {
    return request<T>(path, {
      method: "PUT",
      body: JSON.stringify(body ?? {}),
    });
  },

  patch<T>(path: string, body?: unknown): Promise<T> {
    return request<T>(path, {
      method: "PATCH",
      body: JSON.stringify(body ?? {}),
    });
  },

  delete<T = void>(path: string): Promise<T> {
    return request<T>(path, { method: "DELETE" });
  },
};

// ── Paginated helper ──────────────────────────────────────────────────────────
export function paginatedGet<T>(
  path: string,
  params?: Record<string, string>,
): Promise<PaginatedResponse<T>> {
  return apiClient.get<PaginatedResponse<T>>(path, params);
}
