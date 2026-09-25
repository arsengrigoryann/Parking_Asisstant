import { NextRequest, NextResponse } from "next/server";

const backend = process.env.PARKING_API_URL ?? "http://127.0.0.1:8000";
const uuidPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type RouteContext = { params: Promise<{ reservationId: string }> };

function adminHeaders(): HeadersInit | null {
  const token = process.env.ADMIN_API_TOKEN;
  return token
    ? { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }
    : null;
}

export async function GET(
  request: NextRequest,
  context: RouteContext,
): Promise<NextResponse> {
  const reservationId = await validId(context);
  if (!reservationId) return invalidId();
  const review = request.nextUrl.searchParams.get("view") === "review";
  let headers: HeadersInit = {};
  if (review) {
    const authenticated = adminHeaders();
    if (!authenticated) return missingAdminConfiguration();
    headers = authenticated;
  }
  const path = review
    ? `/admin/reservations/${reservationId}/review`
    : `/api/workflows/${reservationId}/status`;
  return relay(
    await fetch(`${backend}${path}`, { headers, cache: "no-store" }),
  );
}

export async function POST(
  request: NextRequest,
  context: RouteContext,
): Promise<NextResponse> {
  const reservationId = await validId(context);
  if (!reservationId) return invalidId();
  const headers = adminHeaders();
  if (!headers) return missingAdminConfiguration();
  const payload = (await request.json()) as {
    action?: string;
    reason?: string;
  };
  if (
    !payload.action ||
    !["approve", "reject", "retry"].includes(payload.action)
  ) {
    return NextResponse.json(
      { detail: "Unsupported action." },
      { status: 422 },
    );
  }
  const path =
    payload.action === "retry"
      ? `/admin/workflows/${reservationId}/retry-recording`
      : `/admin/reservations/${reservationId}/${payload.action}`;
  const decision = await fetch(`${backend}${path}`, {
    method: "POST",
    headers,
    body:
      payload.action === "reject"
        ? JSON.stringify({ reason: payload.reason || null })
        : undefined,
    cache: "no-store",
  });
  if (!decision.ok) return relay(decision);
  const authoritative = await decision.json();
  const workflow = await fetch(
    `${backend}/api/workflows/${reservationId}/status`,
    { cache: "no-store" },
  );
  if (!workflow.ok) return relay(workflow);
  return NextResponse.json({
    reservation: authoritative,
    workflow: await workflow.json(),
  });
}

async function validId(context: RouteContext): Promise<string | null> {
  const { reservationId } = await context.params;
  return uuidPattern.test(reservationId) ? reservationId : null;
}

function invalidId(): NextResponse {
  return NextResponse.json(
    { detail: "Invalid reservation identifier." },
    { status: 422 },
  );
}

function missingAdminConfiguration(): NextResponse {
  return NextResponse.json(
    { detail: "Server-side administrator authentication is not configured." },
    { status: 503 },
  );
}

async function relay(response: Response): Promise<NextResponse> {
  const body: unknown = await response.json().catch(() => ({
    detail: "Parking service returned an invalid response.",
  }));
  return NextResponse.json(body, { status: response.status });
}
