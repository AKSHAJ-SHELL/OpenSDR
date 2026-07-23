/**
 * Session-gated reverse proxy to the Craftsman API.
 *
 * Browser-originated calls hit `/api/proxy/<path>`; this handler verifies the
 * dashboard session cookie and forwards to the API with the server-held API key.
 * The key is never exposed to the browser.
 */
import { NextRequest, NextResponse } from "next/server";
import { hasValidSession } from "@/lib/session";

const API_BASE =
  process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

async function forward(req: NextRequest, path: string[]): Promise<Response> {
  if (!(await hasValidSession())) {
    return NextResponse.json({ error: "not authenticated" }, { status: 401 });
  }

  const key = process.env.CRAFTSMAN_API_KEY;
  if (!key) {
    return NextResponse.json(
      { error: "dashboard is missing CRAFTSMAN_API_KEY" },
      { status: 500 },
    );
  }

  const target = `${API_BASE}/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers: Record<string, string> = { Authorization: `Bearer ${key}` };
  const contentType = req.headers.get("content-type");
  if (contentType) headers["content-type"] = contentType;

  const hasBody = !["GET", "HEAD"].includes(req.method);
  const upstream = await fetch(target, {
    method: req.method,
    headers,
    body: hasBody ? await req.arrayBuffer() : undefined,
    cache: "no-store",
  });

  // stream the upstream response back verbatim (status + body + content-type)
  const respHeaders = new Headers();
  const upstreamType = upstream.headers.get("content-type");
  if (upstreamType) respHeaders.set("content-type", upstreamType);
  return new NextResponse(upstream.body, {
    status: upstream.status,
    headers: respHeaders,
  });
}

type Ctx = { params: Promise<{ path: string[] }> };

async function handler(req: NextRequest, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params;
  return forward(req, path);
}

export {
  handler as GET,
  handler as POST,
  handler as PATCH,
  handler as PUT,
  handler as DELETE,
};
