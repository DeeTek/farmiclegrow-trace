

const AUTH_BASE = "/account/v1"
const API_BASE = "/api/v1"

export const ENDPOINTS = {
  auth: {
    // Login and Logout
    login: `${AUTH_BASE}/login/`,
    logout: `${AUTH_BASE}/logout/`,
    
    // Register and verify email
    signup: `${AUTH_BASE}/register/`,
    verifyEmail: `${AUTH_BASE}/register/verify-email/`,
    resendEmail: `${AUTH_BASE}/register/resend-email/`,
    
    // Password 
    changePassword: `${AUTH_BASE}/password/change/`,
    resetPassword: `${AUTH_BASE}/password/reset/`,
    confirmPassword: `${AUTH_BASE}/password/reset/confirm/`,
    
    // Tokens
    refreshToken: `${AUTH_BASE}/token/refresh/`,
    verifyToken: `${AUTH_BASE}/token/verify/`,
    
    // Social logins
    googleLogin: `${AUTH_BASE}/social/google/`,
    googleCallback: `${AUTH_BASE}/social/google/callback/`,
    facebookLogin: `${AUTH_BASE}/social/facebook/`,
    facebookCallback: `${AUTH_BASE}/social/facebook/callback/`,
    
    
  },
  
  staff: {}
}