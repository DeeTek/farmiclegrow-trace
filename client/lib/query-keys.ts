/**
 * lib/query-keys.ts
 *
 * Centralised React Query key factories.
 * Use these in every useQuery/useMutation call to ensure
 * consistent cache invalidation across the app.
 */

export const traceKeys = {
  all:                () => ["trace-records"]                            as const,
  lists:              () => [...traceKeys.all(), "list"]                 as const,
  list:    (f?: object)  => [...traceKeys.lists(), f ?? {}]              as const,
  details:            () => [...traceKeys.all(), "detail"]               as const,
  detail:  (id: string)  => [...traceKeys.details(), id]                 as const,
  chain:   (id: string)  => [...traceKeys.detail(id), "chain"]           as const,
  pipeline:           () => [...traceKeys.all(), "pipeline"]             as const,
  destinations:       () => [...traceKeys.all(), "destination-summary"]  as const,
};

export const warehouseKeys = {
  all:               () => ["warehouse-intakes"]                         as const,
  lists:             () => [...warehouseKeys.all(), "list"]              as const,
  list:   (f?: object)  => [...warehouseKeys.lists(), f ?? {}]           as const,
  details:           () => [...warehouseKeys.all(), "detail"]            as const,
  detail: (id: string)  => [...warehouseKeys.details(), id]              as const,
};

export const wishlistKeys = {
  all:               () => ["wishlists"]                                 as const,
  lists:             () => [...wishlistKeys.all(), "list"]               as const,
  list:   (f?: object)  => [...wishlistKeys.lists(), f ?? {}]            as const,
  details:           () => [...wishlistKeys.all(), "detail"]             as const,
  detail: (id: string)  => [...wishlistKeys.details(), id]               as const,
};
