"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

const labelCls =
  "mt-6 block text-[11px] font-semibold uppercase tracking-[0.08em] text-faint";
const inputCls =
  "mt-2 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-ink";

export function LoginForm({
  ssoHref,
  initialError,
}: {
  ssoHref: string | null;
  initialError: string | null;
}) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(initialError);
  const [pending, startTransition] = useTransition();

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    startTransition(async () => {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      if (res.ok) {
        router.replace("/");
        router.refresh();
      } else {
        setError("Incorrect email or password.");
        setPassword("");
      }
    });
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-[var(--radius)] border border-line bg-surface p-8 shadow-[var(--shadow)]"
      >
        <h1 className="font-[family-name:var(--font-display)] text-2xl text-ink">
          Craftsman
        </h1>
        <p className="mt-1 text-sm text-muted">Sign in to the dashboard.</p>

        <label htmlFor="email" className={labelCls}>
          Email
        </label>
        <input
          id="email"
          type="email"
          autoFocus
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Leave empty for admin password"
          className={inputCls}
        />

        <label htmlFor="password" className={labelCls}>
          Password
        </label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className={inputCls}
        />

        {error ? (
          <p className="mt-3 text-xs font-medium text-red-600">{error}</p>
        ) : null}

        <button
          type="submit"
          disabled={pending || !password}
          className="mt-6 w-full rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {pending ? "Signing in…" : "Sign in"}
        </button>

        {ssoHref ? (
          <a
            href={ssoHref}
            className="mt-3 block w-full rounded-lg border border-line bg-bg px-3 py-2 text-center text-sm font-semibold text-ink transition-colors hover:border-ink"
          >
            Sign in with SSO
          </a>
        ) : null}
      </form>
    </div>
  );
}
