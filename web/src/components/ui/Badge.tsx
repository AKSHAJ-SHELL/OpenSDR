const TONES: Record<string, string> = {
  accent: "bg-accent-soft text-accent",
  good: "bg-good-soft text-good",
  warn: "bg-warn-soft text-warn",
  info: "bg-info-soft text-info",
  muted: "bg-bg text-muted border border-line",
};

export function Badge({
  children,
  tone = "muted",
}: {
  children: React.ReactNode;
  tone?: keyof typeof TONES;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold tracking-wide uppercase ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

export function statusTone(status: string): keyof typeof TONES {
  switch (status) {
    case "active":
    case "verified":
    case "interested":
    case "healthy":
      return "good";
    case "paused":
    case "not_now":
    case "ooo":
    case "degraded":
      return "warn";
    case "draft":
    case "new":
      return "info";
    case "disqualified":
    case "unsubscribe":
    case "bounce_or_auto":
    case "paused_mailbox":
      return "muted";
    case "objection":
      return "accent";
    default:
      return "muted";
  }
}
