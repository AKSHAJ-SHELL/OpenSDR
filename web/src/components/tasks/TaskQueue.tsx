"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { api } from "@/lib/api";
import type { Task } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";

const CALL_OUTCOMES = ["connected", "voicemail", "no_answer"] as const;

const primaryBtn =
  "rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50";
const ghostBtn =
  "rounded-lg border border-line bg-bg px-3 py-1.5 text-xs font-semibold text-muted transition-colors hover:border-ink hover:text-ink disabled:opacity-50";

function channelLabel(channel: string): string {
  if (channel === "linkedin_task") return "LinkedIn";
  if (channel === "call_task") return "Call";
  return channel;
}

function dueLabel(task: Task): string {
  const due = new Date(task.due_at);
  return `due ${due.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      className={ghostBtn}
    >
      {copied ? "Copied" : "Copy message"}
    </button>
  );
}

function LinkedInBody({ task }: { task: Task }) {
  const message = task.payload.message ?? "";
  return (
    <div className="mt-3 rounded-lg border border-line bg-bg p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
          Validated note · {task.payload.char_count ?? message.length} chars
        </span>
        <div className="flex items-center gap-2">
          <CopyButton text={message} />
          {task.linkedin_url ? (
            <a
              href={task.linkedin_url}
              target="_blank"
              rel="noreferrer noopener"
              className={ghostBtn}
            >
              Open profile ↗
            </a>
          ) : (
            <span className="text-[11px] text-faint">no LinkedIn URL on lead</span>
          )}
        </div>
      </div>
      <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-ink">{message}</p>
    </div>
  );
}

function DialButton({ task }: { task: Task }) {
  const [state, setState] = useState<"idle" | "dialing" | "ringing" | "error">("idle");
  const [message, setMessage] = useState<string | null>(null);
  return (
    <span className="flex items-center gap-2">
      <button
        type="button"
        disabled={state === "dialing"}
        onClick={async () => {
          setState("dialing");
          setMessage(null);
          try {
            const res = await api.dialTask(task.id);
            setState("ringing");
            setMessage(`Ringing your phone (${res.to_operator})…`);
          } catch (e) {
            setState("error");
            setMessage(e instanceof Error ? e.message : String(e));
          }
        }}
        className={ghostBtn}
      >
        {state === "dialing" ? "Dialing…" : "Click to dial"}
      </button>
      {message ? (
        <span className={`text-[11px] ${state === "error" ? "text-red-600" : "text-muted"}`}>
          {message}
        </span>
      ) : null}
    </span>
  );
}

function CallBody({ task }: { task: Task }) {
  const brief = task.payload.brief;
  if (!brief) return null;
  return (
    <div className="mt-3 rounded-lg border border-line bg-bg p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
          Call brief · grounded, not a script
        </span>
        {task.phone ? (
          <span className="flex items-center gap-2">
            <a href={`tel:${task.phone}`} className={ghostBtn}>
              Call {task.phone}
            </a>
            {task.dialer_available ? <DialButton task={task} /> : null}
          </span>
        ) : (
          <span className="text-[11px] text-faint">no phone on lead</span>
        )}
      </div>
      <dl className="mt-2 grid gap-2 text-sm leading-relaxed text-ink">
        <div>
          <dt className="text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">Opener</dt>
          <dd>{brief.opener}</dd>
        </div>
        <div>
          <dt className="text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
            Pain hypotheses
          </dt>
          <dd>
            <ul className="list-disc pl-4">
              {brief.pain_hypotheses.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          </dd>
        </div>
        <div>
          <dt className="text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
            If they push back
          </dt>
          <dd>{brief.objection_notes}</dd>
        </div>
      </dl>
    </div>
  );
}

function TaskCard({
  task,
  onResolved,
}: {
  task: Task;
  onResolved: (id: string) => void;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<string>(
    task.channel === "call_task" ? "connected" : "sent",
  );

  function act(fn: () => Promise<unknown>) {
    setError(null);
    startTransition(async () => {
      try {
        await fn();
        onResolved(task.id);
        router.refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    });
  }

  return (
    <div className="rounded-[var(--radius)] border border-line bg-surface p-5 shadow-[var(--shadow)]">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-faint">
        <Badge tone={task.channel === "call_task" ? "accent" : "info"}>
          {channelLabel(task.channel)}
        </Badge>
        <span className="text-sm font-semibold text-ink">
          {task.lead_name || task.lead_email || "Unknown lead"}
        </span>
        {task.lead_title ? <span>{task.lead_title}</span> : null}
        {task.company_name || task.company_domain ? (
          <span>· {task.company_name || task.company_domain}</span>
        ) : null}
        {task.campaign_name ? <span>· {task.campaign_name}</span> : null}
        <span className="ml-auto flex items-center gap-2">
          {task.overdue ? <Badge tone="warn">overdue</Badge> : null}
          <span>{dueLabel(task)}</span>
        </span>
      </div>

      {task.brief_highlights.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {task.brief_highlights.map((h) => (
            <span
              key={h}
              className="rounded-full border border-dashed border-line bg-bg px-2 py-0.5 text-[11px] text-muted"
            >
              {h}
            </span>
          ))}
        </div>
      ) : null}

      {task.channel === "linkedin_task" ? <LinkedInBody task={task} /> : <CallBody task={task} />}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {task.channel === "call_task" ? (
          <label className="flex items-center gap-2 text-xs text-muted">
            Outcome
            <select
              value={outcome}
              onChange={(e) => setOutcome(e.target.value)}
              className="rounded-lg border border-line bg-bg px-2 py-1.5 text-xs text-ink outline-none focus:border-ink"
            >
              {CALL_OUTCOMES.map((o) => (
                <option key={o} value={o}>
                  {o.replace("_", " ")}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <button
          type="button"
          disabled={pending}
          onClick={() => act(() => api.completeTask(task.id, outcome))}
          className={primaryBtn}
        >
          {pending ? "Saving…" : "Done — advance sequence"}
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={() => act(() => api.skipTask(task.id))}
          className={ghostBtn}
        >
          Skip this touch
        </button>
      </div>
      {error ? <p className="mt-2 text-xs font-medium text-red-600">{error}</p> : null}
    </div>
  );
}

export function TaskQueue({ initial }: { initial: Task[] }) {
  const [resolved, setResolved] = useState<Set<string>>(new Set());
  const open = initial.filter((t) => !resolved.has(t.id));
  const linkedin = open.filter((t) => t.channel === "linkedin_task");
  const calls = open.filter((t) => t.channel === "call_task");

  if (open.length === 0) {
    return (
      <EmptyState
        title="No open tasks"
        body="When a campaign reaches a LinkedIn or call step, the validated message or call brief lands here for you to act on. Email steps keep running on their own."
      />
    );
  }

  const markResolved = (id: string) =>
    setResolved((prev) => new Set([...prev, id]));

  return (
    <div className="grid gap-6">
      {linkedin.length > 0 ? (
        <section>
          <h2 className="mb-3 font-[family-name:var(--font-display)] text-lg text-ink">
            LinkedIn · {linkedin.length}
          </h2>
          <div className="grid gap-4">
            {linkedin.map((t) => (
              <TaskCard key={t.id} task={t} onResolved={markResolved} />
            ))}
          </div>
        </section>
      ) : null}
      {calls.length > 0 ? (
        <section>
          <h2 className="mb-3 font-[family-name:var(--font-display)] text-lg text-ink">
            Calls · {calls.length}
          </h2>
          <div className="grid gap-4">
            {calls.map((t) => (
              <TaskCard key={t.id} task={t} onResolved={markResolved} />
            ))}
          </div>
        </section>
      ) : null}
      <p className="text-[11px] leading-relaxed text-faint">
        Why is this manual? Automated LinkedIn outreach violates LinkedIn&apos;s terms and
        gets accounts restricted. Craftsman will never automate it — no browser bots, no
        session cookies, ever. It writes the note, validates every claim against the
        research brief, and you decide whether it goes out. Calls work the same way.
      </p>
    </div>
  );
}
