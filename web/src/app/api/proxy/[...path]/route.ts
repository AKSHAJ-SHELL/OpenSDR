/**
 * Session-gated reverse proxy to the Craftsman API.
 *
 * Browser-originated calls hit `/api/proxy/<path>`; this handler verifies the
 * dashboard session cookie and forwards to the API with the server-held API key.
 * The key is never exposed to the browser.
 *
 * RBAC (M5.1b): the session carries a role (owner|operator|viewer). The API's
 * route→scope map is fetched once and cached for 5 minutes; requests whose
 * required scope exceeds the role's ceiling are refused here without ever
 * reaching the API. Unknown routes forward as before — the API is the backstop.
 */
import { NextRequest, NextResponse } from "next/server";
import { getSession, type SessionRole } from "@/lib/session";

const API_BASE =
  process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

// ---------------------------------------------------------------- route scopes

const SCOPE_RANK: Record<string, number> = { read: 1, operate: 2, admin: 3 };
const ROLE_MAX: Record<SessionRole, number> = { viewer: 1, operator: 2, owner: 3 };
const SCOPES_TTL_MS = 5 * 60 * 1000;

type ScopeRule = { method: string; pattern: RegExp; scope: string };

let scopeRules: ScopeRule[] | null = null;
let scopesFetchedAt = 0;

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** `"GET /leads/{lead_id}"` → method + regex matching `/leads/<anything>`. */
function compileRule(route: string, scope: string): ScopeRule | null {
  const space = route.indexOf(" ");
  if (space <= 0) return null;
  const method = route.slice(0, space).toUpperCase();
  const path = route.slice(space + 1);
  const pattern = new RegExp(
    "^" +
      path
        .split("/")
        .map((seg) =>
          seg.startsWith("{") && seg.endsWith("}") ? "[^/]+" : escapeRegExp(seg),
        )
        .join("/") +
      "$",
  );
  return { method, pattern, scope };
}

/** Cached fetch of the API's route→scope map. Returns stale (or null) on failure. */
async function getScopeRules(key: string): Promise<ScopeRule[] | null> {
  const now = Date.now();
  if (scopeRules && now - scopesFetchedAt < SCOPES_TTL_MS) return scopeRules;
  try {
    const res = await fetch(`${API_BASE}/auth/route-scopes`, {
      cache: "no-store",
      headers: { Authorization: `Bearer ${key}`, Accept: "application/json" },
    });
    if (!res.ok) return scopeRules;
    const map = (await res.json()) as Record<string, string>;
    const rules: ScopeRule[] = [];
    for (const [route, scope] of Object.entries(map)) {
      const rule = compileRule(route, scope);
      if (rule) rules.push(rule);
    }
    scopeRules = rules;
    scopesFetchedAt = now;
    return rules;
  } catch {
    return scopeRules;
  }
}

/** True when the role's ceiling is below what the target route requires. */
function exceedsRole(
  rules: ScopeRule[] | null,
  method: string,
  path: string,
  role: SessionRole,
): boolean {
  if (!rules) return false; // map unavailable — forward; the API is the backstop
  const rule = rules.find((r) => r.method === method && r.pattern.test(path));
  if (!rule) return false; // unauthenticated/unknown route — forward
  const required = SCOPE_RANK[rule.scope] ?? SCOPE_RANK.admin; // unknown scope → strictest
  return required > ROLE_MAX[role];
}

// ---------------------------------------------------------------- forwarding

async function forward(req: NextRequest, path: string[]): Promise<Response> {
  const session = await getSession();
  if (!session) {
    return NextResponse.json({ error: "not authenticated" }, { status: 401 });
  }

  const key = process.env.CRAFTSMAN_API_KEY;
  if (!key) {
    return NextResponse.json(
      { error: "dashboard is missing CRAFTSMAN_API_KEY" },
      { status: 500 },
    );
  }

  const rules = await getScopeRules(key);
  if (exceedsRole(rules, req.method.toUpperCase(), `/${path.join("/")}`, session.role)) {
    return NextResponse.json(
      { error: "your role does not permit this action" },
      { status: 403 },
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
