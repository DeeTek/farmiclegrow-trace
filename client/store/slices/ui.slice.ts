/**
 * store/slices/ui.slice.ts — Modals, drawers, global UI (RTK)
 */
import { createSlice, PayloadAction } from "@reduxjs/toolkit";

type ModalKey =
  | "createTraceRecord"
  | "updateTraceStatus"
  | "certifyTrace"
  | "createIntake"
  | "rejectIntake"
  | "createWishlist"
  | "addWishlistItem"
  | "confirmDelete";

type DrawerKey =
  | "traceDetail"
  | "warehouseDetail"
  | "wishlistDetail"
  | "filters";

interface UIState {
  modals:  Partial<Record<ModalKey, boolean>>;
  drawers: Partial<Record<DrawerKey, boolean>>;
  // Payload carried into the modal (e.g. which record to delete)
  modalContext: Record<string, unknown>;
}

const initialState: UIState = {
  modals:       {},
  drawers:      {},
  modalContext: {},
};

export const uiSlice = createSlice({
  name: "ui",
  initialState,
  reducers: {
    openModal(
      state,
      action: PayloadAction<{ key: ModalKey; context?: Record<string, unknown> }>,
    ) {
      state.modals[action.payload.key] = true;
      if (action.payload.context) {
        state.modalContext = { ...state.modalContext, ...action.payload.context };
      }
    },
    closeModal(state, action: PayloadAction<ModalKey>) {
      state.modals[action.payload] = false;
    },
    closeAllModals(state) {
      state.modals = {};
      state.modalContext = {};
    },
    openDrawer(state, action: PayloadAction<DrawerKey>) {
      state.drawers[action.payload] = true;
    },
    closeDrawer(state, action: PayloadAction<DrawerKey>) {
      state.drawers[action.payload] = false;
    },
    toggleDrawer(state, action: PayloadAction<DrawerKey>) {
      state.drawers[action.payload] = !state.drawers[action.payload];
    },
  },
});

export const {
  openModal, closeModal, closeAllModals,
  openDrawer, closeDrawer, toggleDrawer,
} = uiSlice.actions;

// Selectors
export const selectModal   = (key: ModalKey)  => (s: { ui: UIState }) => !!s.ui.modals[key];
export const selectDrawer  = (key: DrawerKey) => (s: { ui: UIState }) => !!s.ui.drawers[key];
export const selectContext = (s: { ui: UIState }) => s.ui.modalContext;
