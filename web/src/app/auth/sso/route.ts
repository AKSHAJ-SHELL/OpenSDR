/**
 * SSO landing (M5.1b). The API's OIDC callback redirects the browser here with
 * a one-time ?code= (60s TTL). We exchange it server-side — admin key attached,
 * never visible to the browser — mint the session cookie, and land on the
 * dashboard. Any failure funnels back to /login with a generic error.
 */
import { NextRequest, NextResponse } from "next/server";
import { createSession, type SessionRole } from "@/lib/session";

const API_BASE =
  process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

const ROLES: readonly string[] = ["owner", "operator", "viewer"];

export async function GET(req: NextRequest) {
  const fail = () =>
    NextResponse.redirect(new URL("/login?error=sso_exchange_failed", req.nextUrl));

  const code = req.nextUrl.searchParams.get("code");
  const key = process.env.CRAFTSMAN_API_KEY;
  if (!code || !key) return fail();

  try {
    const res = await fetch(`${API_BASE}/auth/sso/exchange`, {
      method: "POST",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${key}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ code }),
    });
    if (!res.ok) return fail();
    const user = await res.json();
    if (typeof user?.role !== "string" || !ROLES.includes(user.role)) return fail();
    if (typeof user?.email !== "string") return fail();
    await createSession({
      email: user.email,
      role: user.role as SessionRole,
      userId: typeof user.user_id === "string" ? user.user_id : undefined,
    });
    return NextResponse.redirect(new URL("/", req.nextUrl));
  } catch {
    return fail();
  }
}
