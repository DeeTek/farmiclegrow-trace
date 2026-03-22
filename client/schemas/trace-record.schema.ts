/**
 * schemas/trace-record.schema.ts — Valibot schemas for trace record forms
 */
import * as v from "valibot";

export const TraceStatusValues = [
  "active", "in_transit", "at_warehouse", "processing",
  "exported", "delivered", "recalled", "cancelled",
] as const;

export const TraceRecordCreateSchema = v.object({
  farmer:        v.pipe(v.string(), v.uuid("Invalid farmer ID")),
  farm:          v.pipe(v.string(), v.uuid("Invalid farm ID")),
  product:       v.pipe(v.string(), v.uuid("Invalid product ID")),
  field_officer: v.optional(v.pipe(v.string(), v.uuid())),
  weight_kg:     v.pipe(v.number(), v.minValue(0.01, "Weight must be > 0")),
  harvest_date:  v.pipe(v.string(), v.isoDate("Invalid date format")),
  notes:         v.optional(v.string()),
});

export const TraceStatusUpdateSchema = v.object({
  status:               v.picklist(TraceStatusValues, "Invalid status"),
  destination_country:  v.optional(v.pipe(v.string(), v.minLength(2))),
  note:                 v.optional(v.string()),
});

export const CertifySchema = v.object({
  certification_type: v.pipe(v.string(), v.minLength(2)),
  issuing_body:       v.pipe(v.string(), v.minLength(2)),
  issued_date:        v.pipe(v.string(), v.isoDate()),
  expiry_date:        v.optional(v.pipe(v.string(), v.isoDate())),
});

export type TraceRecordCreateInput = v.InferInput<typeof TraceRecordCreateSchema>;
export type TraceStatusUpdateInput = v.InferInput<typeof TraceStatusUpdateSchema>;
export type CertifyInput           = v.InferInput<typeof CertifySchema>;
