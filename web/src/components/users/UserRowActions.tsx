"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { api } from "@/lib/api";
import type { Role, UserOut } from "@/lib/types";

const btn =
  "rounded-lg border border-line bg-bg px-2.5 py-1 text-[11px] font-semibold text-muted transition-colors hover:border-ink hover:text-ink disabled:opacity-50";

/** The API refuses to demote/disable the last active owner with a 409. */
function friendly(message: string): string {
  if (message.startsWith("409")) {
    return "This is the last active owner — the API refused, so you can't be locked out. Promote another owner first.";
  }
  if (message.startsWith("403")) {
    return "Your role does not permit this action.";
  }
  return message;
}

/** Per-user operations: change role, disable/enable, reset password. */
export function UserRowActions({ user }: { user: UserOut }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const [newPassword, setNewPassword] = useState("");

  function update(body: { role?: Role; disabled?: boolean; password?: string }) {
    setError(null);
    startTransition(async () => {
      try {
        await api.updateUser(user.id, body);
        setResetting(false);
        setNewPassword("");
        router.refresh();
      } catch (e) {
        setError(friendly(e instanceof Error ? e.message : String(e)));
      }
    });
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-1.5">
        <select
          aria-label={`Role for ${user.email}`}
          value={user.role}
          disabled={pending}
          onChange={(e) => update({ role: e.target.value as Role })}
          className="rounded-lg border border-line bg-bg px-2 py-1 text-[11px] font-semibold text-muted outline-none transition-colors hover:border-ink hover:text-ink focus:border-ink disabled:opacity-50"
        >
          <option value="viewer">viewer</option>
          <option value="operator">operator</option>
          <option value="owner">owner</option>
        </select>
        <button
          type="button"
          disabled={pending}
          onClick={() => update({ disabled: !user.disabled_at })}
          className={btn}
        >
          {user.disabled_at ? "Enable" : "Disable"}
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={() => {
            setResetting(!resetting);
            setNewPassword("");
            setError(null);
          }}
          className={btn}
        >
          Reset password
        </button>
      </div>

      {resetting ? (
        <div className="mt-1 w-72 rounded-lg border border-line bg-surface p-3 text-left">
          <p className="text-[11px] leading-relaxed text-muted">
            Set a new password for <span className="font-mono text-ink">{user.email}</span>{" "}
            (at least 12 characters). It takes effect immediately.
          </p>
          <input
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            className="mt-2 w-full rounded border border-line bg-bg px-2 py-1 text-xs text-ink outline-none focus:border-ink"
          />
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              disabled={pending || newPassword.length < 12}
              onClick={() => update({ password: newPassword })}
              className="rounded-lg bg-accent px-2.5 py-1 text-[11px] font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {pending ? "Saving…" : "Set password"}
            </button>
            <button
              type="button"
              onClick={() => {
                setResetting(false);
                setNewPassword("");
              }}
              className={btn}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {error ? (
        <p className="max-w-xs text-right text-[11px] leading-relaxed text-red-600">{error}</p>
      ) : null}
    </div>
  );
}
