/**
 * hooks/wishlists/index.ts
 */
import {
  useQuery,
  useMutation,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";

import { apiClient, paginatedGet } from "@/lib/api-client";
import { wishlistKeys }            from "@/lib/query-keys";
import {
  setWishlistLoading,
  incrementItemCount,
  decrementItemCount,
} from "@/signals/wishlists";
import { useAppDispatch }          from "@/store";
import { setActiveWishlist }       from "@/store/slices/selection.slice";
import { closeModal }              from "@/store/slices/ui.slice";

import type {
  Wishlist,
  WishlistCreatePayload,
  AddItemPayload,
  MoveToCartPayload,
} from "@/types";

// ── List ──────────────────────────────────────────────────────────────────────
export function useWishlists(params?: Record<string, string>) {
  return useQuery({
    queryKey:    wishlistKeys.list(params),
    queryFn:     () => paginatedGet<Wishlist>("/api/wishlists/", params),
    placeholderData: keepPreviousData,
  });
}

// ── Detail ────────────────────────────────────────────────────────────────────
export function useWishlist(id: string) {
  return useQuery({
    queryKey: wishlistKeys.detail(id),
    queryFn:  () => apiClient.get<Wishlist>(`/api/wishlists/${id}/`),
    enabled:  !!id,
  });
}

// ── Create ────────────────────────────────────────────────────────────────────
export function useCreateWishlist() {
  const qc       = useQueryClient();
  const dispatch = useAppDispatch();

  return useMutation({
    mutationFn: (payload: WishlistCreatePayload) =>
      apiClient.post<Wishlist>("/api/wishlists/", payload),

    onSuccess(wishlist) {
      qc.invalidateQueries({ queryKey: wishlistKeys.lists() });
      dispatch(setActiveWishlist(wishlist.id));
      dispatch(closeModal("createWishlist"));
    },
  });
}

// ── Update ────────────────────────────────────────────────────────────────────
export function useUpdateWishlist(id: string) {
  const qc = useQueryClient();

  return useMutation({
    mutationFn: (payload: Partial<WishlistCreatePayload>) =>
      apiClient.patch<Wishlist>(`/api/wishlists/${id}/`, payload),

    onSuccess(wishlist) {
      qc.setQueryData(wishlistKeys.detail(id), wishlist);
      qc.invalidateQueries({ queryKey: wishlistKeys.lists() });
    },
  });
}

// ── Delete ────────────────────────────────────────────────────────────────────
export function useDeleteWishlist(id: string) {
  const qc       = useQueryClient();
  const dispatch = useAppDispatch();

  return useMutation({
    mutationFn: () => apiClient.delete(`/api/wishlists/${id}/`),

    onMutate() { setWishlistLoading(id, "delete"); },
    onSuccess() {
      qc.removeQueries({ queryKey: wishlistKeys.detail(id) });
      qc.invalidateQueries({ queryKey: wishlistKeys.lists() });
      dispatch(setActiveWishlist(null));
      dispatch(closeModal("confirmDelete"));
    },
    onSettled() { setWishlistLoading(id, null); },
  });
}

// ── Add item ──────────────────────────────────────────────────────────────────
export function useAddWishlistItem(wishlistId: string) {
  const qc       = useQueryClient();
  const dispatch = useAppDispatch();

  return useMutation({
    mutationFn: (payload: AddItemPayload) =>
      apiClient.post(`/api/wishlists/${wishlistId}/add-item/`, payload),

    onMutate() {
      setWishlistLoading(wishlistId, "add-item");
      incrementItemCount(wishlistId);   // optimistic
    },
    onSuccess() {
      qc.invalidateQueries({ queryKey: wishlistKeys.detail(wishlistId) });
      dispatch(closeModal("addWishlistItem"));
    },
    onError() {
      decrementItemCount(wishlistId);   // rollback optimistic
    },
    onSettled() { setWishlistLoading(wishlistId, null); },
  });
}

// ── Move to cart ──────────────────────────────────────────────────────────────
export function useMoveToCart(wishlistId: string) {
  const qc = useQueryClient();

  return useMutation({
    mutationFn: (payload?: MoveToCartPayload) =>
      apiClient.post(`/api/wishlists/${wishlistId}/move-to-cart/`, payload ?? {}),

    onMutate() { setWishlistLoading(wishlistId, "move-to-cart"); },
    onSuccess() {
      qc.invalidateQueries({ queryKey: wishlistKeys.detail(wishlistId) });
      // Invalidate cart too if you have cart keys
      qc.invalidateQueries({ queryKey: ["cart"] });
    },
    onSettled() { setWishlistLoading(wishlistId, null); },
  });
}

// ── Remove item ───────────────────────────────────────────────────────────────
export function useRemoveWishlistItem(wishlistId: string) {
  const qc = useQueryClient();

  return useMutation({
    mutationFn: (productId: string) =>
      apiClient.delete(`/api/wishlists/${wishlistId}/remove-item/${productId}/`),

    onMutate() {
      setWishlistLoading(wishlistId, "remove-item");
      decrementItemCount(wishlistId);   // optimistic
    },
    onSuccess() {
      qc.invalidateQueries({ queryKey: wishlistKeys.detail(wishlistId) });
    },
    onError() {
      incrementItemCount(wishlistId);   // rollback
    },
    onSettled() { setWishlistLoading(wishlistId, null); },
  });
}
