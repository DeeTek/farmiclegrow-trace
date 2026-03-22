/**
 * signals/wishlists.ts
 */
import { signal, computed } from "@preact/signals-react";

// Optimistic item count per wishlist
export const optimisticItemCounts = signal<Record<string, number>>({});

export function incrementItemCount(wishlistId: string) {
  optimisticItemCounts.value = {
    ...optimisticItemCounts.value,
    [wishlistId]: (optimisticItemCounts.value[wishlistId] ?? 0) + 1,
  };
}

export function decrementItemCount(wishlistId: string) {
  optimisticItemCounts.value = {
    ...optimisticItemCounts.value,
    [wishlistId]: Math.max((optimisticItemCounts.value[wishlistId] ?? 1) - 1, 0),
  };
}

export function getItemCount(wishlistId: string) {
  return computed(() => optimisticItemCounts.value[wishlistId] ?? 0);
}

// Loading flags
export type WishlistAction = "add-item" | "move-to-cart" | "remove-item" | "delete";
export const wishlistLoadingMap = signal<Record<string, WishlistAction | null>>({});

export function setWishlistLoading(id: string, action: WishlistAction | null) {
  wishlistLoadingMap.value = { ...wishlistLoadingMap.value, [id]: action };
}

// Form dirty
export const wishlistFormDirty = signal(false);
export function markWishlistDirty() { wishlistFormDirty.value = true; }
export function clearWishlistDirty() { wishlistFormDirty.value = false; }
