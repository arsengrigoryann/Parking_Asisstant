import { NextRequest, NextResponse } from "next/server";

const backend = process.env.PARKING_API_URL ?? "http://127.0.0.1:8000";

export async function POST(request: NextRequest): Promise<NextResponse> {
  const payload: unknown = await request.json();
  const response = await fetch(`${backend}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  return relay(response);
}

async function relay(response: Response): Promise<NextResponse> {
  const body: unknown = await response.json().catch(() => ({
    detail: "Parking service returned an invalid response.",
  }));
  return NextResponse.json(body, { status: response.status });
}
