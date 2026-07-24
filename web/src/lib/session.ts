/**
 * Stateless signed session for the dashboard.
 *
 * The cookie value is `base64url(payload).base64url(HMAC-SHA256(payload))`,
 * signed with DASHBOARD_SESSION_SECRET. Runs only on the server (route handlers
 * and proxy.ts, both Node.js runtime) — never bundled to the browser.
 *
 * Payload is `{exp, email?, role, userId?}` (M5.1b). Legacy tokens minted
 * before roles existed carry only `{exp}` and are treated as the break-glass
 * admin: role "owner".
 */
import { createHmac, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";

const COOKIE = "craftsman_session";
const MAX_AGE_S = 7 * 24 * 60 * 60;

export type SessionRole = "owner" | "operator" | "viewer";

export type SessionUser = {
  email?: string;
  role: SessionRole;
  userId?: string;
};

const ROLES: readonly string[] = ["owner", "operator", "viewer"];

function secret(): string {
  const s = process.env.DASHBOARD_SESSION_SECRET;
  if (!s) {
    throw new Error("DASHBOARD_SESSION_SECRET is not set — refusing to issue sessions.");
  }
  return s;
}

function b64url(buf: Buffer): string {
  return buf.toString("base64url");
}

function sign(payloadB64: string): string {
  return b64url(createHmac("sha256", secret()).update(payloadB64).digest());
}

export function makeToken(expEpochMs: number, user?: SessionUser): string {
  const body: Record<string, unknown> = { exp: expEpochMs };
  if (user) {
    body.role = user.role;
    if (user.email) body.email = user.email;
    if (user.userId) body.userId = user.userId;
  }
  const payload = b64url(Buffer.from(JSON.stringify(body)));
  return `${payload}.${sign(payload)}`;
}

/** Verify signature + expiry and return the raw payload, or null. */
function verifiedPayload(token: string | undefined): Record<string, unknown> | null {
  if (!token) return null;
  const dot = token.indexOf(".");
  if (dot <= 0) return null;
  const payloadB64 = token.slice(0, dot);
  const sigB64 = token.slice(dot + 1);

  const expected = Buffer.from(sign(payloadB64), "utf8");
  const got = Buffer.from(sigB64, "utf8");
  if (expected.length !== got.length || !timingSafeEqual(expected, got)) {
    return null;
  }
  try {
    const payload = JSON.parse(Buffer.from(payloadB64, "base64url").toString());
    if (typeof payload?.exp !== "number" || payload.exp <= Date.now()) return null;
    return payload;
  } catch {
    return null;
  }
}

/** Returns true when the token is well-formed, correctly signed, and unexpired. */
export function verifyToken(token: string | undefined): boolean {
  return verifiedPayload(token) !== null;
}

/** Verified session user from a raw token. Legacy `{exp}`-only tokens → owner. */
export function parseToken(token: string | undefined): SessionUser | null {
  const payload = verifiedPayload(token);
  if (!payload) return null;
  const role: SessionRole =
    typeof payload.role === "string" && ROLES.includes(payload.role)
      ? (payload.role as SessionRole)
      : "owner"; // legacy password-hash login (break-glass admin)
  return {
    role,
    email: typeof payload.email === "string" ? payload.email : undefined,
    userId: typeof payload.userId === "string" ? payload.userId : undefined,
  };
}

export async function createSession(user?: SessionUser): Promise<void> {
  const expiresAt = Date.now() + MAX_AGE_S * 1000;
  const store = await cookies();
  store.set(COOKIE, makeToken(expiresAt, user ?? { role: "owner" }), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: MAX_AGE_S,
  });
}

export async function destroySession(): Promise<void> {
  const store = await cookies();
  store.delete(COOKIE);
}

/** Read + verify the session cookie in a route handler / server component. */
export async function hasValidSession(): Promise<boolean> {
  const store = await cookies();
  return verifyToken(store.get(COOKIE)?.value);
}

/** Verified session payload (email/role/userId), or null when signed out. */
export async function getSession(): Promise<SessionUser | null> {
  const store = await cookies();
  return parseToken(store.get(COOKIE)?.value);
}

export const SESSION_COOKIE = COOKIE;
