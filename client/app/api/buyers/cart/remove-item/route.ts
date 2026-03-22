import { NextRequest } from "next/server";
import { djangoProxy } from "@/lib/django-proxy";

export async function DELETE(request: NextRequest) {
  return djangoProxy(request, "/api/v1/buyers/cart/remove-item/");
}

