/**
 * signals/trace-records.ts
 *
 * Preact signals for trace-record domain.
 * Used for: optimistic counters, per-component loading flags, form dirty state.
 */
import { signal, computed } from "@preact/signals-react";

// ── Optimistic counters ───────────────────────────────────────────────────────
export const optimisticStatusCount = signal<Record<string, number>>({});

export function incrementStatus(status: string) {
  optimisticStatusCount.value = {
    ...optimisticStatusCount.value,
    [status]: (optimisticStatusCount.value[status] ?? 0) + 1,
  };
}

export function decrementStatus(status: string) {
  optimisticStatusCount.value = {
    ...optimisticStatusCount.value,
    [status]: Math.max((optimisticStatusCount.value[status] ?? 1) - 1, 0),
  };
}

// ── Per-component loading flags ───────────────────────────────────────────────
// Key: trace record id, value: which action is in-flight
export type TraceLoadingAction = "certify" | "update-status" | "delete";
export const traceLoadingMap = signal<Record<string, TraceLoadingAction | null>>({});

export function setTraceLoading(id: string, action: TraceLoadingAction | null) {
  traceLoadingMap.value = { ...traceLoadingMap.value, [id]: action };
}

export function isTraceLoading(id: string) {
  return computed(() => traceLoadingMap.value[id] !== null && traceLoadingMap.value[id] !== undefined);
}

// ── Form dirty state ──────────────────────────────────────────────────────────
export const traceFormDirty = signal(false);
export const traceFormId = signal<string | null>(null);   // which record is being edited

export function markTraceDirty(id: string) {
  traceFormDirty.value = true;
  traceFormId.value = id;
}

export function clearTraceDirty() {
  traceFormDirty.value = false;
  traceFormId.value = null;
}
