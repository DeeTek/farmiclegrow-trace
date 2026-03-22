/**
 * hooks/warehouse-intakes/index.ts
 */
import {
  useQuery,
  useMutation,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";

import { apiClient, paginatedGet } from "@/lib/api-client";
import { warehouseKeys }           from "@/lib/query-keys";
import { setIntakeLoading, addOptimisticWeight } from "@/signals/warehouse";
import { useAppDispatch }          from "@/store";
import { setActiveIntake }         from "@/store/slices/selection.slice";
import { closeModal }              from "@/store/slices/ui.slice";

import type {
  PaginatedResponse,
  WarehouseIntake,
  WarehouseIntakeCreatePayload,
  RejectPayload,
} from "@/types";

// ── List ──────────────────────────────────────────────────────────────────────
export function useWarehouseIntakes(params?: Record<string, string>) {
  return useQuery({
    queryKey:    warehouseKeys.list(params),
    queryFn:     () => paginatedGet<WarehouseIntake>("/api/warehouse-intakes/", params),
    placeholderData: keepPreviousData,
  });
}

// ── Detail ────────────────────────────────────────────────────────────────────
export function useWarehouseIntake(id: string) {
  return useQuery({
    queryKey: warehouseKeys.detail(id),
    queryFn:  () => apiClient.get<WarehouseIntake>(`/api/warehouse-intakes/${id}/`),
    enabled:  !!id,
  });
}

// ── Create ────────────────────────────────────────────────────────────────────
export function useCreateWarehouseIntake() {
  const qc       = useQueryClient();
  const dispatch = useAppDispatch();

  return useMutation({
    mutationFn: (payload: WarehouseIntakeCreatePayload) =>
      apiClient.post<WarehouseIntake>("/api/warehouse-intakes/", payload),

    onSuccess(intake) {
      qc.invalidateQueries({ queryKey: warehouseKeys.lists() });
      dispatch(setActiveIntake(intake.id));
      dispatch(closeModal("createIntake"));
      addOptimisticWeight(intake.weight_kg);
    },
  });
}

// ── Update ────────────────────────────────────────────────────────────────────
export function useUpdateWarehouseIntake(id: string) {
  const qc = useQueryClient();

  return useMutation({
    mutationFn: (payload: Partial<WarehouseIntakeCreatePayload>) =>
      apiClient.patch<WarehouseIntake>(`/api/warehouse-intakes/${id}/`, payload),

    onSuccess(intake) {
      qc.setQueryData(warehouseKeys.detail(id), intake);
      qc.invalidateQueries({ queryKey: warehouseKeys.lists() });
    },
  });
}

// ── Delete ────────────────────────────────────────────────────────────────────
export function useDeleteWarehouseIntake(id: string) {
  const qc       = useQueryClient();
  const dispatch = useAppDispatch();

  return useMutation({
    mutationFn: () => apiClient.delete(`/api/warehouse-intakes/${id}/`),

    onMutate() { setIntakeLoading(id, "delete"); },
    onSuccess() {
      qc.removeQueries({ queryKey: warehouseKeys.detail(id) });
      qc.invalidateQueries({ queryKey: warehouseKeys.lists() });
      dispatch(setActiveIntake(null));
      dispatch(closeModal("confirmDelete"));
    },
    onSettled() { setIntakeLoading(id, null); },
  });
}

// ── Accept ────────────────────────────────────────────────────────────────────
export function useAcceptIntake(id: string) {
  const qc = useQueryClient();

  return useMutation({
    mutationFn: () =>
      apiClient.post<WarehouseIntake>(`/api/warehouse-intakes/${id}/accept/`),

    onMutate() { setIntakeLoading(id, "accept"); },
    onSuccess(intake) {
      qc.setQueryData(warehouseKeys.detail(id), intake);
      qc.invalidateQueries({ queryKey: warehouseKeys.lists() });
    },
    onSettled() { setIntakeLoading(id, null); },
  });
}

// ── Reject ────────────────────────────────────────────────────────────────────
export function useRejectIntake(id: string) {
  const qc       = useQueryClient();
  const dispatch = useAppDispatch();

  return useMutation({
    mutationFn: (payload: RejectPayload) =>
      apiClient.post<WarehouseIntake>(`/api/warehouse-intakes/${id}/reject/`, payload),

    onMutate() { setIntakeLoading(id, "reject"); },
    onSuccess(intake) {
      qc.setQueryData(warehouseKeys.detail(id), intake);
      qc.invalidateQueries({ queryKey: warehouseKeys.lists() });
      dispatch(closeModal("rejectIntake"));
    },
    onSettled() { setIntakeLoading(id, null); },
  });
}
