import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { streamEvents } from "./client";

export type LiveStatus = "connecting" | "live" | "reconnecting";

/** Maps a server event topic to the query families it makes stale. */
export function keysForTopic(topic: string, data: unknown): string[][] {
  const id = typeof data === "object" && data !== null && "id" in data ? String(data.id) : null;
  switch (topic) {
    case "device.updated":
    case "device.health":
      return [["devices"], ["fleet"], ...(id ? [["device", id]] : [])];
    case "host.updated":
      return [["hosts"], ["fleet"]];
    case "job.updated":
      return [["jobs"], ["fleet"], ...(id ? [["job", id]] : [])];
    case "execution.updated":
      return [["executions"], ["jobs"], ...(id ? [["execution", id]] : [])];
    case "execution.artifact":
      return [["execution"]];
    case "alert.updated":
      return [["alerts"]];
    default:
      return [];
  }
}

/** One long-lived event stream for the whole app. Invalidations are coalesced so a burst of
 *  events causes one refetch per query family, not one per event. */
export function useLiveEvents(enabled: boolean): LiveStatus {
  const client = useQueryClient();
  const [status, setStatus] = useState<LiveStatus>("connecting");

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    const isAborted = () => controller.signal.aborted;
    const pending = new Map<string, string[]>();
    const scheduled: { timer?: ReturnType<typeof setTimeout> } = {};

    const flush = () => {
      scheduled.timer = undefined;
      for (const key of pending.values()) void client.invalidateQueries({ queryKey: key });
      pending.clear();
    };

    void (async () => {
      let delay = 1000;
      while (!isAborted()) {
        try {
          setStatus((s) => (s === "live" ? s : "connecting"));
          await streamEvents(
            "/v1/events",
            controller.signal,
            (event) => {
              setStatus("live");
              delay = 1000;
              for (const key of keysForTopic(event.event, event.data))
                pending.set(key.join("/"), key);
              scheduled.timer ??= setTimeout(flush, 250);
            },
            () => {
              setStatus("live");
              delay = 1000;
            },
          );
        } catch {
          if (isAborted()) return;
        }
        setStatus("reconnecting");
        await new Promise((resolve) => setTimeout(resolve, delay));
        delay = Math.min(delay * 2, 15_000);
      }
    })();

    return () => {
      controller.abort();
      if (scheduled.timer) clearTimeout(scheduled.timer);
    };
  }, [client, enabled]);

  return status;
}
