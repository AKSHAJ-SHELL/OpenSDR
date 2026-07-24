/**
 * Route gate (Next.js 16 renamed Middleware → Proxy; runs on the Node.js runtime).
 *
 * Optimistic check only: verify the session cookie's signature + expiry and
 * redirect. Real authorization happens at the data layer — every API call is
 * gated by the API key in /api/proxy, and the API itself requires a scoped key.
 */
import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, verifyToken } from "@/lib/session";

// `/auth/sso` is the SSO landing: the browser arrives here *without* a session
// (the route handler mints one), so the gate must let it through.
const PUBLIC_PATHS = new Set(["/login", "/auth/sso"]);

export default function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const authed = verifyToken(req.cookies.get(SESSION_COOKIE)?.value);

  if (PUBLIC_PATHS.has(pathname)) {
    // already signed in → send to the dashboard
    if (authed) return NextResponse.redirect(new URL("/", req.nextUrl));
    return NextResponse.next();
  }

  if (!authed) {
    const login = new URL("/login", req.nextUrl);
    return NextResponse.redirect(login);
  }
  return NextResponse.next();
}

export const config = {
  // run on everything except API routes, Next internals, and static assets
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:png|svg|ico)$).*)"],
};
