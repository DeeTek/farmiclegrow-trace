/**
 * schemas/wishlist.schema.ts
 */
import * as v from "valibot";

export const WishlistCreateSchema = v.object({
  name: v.pipe(v.string(), v.minLength(1, "Name is required"), v.maxLength(100)),
});

export const AddItemSchema = v.object({
  product: v.pipe(v.string(), v.uuid("Invalid product ID")),
});

export type WishlistCreateInput = v.InferInput<typeof WishlistCreateSchema>;
export type AddItemInput        = v.InferInput<typeof AddItemSchema>;
