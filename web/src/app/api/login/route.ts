/**
 * Dashboard login (M5.1b).
 *
 * Two paths, one uniform failure mode:
 *  - email + password  → verified server-side against the API's user store;
 *    the session carries {email, role, userId}.
 *  - password only     → legacy break-glass admin against DASHBOARD_PASSWORD_HASH;
 *    the session role is "owner".
 * Every failure returns the same 401 — no hint which path (or field) failed.
 */
import { NextResponse } from "next/server";
import { verifyPassword } from "@/lib/password";
import { createSession, type SessionRole } from "@/lib/session";

const API_BASE =
  process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

const ROLES: readonly string[] = ["owner", "operator", "viewer"];

function invalid(): NextResponse {
  return NextResponse.json({ error: "invalid credentials" }, { status: 401 });
}

export async function POST(req: Request) {
  let email = "";
  let password = "";
  try {
    const body = await req.json();
    email = typeof body?.email === "string" ? body.email.trim() : "";
    password = typeof body?.password === "string" ? body.password : "";
  } catch {
    return NextResponse.json({ error: "invalid body" }, { status: 400 });
  }

  if (!password) return invalid();

  if (email) {
    // org user: verify against the API's user store, server-side.
    const key = process.env.CRAFTSMAN_API_KEY;
    if (!key) return invalid();
    try {
      const res = await fetch(`${API_BASE}/auth/verify-credentials`, {
        method: "POST",
        cache: "no-store",
        headers: {
          Authorization: `Bearer ${key}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) return invalid();
      const user = await res.json();
      if (typeof user?.role !== "string" || !ROLES.includes(user.role)) return invalid();
      await createSession({
        email: typeof user.email === "string" ? user.email : email,
        role: user.role as SessionRole,
        userId: typeof user.user_id === "string" ? user.user_id : undefined,
      });
      return NextResponse.json({ ok: true });
    } catch {
      return invalid();
    }
  }

  // break-glass admin: single shared password, only when a hash is configured.
  if (!process.env.DASHBOARD_PASSWORD_HASH) return invalid();
  try {
    if (!verifyPassword(password)) return invalid();
  } catch {
    return invalid();
  }
  await createSession({ role: "owner" });
  return NextResponse.json({ ok: true });
}
