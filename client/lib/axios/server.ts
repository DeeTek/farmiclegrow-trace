import axios from "axios"

export const serverAxios = axios.create({
  baseURL: process.env.DJANGO_SERVER_URL,
  headers: {
    "Content-Type": "application/json"
  },
  timeout: 10_000
})