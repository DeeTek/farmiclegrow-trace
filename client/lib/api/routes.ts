
const BFF_AUTH = "/api/auth"
const BFF_API = "/api"

export const ROUTES = {
  auth: {
    login: `${BFF_AUTH}/login`,
    logout: `${BFF_AUTH}/logout`,
    
    me: `${BFF_AUTH}/me`,
    
    
    signup: `${BFF_AUTH}/register`,
    verifyEmail: `${BFF_AUTH}/register/verify-email`,
    resendEmail: `${BFF_AUTH}/register/resend-email`,
    
    changePassword: `${BFF_AUTH}/password/change`,
    resetPassword: `${BFF_AUTH}/password/reset`,
    confirmPassword: `${BFF_AUTH}/password/reset/confirm`,
    
    refreshToken: `${BFF_AUTH}/token/refresh`,
    verifyToken: `${BFF_AUTH}/token/verify`,
    
    
    
    
  }
}