import { NextRequest } from "next/server";
import { djangoProxy } from "@/lib/django-proxy";

type Ctx = { params: Promise<{ id: string }> };

export async function GET(r: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return djangoProxy(r, `/account/v1/mfa/webauthn/keys/${id}/`);
}

export async function DELETE(r: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return djangoProxy(r, `/account/v1/mfa/webauthn/keys/${id}/`);
}

