// =============================================================================
// Shared domain types — FarmicleGrow-Trace
// =============================================================================

export type UUID = string;

// ── Pagination ────────────────────────────────────────────────────────────────
export interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

// ── API error shape from Django global exception handler ──────────────────────
export interface ApiError {
  message: string;
  [key: string]: unknown;
}

// ── TraceRecord ───────────────────────────────────────────────────────────────
export type TraceStatus =
  | "active"
  | "in_transit"
  | "at_warehouse"
  | "processing"
  | "exported"
  | "delivered"
  | "recalled"
  | "cancelled";

export interface TraceRecord {
  id: UUID;
  trace_code: string;
  farmer_batch_code: string;
  warehouse_batch_code: string;
  product_batch_code: string;
  status: TraceStatus;
  farmer: UUID;
  farm: UUID;
  product: UUID;
  field_officer: UUID | null;
  warehouse_intake: UUID | null;
  weight_kg: number;
  harvest_date: string;
  export_destination_country: string | null;
  notes: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface TraceRecordCreatePayload {
  farmer: UUID;
  farm: UUID;
  product: UUID;
  field_officer?: UUID;
  weight_kg: number;
  harvest_date: string;
  notes?: string;
}

export interface TraceStatusUpdatePayload {
  status: TraceStatus;
  destination_country?: string;
  note?: string;
}

export interface CertifyPayload {
  certification_type: string;
  issuing_body: string;
  issued_date: string;
  expiry_date?: string;
  document?: File;
}

export interface TraceChain {
  trace_code: string;
  farmer: Record<string, unknown>;
  farm: Record<string, unknown>;
  processing_steps: Record<string, unknown>[];
  certifications: Record<string, unknown>[];
}

export interface DestinationSummaryItem {
  export_destination_country: string;
  shipments: number;
  total_weight_kg: number;
  farmers_count: number;
}

export interface StatusPipeline {
  active: number;
  in_transit: number;
  at_warehouse: number;
  processing: number;
  exported: number;
  delivered: number;
  recalled: number;
  cancelled: number;
}

// ── WarehouseIntake ───────────────────────────────────────────────────────────
export type IntakeStatus =
  | "received"
  | "under_qc"
  | "passed"
  | "rejected"
  | "processed";

export interface WarehouseIntake {
  id: UUID;
  status: IntakeStatus;
  batch: UUID;
  warehouse: UUID;
  received_by: UUID;
  received_at: string;
  weight_kg: number;
  rejection_reason: string;
  created_at: string;
  updated_at: string;
}

export interface WarehouseIntakeCreatePayload {
  batch: UUID;
  warehouse: UUID;
  weight_kg: number;
}

export interface RejectPayload {
  reason: string;
}

// ── Wishlist ──────────────────────────────────────────────────────────────────
export interface Wishlist {
  id: UUID;
  name: string;
  buyer: UUID;
  items: WishlistItem[];
  created_at: string;
}

export interface WishlistItem {
  id: UUID;
  product: UUID;
  added_at: string;
}

export interface WishlistCreatePayload {
  name: string;
}

export interface AddItemPayload {
  product: UUID;
}

export interface MoveToCartPayload {
  item_ids?: UUID[];
}

// ── Auth ──────────────────────────────────────────────────────────────────────
export interface AuthUser {
  id: UUID;
  email: string;
  role: string;
  is_staff: boolean;
  is_superuser: boolean;
}
