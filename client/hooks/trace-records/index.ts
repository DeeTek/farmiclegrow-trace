/**
 * hooks/trace-records/index.ts
 *
 * React Query hooks for the trace-records domain.
 * All mutations integrate with:
 *   - Preact signals (loading flags, optimistic counters)
 *   - RTK (active selection, modal context)
 */
import {
  useQuery,
  useMutation,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";

import { apiClient, paginatedGet } from "@/lib/api-client";
import { traceKeys }               from "@/lib/query-keys";
import {
  setTraceLoading,
  incrementStatus,
  decrementStatus,
} from "@/signals/trace-records";
import { useAppDispatch }          from "@/store";
import { setActiveTrace }          from "@/store/slices/selection.slice";
import { closeModal }              from "@/store/slices/ui.slice";

import type {
  PaginatedResponse,
  TraceRecord,
  TraceRecordCreatePayload,
  TraceStatusUpdatePayload,
  CertifyPayload,
  TraceChain,
  DestinationSummaryItem,
  StatusPipeline,
} from "@/types";

// ── List ──────────────────────────────────────────────────────────────────────
export function useTraceRecords(params?: Record<string, string>) {
  return useQuery({
    queryKey:    traceKeys.list(params),
    queryFn:     () => paginatedGet<TraceRecord>("/api/trace-records/", params),
    placeholderData: keepPreviousData,
  });
}

// ── Detail ────────────────────────────────────────────────────────────────────
export function useTraceRecord(id: string) {
  return useQuery({
    queryKey: traceKeys.detail(id),
    queryFn:  () => apiClient.get<TraceRecord>(`/api/trace-records/${id}/`),
    enabled:  !!id,
  });
}

// ── Chain ─────────────────────────────────────────────────────────────────────
export function useTraceChain(id: string) {
  return useQuery({
    queryKey: traceKeys.chain(id),
    queryFn:  () => apiClient.get<TraceChain>(`/api/trace-records/${id}/chain/`),
    enabled:  !!id,
    staleTime: 1000 * 60 * 5,   // chain changes rarely
  });
}

// ── Pipeline (status counts) ──────────────────────────────────────────────────
export function useTracePipeline() {
  return useQuery({
    queryKey: traceKeys.pipeline(),
    queryFn:  () => apiClient.get<StatusPipeline>("/api/trace-records/pipeline/"),
    staleTime: 1000 * 30,
  });
}

// ── Destination summary ───────────────────────────────────────────────────────
export function useDestinationSummary() {
  return useQuery({
    queryKey: traceKeys.destinations(),
    queryFn:  () => apiClient.get<DestinationSummaryItem[]>("/api/trace-records/destination-summary/"),
  });
}

// ── Create ────────────────────────────────────────────────────────────────────
export function useCreateTraceRecord() {
  const qc       = useQueryClient();
  const dispatch = useAppDispatch();

  return useMutation({
    mutationFn: (payload: TraceRecordCreatePayload) =>
      apiClient.post<TraceRecord>("/api/trace-records/", payload),

    onSuccess(record) {
      qc.invalidateQueries({ queryKey: traceKeys.lists() });
      qc.invalidateQueries({ queryKey: traceKeys.pipeline() });
      dispatch(setActiveTrace(record.id));
      dispatch(closeModal("createTraceRecord"));
      incrementStatus(record.status);
    },
  });
}

// ── Update (full) ─────────────────────────────────────────────────────────────
export function useUpdateTraceRecord(id: string) {
  const qc = useQueryClient();

  return useMutation({
    mutationFn: (payload: Partial<TraceRecordCreatePayload>) =>
      apiClient.patch<TraceRecord>(`/api/trace-records/${id}/`, payload),

    onSuccess(record) {
      qc.setQueryData(traceKeys.detail(id), record);
      qc.invalidateQueries({ queryKey: traceKeys.lists() });
    },
  });
}

// ── Delete ────────────────────────────────────────────────────────────────────
export function useDeleteTraceRecord(id: string) {
  const qc       = useQueryClient();
  const dispatch = useAppDispatch();

  return useMutation({
    mutationFn: () => apiClient.delete(`/api/trace-records/${id}/`),

    onMutate() {
      setTraceLoading(id, "delete");
    },
    onSuccess() {
      qc.removeQueries({ queryKey: traceKeys.detail(id) });
      qc.invalidateQueries({ queryKey: traceKeys.lists() });
      qc.invalidateQueries({ queryKey: traceKeys.pipeline() });
      dispatch(setActiveTrace(null));
      dispatch(closeModal("confirmDelete"));
    },
    onSettled() {
      setTraceLoading(id, null);
    },
  });
}

// ── Update status ─────────────────────────────────────────────────────────────
export function useUpdateTraceStatus(id: string) {
  const qc = useQueryClient();

  return useMutation({
    mutationFn: (payload: TraceStatusUpdatePayload) =>
      apiClient.post<TraceRecord>(`/api/trace-records/${id}/update-status/`, payload),

    onMutate(payload) {
      setTraceLoading(id, "update-status");
      // Optimistic status counter
      const prev = qc.getQueryData<TraceRecord>(traceKeys.detail(id));
      if (prev) {
        decrementStatus(prev.status);
        incrementStatus(payload.status);
      }
    },
    onSuccess(record) {
      qc.setQueryData(traceKeys.detail(id), record);
      qc.invalidateQueries({ queryKey: traceKeys.pipeline() });
      qc.invalidateQueries({ queryKey: traceKeys.lists() });
      dispatch(closeModal("updateTraceStatus"));
    },
    onSettled() {
      setTraceLoading(id, null);
    },
  });
}

// ── Certify ───────────────────────────────────────────────────────────────────
export function useCertifyTraceRecord(id: string) {
  const qc       = useQueryClient();
  const dispatch = useAppDispatch();

  return useMutation({
    mutationFn: (payload: CertifyPayload) =>
      apiClient.post(`/api/trace-records/${id}/certify/`, payload),

    onMutate() {
      setTraceLoading(id, "certify");
    },
    onSuccess() {
      qc.invalidateQueries({ queryKey: traceKeys.detail(id) });
      qc.invalidateQueries({ queryKey: traceKeys.chain(id) });
      dispatch(closeModal("certifyTrace"));
    },
    onSettled() {
      setTraceLoading(id, null);
    },
  });
}
