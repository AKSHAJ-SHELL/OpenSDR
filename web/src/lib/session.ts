/**
 * Stateless signed session for the single dashboard admin.
 *
 * The cookie value is `base64url(payload).base64url(HMAC-SHA256(payload))`,
 * signed with DASHBOARD_SESSION_SECRET. Runs only on the server (route handlers
 * and proxy.ts, both Node.js runtime) — never bundled to the browser.
 */
import { createHmac, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";

const COOKIE = "craftsman_session";
const MAX_AGE_S = 7 * 24 * 60 * 60;

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

export function makeToken(expEpochMs: number): string {
  const payload = b64url(Buffer.from(JSON.stringify({ exp: expEpochMs })));
  return `${payload}.${sign(payload)}`;
}

/** Returns true when the token is well-formed, correctly signed, and unexpired. */
export function verifyToken(token: string | undefined): boolean {
  if (!token) return false;
  const dot = token.indexOf(".");
  if (dot <= 0) return false;
  const payloadB64 = token.slice(0, dot);
  const sigB64 = token.slice(dot + 1);

  const expected = Buffer.from(sign(payloadB64), "utf8");
  const got = Buffer.from(sigB64, "utf8");
  if (expected.length !== got.length || !timingSafeEqual(expected, got)) {
    return false;
  }
  try {
    const { exp } = JSON.parse(Buffer.from(payloadB64, "base64url").toString());
    return typeof exp === "number" && exp > Date.now();
  } catch {
    return false;
  }
}

export async function createSession(): Promise<void> {
  const expiresAt = Date.now() + MAX_AGE_S * 1000;
  const store = await cookies();
  store.set(COOKIE, makeToken(expiresAt), {
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

export const SESSION_COOKIE = COOKIE;
