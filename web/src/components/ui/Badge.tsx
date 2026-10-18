import type { ReactNode } from "react";
import { TONE_VARS } from "../../lib/states";
import type { Tone } from "../../lib/states";

interface BadgeProps {
  tone?: Tone;
  children: ReactNode;
  /** Show a leading status dot. The text always carries the meaning, never colour alone. */
  dot?: boolean;
  title?: string;
}

export function Badge({ tone = "neutral", children, dot = true, title }: BadgeProps) {
  const vars = TONE_VARS[tone];
  return (
    <span
      title={title}
      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium"
      style={{ background: vars.bg, color: vars.fg }}
    >
      {dot && (
        <span
          aria-hidden="true"
          className="size-1.5 rounded-full"
          style={{ background: vars.dot }}
        />
      )}
      {children}
    </span>
  );
}
