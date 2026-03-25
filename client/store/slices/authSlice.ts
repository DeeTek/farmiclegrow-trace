import { createSlice, PayloadAction } from "@reduxjs/toolkit"
import type { AuthUser } from "@/types"


interface AuthState = {
  user: AuthUser | null,
  isAuthenticated: boolean,
  isHydrated: boolean
}

const initialState: AuthState = {
  user: null,
  isAuthenticated: false,
  isHydrated: false
}

export const authSlice = createSlice({
  name: "auth",
  initialState,
  reducers: {
    setAuth(state, action: PayloadAction<AuthUser>){
      state.user = action.payload;
      state.isAuthenticated = true;
      state.isHydrated = true;
    },
    clearAuth(state){
      state.user = null;
      state.isAuthenticated = false;
      state.isHydrated = false;
    },
    setHydrated(state){
      state.isHydrated = true;
    }
  }
})

export { setAuth, clearAuth, setHydrated } = authSlice.actions;
export default authSlice.reducer;