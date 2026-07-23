"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTransition } from "react";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/leads", label: "Leads" },
  { href: "/find-leads", label: "Find leads" },
  { href: "/inbox", label: "Inbox" },
  { href: "/tasks", label: "Tasks" },
  { href: "/review", label: "Review" },
  { href: "/campaigns", label: "Campaigns" },
  { href: "/deliverability", label: "Deliverability" },
  { href: "/analytics", label: "Analytics" },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [loggingOut, startLogout] = useTransition();

  function logout() {
    startLogout(async () => {
      await fetch("/api/logout", { method: "POST" });
      router.replace("/login");
      router.refresh();
    });
  }

  return (
    <aside className="flex h-screen w-[240px] shrink-0 flex-col bg-sidebar text-sidebar-ink">
      <div className="border-b border-white/10 px-5 py-6">
        <Link href="/" className="block">
          <div className="font-[family-name:var(--font-display)] text-[1.65rem] font-medium tracking-tight">
            Craftsman
          </div>
          <div className="mt-1 text-[11px] font-medium uppercase tracking-[0.14em] text-sidebar-muted">
            AI outbound agent
          </div>
        </Link>
      </div>

      <nav className="flex flex-1 flex-col gap-1 px-3 py-4">
        {NAV.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`rounded-lg px-3 py-2.5 text-sm font-medium transition-colors duration-150 ${
                active
                  ? "bg-accent text-accent-ink"
                  : "text-sidebar-muted hover:bg-sidebar-hover hover:text-sidebar-ink"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-white/10 px-5 py-4">
        <div className="flex items-center gap-2 text-xs text-sidebar-muted">
          <span className="live-dot inline-block h-1.5 w-1.5 rounded-full bg-accent" />
          Agent online
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-sidebar-muted/80">
          Learning loop active · Thompson sampling
        </p>
        <button
          type="button"
          onClick={logout}
          disabled={loggingOut}
          className="mt-3 text-[11px] font-medium text-sidebar-muted underline-offset-2 transition-colors hover:text-sidebar-ink hover:underline disabled:opacity-50"
        >
          {loggingOut ? "Signing out…" : "Sign out"}
        </button>
      </div>
    </aside>
  );
}
