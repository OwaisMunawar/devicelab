import { Link, Outlet } from "@tanstack/react-router";
import { useAlerts } from "../api/hooks";
import { useLiveEvents } from "../api/live";
import type { LiveStatus } from "../api/live";
import { Badge } from "./ui";
import { ThemeToggle } from "./ThemeToggle";

const NAV = [
  { to: "/", label: "Fleet" },
  { to: "/jobs", label: "Jobs" },
  { to: "/templates", label: "Templates" },
  { to: "/schedules", label: "Schedules" },
  { to: "/alerts", label: "Alerts" },
  { to: "/admin", label: "Admin" },
] as const;

const LIVE_LABEL: Record<LiveStatus, string> = {
  connecting: "Connecting",
  live: "Live",
  reconnecting: "Reconnecting",
};

export function AppShell() {
  const status = useLiveEvents(true);
  const open = useAlerts("open");
  const openCount = open.data?.total ?? 0;

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[14rem_1fr]">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-accent focus:px-3 focus:py-2 focus:text-accent-fg"
      >
        Skip to content
      </a>
      <aside className="border-b border-line bg-surface lg:sticky lg:top-0 lg:h-screen lg:border-b-0 lg:border-r">
        <div className="flex items-center justify-between gap-3 px-4 py-3 lg:block lg:py-5">
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <span
              aria-hidden="true"
              className="grid size-7 place-items-center rounded-lg bg-accent text-accent-fg"
            >
              <svg
                viewBox="0 0 24 24"
                className="size-4"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <rect x="8" y="3" width="8" height="18" rx="2" />
              </svg>
            </span>
            DeviceLab
          </Link>
          <div className="flex items-center gap-2 lg:mt-4">
            <span role="status" aria-live="polite">
              <Badge tone={status === "live" ? "success" : "warning"}>{LIVE_LABEL[status]}</Badge>
            </span>
            <ThemeToggle />
          </div>
        </div>
        <nav
          aria-label="Primary"
          className="flex gap-1 overflow-x-auto px-3 pb-3 lg:flex-col lg:pb-0"
        >
          {NAV.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              activeOptions={{ exact: item.to === "/" }}
              className="flex items-center justify-between gap-2 whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium text-muted hover:bg-surface-2 hover:text-fg"
              activeProps={{
                className: "bg-accent-soft text-accent hover:bg-accent-soft hover:text-accent",
              }}
            >
              {item.label}
              {item.to === "/alerts" && openCount > 0 && (
                <span className="rounded-full bg-[var(--quarantined-dot)] px-1.5 text-xs font-semibold text-white">
                  {openCount}
                </span>
              )}
            </Link>
          ))}
        </nav>
      </aside>
      <main id="main" className="min-w-0 px-4 py-6 lg:px-8">
        <Outlet />
      </main>
    </div>
  );
}
