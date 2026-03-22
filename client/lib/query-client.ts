/**
 * lib/query-client.ts
 *
 * Singleton React Query client with production-safe defaults.
 * Import this wherever you need to imperatively invalidate / prefetch.
 */
import { QueryClient } from "@tanstack/react-query";
import { ApiRequestError } from "@/lib/api-client";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 1000 * 60 * 2,          // 2 min
        gcTime:    1000 * 60 * 10,          // 10 min
        retry(failureCount, error) {
          if (error instanceof ApiRequestError) {
            // Never retry auth / not-found errors
            if ([401, 403, 404].includes(error.status)) return false;
          }
          return failureCount < 2;
        },
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

let browserClient: QueryClient | undefined;

export function getQueryClient(): QueryClient {
  if (typeof window === "undefined") {
    // Server: always create new client
    return makeQueryClient();
  }
  // Browser: reuse singleton
  if (!browserClient) browserClient = makeQueryClient();
  return browserClient;
}
