/**
 * signals/warehouse.ts
 */
import { signal } from "@preact/signals-react";

export type IntakeAction = "accept" | "reject" | "delete";
export const warehouseLoadingMap = signal<Record<string, IntakeAction | null>>({});

export function setIntakeLoading(id: string, action: IntakeAction | null) {
  warehouseLoadingMap.value = { ...warehouseLoadingMap.value, [id]: action };
}

// Optimistic weight counter (kg received today)
export const optimisticWeightReceived = signal(0);

export function addOptimisticWeight(kg: number) {
  optimisticWeightReceived.value += kg;
}

// Form dirty
export const intakeFormDirty = signal(false);
export function markIntakeDirty() { intakeFormDirty.value = true; }
export function clearIntakeDirty() { intakeFormDirty.value = false; }
