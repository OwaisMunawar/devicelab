import clsx from "clsx";
import type { ReactNode } from "react";

interface StatProps {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "default" | "good" | "bad" | "warn";
}

const VALUE_TONE: Record<NonNullable<StatProps["tone"]>, string> = {
  default: "text-fg",
  good: "text-[var(--available-fg)]",
  bad: "text-[var(--quarantined-fg)]",
  warn: "text-[var(--maintenance-fg)]",
};

export function Stat({ label, value, hint, tone = "default" }: StatProps) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4 shadow-sm">
      <dl>
        <dt className="text-xs font-medium uppercase tracking-wide text-muted">{label}</dt>
        <dd
          className={clsx(
            "mt-1 text-2xl font-semibold tabular-nums tracking-tight",
            VALUE_TONE[tone],
          )}
        >
          {value}
        </dd>
      </dl>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}
