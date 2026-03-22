import { NextRequest } from "next/server";
import { djangoProxy } from "@/lib/django-proxy";

export async function PATCH(request: NextRequest) {
  return djangoProxy(request, "/api/v1/buyers/cart/update-item/");
}

