import { NextRequest } from "next/server";
import { djangoProxy } from "@/lib/django-proxy";

export async function GET(request: NextRequest) {
  return djangoProxy(request, "/account/v1/social/accounts/");
}

