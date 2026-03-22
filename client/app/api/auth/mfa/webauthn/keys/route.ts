import { NextRequest } from "next/server";
import { djangoProxy } from "@/lib/django-proxy";

export async function GET(request: NextRequest) {
  return djangoProxy(request, "/account/v1/mfa/webauthn/keys/");
}

export async function DELETE(request: NextRequest) {
  return djangoProxy(request, "/account/v1/mfa/webauthn/keys/");
}

