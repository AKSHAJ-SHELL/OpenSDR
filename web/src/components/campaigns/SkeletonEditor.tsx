"use client";

import { useRef } from "react";
import { checkSkeleton, KNOWN_SLOTS, previewSkeleton } from "@/lib/skeleton";
import type { SenderPersona } from "@/lib/types";

/**
 * Skeleton textarea with slot-insert chips, authoring-time validation, and a live
 * preview rendered with sample fills — the same substitution rule the send path uses.
 */
export function SkeletonEditor({
  value,
  onChange,
  persona,
}: {
  value: string;
  onChange: (next: string) => void;
  persona: SenderPersona | null;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { errors, warnings } = checkSkeleton(value);

  function insertSlot(slot: string) {
    const el = textareaRef.current;
    const token = `{{${slot}}}`;
    if (!el) {
      onChange(value + token);
      return;
    }
    const start = el.selectionStart ?? value.length;
    const end = el.selectionEnd ?? value.length;
    onChange(value.slice(0, start) + token + value.slice(end));
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(start + token.length, start + token.length);
    });
  }

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div>
        <div className="flex flex-wrap gap-1.5">
          {KNOWN_SLOTS.map((slot) => (
            <button
              key={slot}
              type="button"
              onClick={() => insertSlot(slot)}
              className="rounded-full border border-line bg-bg px-2 py-0.5 font-mono text-[11px] text-muted transition-colors hover:border-ink hover:text-ink"
            >
              {`{{${slot}}}`}
            </button>
          ))}
        </div>
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={10}
          spellCheck={false}
          className="mt-2 w-full rounded-lg border border-line bg-bg px-3 py-2 font-mono text-xs leading-relaxed text-ink outline-none focus:border-ink"
        />
        {errors.map((msg) => (
          <p key={msg} className="mt-1 text-xs font-medium text-red-600">
            {msg}
          </p>
        ))}
        {warnings.map((msg) => (
          <p key={msg} className="mt-1 text-xs text-amber-600">
            {msg}
          </p>
        ))}
      </div>
      <div>
        <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
          Preview · sample fills
        </div>
        <pre className="mt-2 whitespace-pre-wrap rounded-lg border border-dashed border-line bg-bg px-3 py-2 text-xs leading-relaxed text-muted">
          {previewSkeleton(value, persona)}
        </pre>
      </div>
    </div>
  );
}

export function skeletonHasBlockingErrors(skeleton: string): boolean {
  return checkSkeleton(skeleton).errors.length > 0;
}
