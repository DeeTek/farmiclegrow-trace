import { NextRequest } from "next/server";
import { djangoProxy } from "@/lib/django-proxy";

export async function GET(request: NextRequest) {
  return djangoProxy(request, "/api/v1/buyers/orders/");
}

export async function POST(request: NextRequest) {
  return djangoProxy(request, "/api/v1/buyers/orders/");
}

