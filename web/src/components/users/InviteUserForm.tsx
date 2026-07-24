"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { api } from "@/lib/api";
import type { Role } from "@/lib/types";

const inputCls =
  "w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-ink";
const labelCls =
  "block text-[11px] font-semibold uppercase tracking-[0.08em] text-faint";

/** Invite a user: email + role, optional display name and initial password. */
export function InviteUserForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const passwordTooShort = password.length > 0 && password.length < 12;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (passwordTooShort) return;
    setError(null);
    startTransition(async () => {
      try {
        await api.createUser({
          email: email.trim(),
          role,
          ...(displayName.trim() ? { display_name: displayName.trim() } : {}),
          ...(password ? { password } : {}),
        });
        setEmail("");
        setDisplayName("");
        setRole("viewer");
        setPassword("");
        router.refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    });
  }

  return (
    <form
      onSubmit={submit}
      className="rounded-[var(--radius)] border border-line bg-surface p-5 shadow-[var(--shadow)]"
    >
      <div className="text-sm font-semibold text-ink">Invite a user</div>
      <p className="mt-1 text-xs text-muted">
        With SSO enabled they can sign in immediately; a password is optional (12+ characters).
      </p>

      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <label htmlFor="invite-email" className={labelCls}>
            Email
          </label>
          <input
            id="invite-email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={`mt-2 ${inputCls}`}
          />
        </div>
        <div>
          <label htmlFor="invite-name" className={labelCls}>
            Display name
          </label>
          <input
            id="invite-name"
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Optional"
            className={`mt-2 ${inputCls}`}
          />
        </div>
        <div>
          <label htmlFor="invite-role" className={labelCls}>
            Role
          </label>
          <select
            id="invite-role"
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
            className={`mt-2 ${inputCls}`}
          >
            <option value="viewer">viewer</option>
            <option value="operator">operator</option>
            <option value="owner">owner</option>
          </select>
        </div>
        <div>
          <label htmlFor="invite-password" className={labelCls}>
            Initial password
          </label>
          <input
            id="invite-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Optional, 12+ chars"
            className={`mt-2 ${inputCls}`}
          />
        </div>
      </div>

      {passwordTooShort ? (
        <p className="mt-3 text-xs font-medium text-red-600">
          Passwords must be at least 12 characters.
        </p>
      ) : null}
      {error ? (
        <p className="mt-3 text-xs font-medium text-red-600">{error}</p>
      ) : null}

      <button
        type="submit"
        disabled={pending || !email.trim() || passwordTooShort}
        className="mt-4 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50"
      >
        {pending ? "Inviting…" : "Invite"}
      </button>
    </form>
  );
}
