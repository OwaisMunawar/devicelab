import type { ReactNode } from "react";
import { TONE_VARS } from "../../lib/states";
import type { Tone } from "../../lib/states";

export interface TimelineItem {
  id: string;
  title: ReactNode;
  tone: Tone;
  meta?: ReactNode;
  detail?: ReactNode;
}

interface TimelineProps {
  items: TimelineItem[];
  label: string;
}

export function Timeline({ items, label }: TimelineProps) {
  return (
    <ol aria-label={label} className="relative space-y-4 pl-6">
      <span aria-hidden="true" className="absolute bottom-1 left-[9px] top-1 w-px bg-line" />
      {items.map((item) => (
        <li key={item.id} className="relative">
          <span
            aria-hidden="true"
            className="absolute -left-6 top-1 grid size-[19px] place-items-center rounded-full border-2 border-surface"
            style={{ background: TONE_VARS[item.tone].dot }}
          />
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <div className="text-sm font-medium">{item.title}</div>
            {item.meta && <div className="text-xs text-muted">{item.meta}</div>}
          </div>
          {item.detail && <div className="mt-1 text-sm text-muted">{item.detail}</div>}
        </li>
      ))}
    </ol>
  );
}
