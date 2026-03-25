import { NextRequest, NextResponse } from "next/server";
import { ENDPOINTS } from "@/lib/api/endpoints"

export async function POST(req: NextRequest) {
  try{
    const body = await req.json()
    
    const res = await fetch(`${process.env.DJANGO_SERVER_URL}${ENDPOINTS.auth.login}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(body),
    })
    
    const data = await res.json()
    
    if(!data.ok){
      return NextResponse.json({error: data }, { status: res.status })
    }
    
    const response = NextResponse.json({ user: data.user })
    
    response.cookies.set("fg-access", data.access, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "Lax",
      path: "/"
    })
    response.cookies.set("fg-refresh", data.refresh, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "Lax",
      path: "/"
    })
    
    return response
  }
  catch(err any){
    return NextResponse.json({ error: "Internal server error."}, { status: 500 })
  }
}

