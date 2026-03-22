import { NextRequest } from "next/server";
import { djangoProxy } from "@/lib/django-proxy";

export async function POST(request: NextRequest) {
  return djangoProxy(request, "/api/v1/buyers/coupons/validate/");
}

