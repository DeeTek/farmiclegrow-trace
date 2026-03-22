import { NextRequest } from "next/server";
import { djangoProxy } from "@/lib/django-proxy";

type Ctx = { params: Promise<{ id: string }> };

export async function POST(r: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return djangoProxy(r, `/api/v1/buyers/notifications/${id}/read/`);
}

