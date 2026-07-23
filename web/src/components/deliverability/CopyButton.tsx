"use client";

import { useState } from "react";

/** Copy-to-clipboard for a DNS record value. The only interactive piece on an
 *  otherwise read-only page. */
export function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      className="shrink-0 rounded-md border border-line bg-bg px-2 py-1 text-[11px] font-semibold text-muted transition-colors hover:border-ink hover:text-ink"
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}
