import { NextRequest } from "next/server";
import { djangoProxy } from "@/lib/django-proxy";

type Ctx = { params: Promise<{ id: string }> };

export async function GET(r: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return djangoProxy(r, `/api/v1/buyers/orders/${id}/`);
}

export async function PUT(r: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return djangoProxy(r, `/api/v1/buyers/orders/${id}/`);
}

export async function PATCH(r: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return djangoProxy(r, `/api/v1/buyers/orders/${id}/`);
}

export async function DELETE(r: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return djangoProxy(r, `/api/v1/buyers/orders/${id}/`);
}

