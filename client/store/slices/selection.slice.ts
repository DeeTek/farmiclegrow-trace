/**
 * store/slices/selection.slice.ts — Selected/active item IDs (RTK)
 */
import { createSlice, PayloadAction } from "@reduxjs/toolkit";

interface SelectionState {
  activeTraceId:     string | null;
  activeIntakeId:    string | null;
  activeWishlistId:  string | null;
  // Multi-select for bulk actions
  selectedTraceIds:    string[];
  selectedIntakeIds:   string[];
}

const initialState: SelectionState = {
  activeTraceId:    null,
  activeIntakeId:   null,
  activeWishlistId: null,
  selectedTraceIds:  [],
  selectedIntakeIds: [],
};

export const selectionSlice = createSlice({
  name: "selection",
  initialState,
  reducers: {
    setActiveTrace(state, { payload }: PayloadAction<string | null>) {
      state.activeTraceId = payload;
    },
    setActiveIntake(state, { payload }: PayloadAction<string | null>) {
      state.activeIntakeId = payload;
    },
    setActiveWishlist(state, { payload }: PayloadAction<string | null>) {
      state.activeWishlistId = payload;
    },
    toggleTraceSelection(state, { payload }: PayloadAction<string>) {
      const idx = state.selectedTraceIds.indexOf(payload);
      if (idx === -1) state.selectedTraceIds.push(payload);
      else state.selectedTraceIds.splice(idx, 1);
    },
    setTraceSelection(state, { payload }: PayloadAction<string[]>) {
      state.selectedTraceIds = payload;
    },
    clearTraceSelection(state) {
      state.selectedTraceIds = [];
    },
    toggleIntakeSelection(state, { payload }: PayloadAction<string>) {
      const idx = state.selectedIntakeIds.indexOf(payload);
      if (idx === -1) state.selectedIntakeIds.push(payload);
      else state.selectedIntakeIds.splice(idx, 1);
    },
    clearIntakeSelection(state) {
      state.selectedIntakeIds = [];
    },
  },
});

export const {
  setActiveTrace, setActiveIntake, setActiveWishlist,
  toggleTraceSelection, setTraceSelection, clearTraceSelection,
  toggleIntakeSelection, clearIntakeSelection,
} = selectionSlice.actions;

// Selectors
export const selectActiveTraceId    = (s: { selection: SelectionState }) => s.selection.activeTraceId;
export const selectActiveIntakeId   = (s: { selection: SelectionState }) => s.selection.activeIntakeId;
export const selectActiveWishlistId = (s: { selection: SelectionState }) => s.selection.activeWishlistId;
export const selectSelectedTraces   = (s: { selection: SelectionState }) => s.selection.selectedTraceIds;
export const selectSelectedIntakes  = (s: { selection: SelectionState }) => s.selection.selectedIntakeIds;
