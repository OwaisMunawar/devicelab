import clsx from "clsx";
import { useId } from "react";
import type { ReactNode } from "react";

interface CardProps {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  /** Remove body padding, for tables and lists that bleed to the edge. */
  flush?: boolean;
}

export function Card({ title, description, actions, children, className, flush }: CardProps) {
  const headingId = useId();
  return (
    <section
      aria-labelledby={title ? headingId : undefined}
      className={clsx("rounded-xl border border-line bg-surface shadow-sm", className)}
    >
      {(title ?? actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
          <div className="min-w-0">
            {title && (
              <h2 id={headingId} className="truncate text-sm font-semibold tracking-tight">
                {title}
              </h2>
            )}
            {description && <p className="mt-0.5 text-xs text-muted">{description}</p>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={clsx(!flush && "p-4")}>{children}</div>
    </section>
  );
}
