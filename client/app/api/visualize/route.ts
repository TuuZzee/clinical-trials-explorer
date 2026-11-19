export const runtime = "nodejs";

export const dynamic = "force-dynamic";

const error = (code: string, message: string, status: number) =>
  Response.json({ error: { code, message } }, { status, headers: { "Cache-Control": "no-store" } });

export async function POST(request: Request) {
  let payload: unknown;

  try {
    payload = await request.json();
  } catch {
    return error("invalid_request", "Send a valid JSON request.", 400);
  }

  const deadline = AbortSignal.timeout(150_000); // Backend default budget: 120 seconds.

  try {
    const base = process.env.BACKEND_URL || "http://127.0.0.1:8000";
    const response = await fetch(`${base.replace(/\/$/, "")}/api/v1/visualize`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
      cache: "no-store",
      signal: AbortSignal.any([request.signal, deadline]),
    });

    return new Response(await response.arrayBuffer(), {
      status: response.status,
      headers: {
        "Content-Type": response.headers.get("Content-Type") || "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return deadline.aborted
      ? error(
          "proxy_timeout",
          "The backend took too long to respond. Narrow the filters and retry.",
          504,
        )
      : error(
          "backend_unavailable",
          "Cannot reach the backend. Check that FastAPI is running and retry.",
          502,
        );
  }
}
