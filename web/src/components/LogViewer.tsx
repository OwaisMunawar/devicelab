import { useEffect, useRef, useState } from "react";
import { streamEvents } from "../api/client";
import type { LogLine } from "../api/types";
import { formatTime } from "../lib/format";

const LEVEL_CLASS: Record<string, string> = {
  error: "text-[var(--quarantined-fg)]",
  warning: "text-[var(--maintenance-fg)]",
  info: "text-fg",
  debug: "text-muted",
};

function isLogLine(value: unknown): value is LogLine {
  return typeof value === "object" && value !== null && "message" in value && "ts" in value;
}

/** Replays stored lines then follows the live stream while the execution is running. */
export function LogViewer({ executionId, live }: { executionId: string; live: boolean }) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [following, setFollowing] = useState(true);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    const seen = new Set<number>();
    void (async () => {
      try {
        await streamEvents(
          `/v1/executions/${executionId}/logs/stream`,
          controller.signal,
          (event) => {
            if (!isLogLine(event.data)) return;
            const line = event.data;
            if (line.id !== null) {
              if (seen.has(line.id)) return;
              seen.add(line.id);
            }
            setLines((prev) => [...prev, line]);
          },
        );
      } catch {
        // The stream ends when the tab closes or the server restarts; stored lines remain.
      }
    })();
    return () => controller.abort();
  }, [executionId, live]);

  useEffect(() => {
    if (following) endRef.current?.scrollIntoView({ block: "nearest" });
  }, [lines, following]);

  return (
    <div>
      <div className="flex items-center justify-between border-b border-line px-4 py-2 text-xs text-muted">
        <span>{lines.length} lines</span>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={following}
            onChange={(e) => setFollowing(e.target.checked)}
          />
          Follow
        </label>
      </div>
      <div
        role="log"
        aria-label="Execution log"
        aria-live={live ? "polite" : "off"}
        tabIndex={0}
        className="max-h-80 overflow-auto bg-surface-2 p-3 font-mono text-xs leading-relaxed"
      >
        {lines.length === 0 && <p className="text-muted">No log lines yet.</p>}
        {lines.map((line, i) => (
          <div key={line.id ?? `l-${i}`} className={LEVEL_CLASS[line.level] ?? "text-fg"}>
            <span className="mr-2 text-muted">{formatTime(line.ts).split(", ").at(-1)}</span>
            {line.message}
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}
