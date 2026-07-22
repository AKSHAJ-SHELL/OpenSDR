"use client";

import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { api } from "@/lib/api";

export function CampaignActions({
  id,
  status,
}: {
  id: string;
  status: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  function run(action: "activate" | "pause") {
    startTransition(async () => {
      if (action === "activate") {
        // Two-step activate (M1.2): no completed dry-run → explicit override required.
        try {
          const runs = await api.dryRuns(id);
          if (
            !runs.some((r) => r.status === "complete") &&
            !window.confirm(
              "No completed dry-run for this campaign.\n\n" +
                "Recommended: run a dry-run first — it previews the real research, copy, " +
                "and validator verdicts in Mailpit before anything is sent for real.\n\n" +
                "Activate anyway?",
            )
          ) {
            return;
          }
        } catch {
          // dry-run status unavailable — fall through to the plain activate
        }
        await api.activate(id);
      } else {
        await api.pause(id);
      }
      router.refresh();
    });
  }

  return (
    <div className="flex gap-2">
      {status !== "active" ? (
        <button
          type="button"
          disabled={pending}
          onClick={() => run("activate")}
          className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          Activate
        </button>
      ) : (
        <button
          type="button"
          disabled={pending}
          onClick={() => run("pause")}
          className="rounded-lg border border-line bg-bg px-3 py-1.5 text-xs font-semibold text-ink transition-colors hover:border-ink disabled:opacity-50"
        >
          Pause
        </button>
      )}
    </div>
  );
}
