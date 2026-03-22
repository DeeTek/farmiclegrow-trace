/**
 * schemas/warehouse-intake.schema.ts
 */
import * as v from "valibot";

export const WarehouseIntakeCreateSchema = v.object({
  batch:     v.pipe(v.string(), v.uuid("Invalid batch ID")),
  warehouse: v.pipe(v.string(), v.uuid("Invalid warehouse ID")),
  weight_kg: v.pipe(v.number(), v.minValue(0.01, "Weight must be > 0")),
});

export const RejectIntakeSchema = v.object({
  reason: v.pipe(v.string(), v.minLength(10, "Provide at least 10 characters")),
});

export type WarehouseIntakeCreateInput = v.InferInput<typeof WarehouseIntakeCreateSchema>;
export type RejectIntakeInput          = v.InferInput<typeof RejectIntakeSchema>;
