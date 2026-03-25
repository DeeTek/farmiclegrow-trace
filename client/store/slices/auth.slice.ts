/**
 * store/slices/auth.slice.ts — Auth state (RTK)
 */
import { createSlice, PayloadAction } from "@reduxjs/toolkit";
import type { AuthUser } from "@/types";

interface AuthState {
  user: AuthUser | null;
  isHydrated:    boolean;     // true once auth has been checked on mount
  accessToken:   string | null;  // only for Bearer flows (not HttpOnly)
}

const initialState: AuthState = {
  user:        null,
  isHydrated:  false,
  accessToken: null,
};

export const authSlice = createSlice({
  name: "auth",
  initialState,
  reducers: {
    setUser(state, action: PayloadAction<AuthUser | null>) {
      state.user = action.payload;
      state.isHydrated = true;
    },
    setAccessToken(state, action: PayloadAction<string | null>) {
      state.accessToken = action.payload;
    },
    logout(state) {
      state.user = null;
      state.accessToken = null;
    },
    markHydrated(state) {
      state.isHydrated = true;
    },
  },
});

export const { setUser, setAccessToken, logout, markHydrated } = authSlice.actions;

// Selectors
export const selectUser = (s: { auth: AuthState }) => s.auth.user;
export const selectIsHydrated   = (s: { auth: AuthState }) => s.auth.isHydrated;
export const selectAccessToken  = (s: { auth: AuthState }) => s.auth.accessToken;
export const selectIsAdmin      = (s: { auth: AuthState }) =>
  s.auth.user?.is_superuser || s.auth.user?.role === "super_admin";
