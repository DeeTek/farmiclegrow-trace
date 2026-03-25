"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useDispatch } from "react-redux"
import { useRouter } from "next/navigation";
import { clientAxios } from "@/lib/axios/client";
import { ROUTES } from "@/lib/api/routes";          
import { setAuth } from "@/store/slices/authSlice";
import { authKeys } from "@/lib/query/keys";
import { loginError, resetLoginSignals } from "@/lib/signals/auth.signals";

interface LoginPayload {
  email: string,
  password: string,
  rememberMe: boolean,
}

export function useLogin(){
  const dispatch = useDispatch()
  const queryClient = useQueryClient()
  const router = useRouter()
  
  return useMutation({
    mutationFn: (payload: LoginPayload) => clientAxios.post()
    onSuccess: () => 
    onError: () => 
  })
}
