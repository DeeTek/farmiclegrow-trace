import { signal, computed } from "@preact/signal-react"

export const loginEmail = signal("")
export const loginPassword = signal("")
export const loginRememberMe = signal(false)
export const loginError = signal<string | null>(null)

export function resetLoginSignals(){
  loginEmail.value = ""
  loginPassword.value = ""
  loginRememberMe.value = false
  loginError.value = null
}
