import axios from "axios"
import { store } from "@/store"
import { clearAuth } from "@/store/slice/auth.slice"

export const clientAxios = axios.create({
  baseURL: process.env.NEXT_CLIENT_API_URL,
  headers: { 
    "Content-Type": "application/json"
  },
  withCredentials: true
})

/***
 * Axios interceptors setup
 * /
 */
