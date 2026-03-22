import { NextRequest } from "next/server";
import { djangoProxy } from "@/lib/django-proxy";

export async function GET(request: NextRequest) {
  return djangoProxy(request, "/account/v1/logout/");
}

export async function POST(request: NextRequest) {
  return djangoProxy(request, "/account/v1/logout/");
}

