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
      if (action === "activate") await api.activate(id);
      else await api.pause(id);
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
