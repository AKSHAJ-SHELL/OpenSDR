import { LoginForm } from "@/components/login/LoginForm";

export const dynamic = "force-dynamic";

const API_BASE =
  process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

/** The SSO link is followed by the browser, so prefer the public base URL. */
const PUBLIC_API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? process.env.API_URL ?? "http://127.0.0.1:8000";

/** Human-friendly (and deliberately vague) messages for ?error= SSO reasons. */
const SSO_ERRORS: Record<string, string> = {
  sso_denied: "SSO sign-in was cancelled. Try again, or sign in with a password.",
  sso_state_invalid: "That SSO sign-in expired or was invalid. Please try again.",
  sso_exchange_failed: "SSO sign-in didn't complete. Please try again.",
  sso_unknown_user: "SSO sign-in didn't complete. Ask an owner to add your account, then try again.",
  sso_user_disabled: "This account can't sign in. Contact an owner.",
};

async function ssoEnabled(): Promise<boolean> {
  try {
    const key = process.env.CRAFTSMAN_API_KEY;
    const res = await fetch(`${API_BASE}/auth/sso/status`, {
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...(key ? { Authorization: `Bearer ${key}` } : {}),
      },
    });
    if (!res.ok) return false;
    const body = await res.json();
    return body?.enabled === true;
  } catch {
    return false;
  }
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  const sso = await ssoEnabled();
  const ssoError = error ? (SSO_ERRORS[error] ?? "Sign-in didn't complete. Please try again.") : null;

  return (
    <LoginForm
      ssoHref={sso ? `${PUBLIC_API_BASE}/auth/oidc/login` : null}
      initialError={ssoError}
    />
  );
}
