import { useState } from "react";
import { useAcknowledgeAlert, useAlertRules, useAlerts, useToggleRule } from "../api/hooks";
import { PageHeader, QueryError } from "../components/PageHeader";
import { Badge, Button, Card, EmptyState, Table } from "../components/ui";
import type { Tone } from "../lib/states";
import { formatTime, relativeTime, titleCase } from "../lib/format";

const SEVERITY: Record<string, Tone> = { info: "info", warning: "warning", critical: "danger" };

export function AlertsPage() {
  const [tab, setTab] = useState<"open" | "resolved">("open");
  const alerts = useAlerts(tab);
  const rules = useAlertRules();
  const ack = useAcknowledgeAlert();
  const toggle = useToggleRule();

  return (
    <>
      <PageHeader
        title="Alerts"
        description="Rules are evaluated every few seconds. Repeats are counted, not re-sent, until the cooldown passes; webhooks are HMAC-signed."
      />
      <div className="grid gap-4 xl:grid-cols-[1fr_24rem]">
        <Card
          title="Alert feed"
          flush
          actions={
            <div role="tablist" aria-label="Alert state" className="flex gap-1">
              {(["open", "resolved"] as const).map((t) => (
                <button
                  key={t}
                  role="tab"
                  type="button"
                  aria-selected={tab === t}
                  onClick={() => setTab(t)}
                  className={`rounded-lg px-3 py-1 text-xs font-medium capitalize ${tab === t ? "bg-accent-soft text-accent" : "text-muted hover:bg-surface-2"}`}
                >
                  {t}
                </button>
              ))}
            </div>
          }
        >
          {alerts.isError ? (
            <QueryError error={alerts.error} />
          ) : (
            <Table
              caption={`${tab} alerts`}
              rows={alerts.data?.items ?? []}
              rowKey={(a) => a.id}
              empty={{
                title: tab === "open" ? "All quiet" : "Nothing resolved yet",
                description: "Alerts appear here when a rule fires.",
              }}
              columns={[
                {
                  key: "sev",
                  header: "Severity",
                  cell: (a) => (
                    <Badge tone={SEVERITY[a.severity] ?? "neutral"}>{titleCase(a.severity)}</Badge>
                  ),
                },
                { key: "kind", header: "Rule", cell: (a) => titleCase(a.kind) },
                { key: "msg", header: "Message", cell: (a) => a.message, className: "min-w-64" },
                { key: "n", header: "Seen", cell: (a) => `${a.occurrences}x` },
                {
                  key: "last",
                  header: "Last",
                  cell: (a) => (
                    <span title={formatTime(a.last_seen)}>{relativeTime(a.last_seen)}</span>
                  ),
                },
                {
                  key: "act",
                  header: "",
                  align: "right",
                  cell: (a) =>
                    a.state === "open" ? (
                      <Button size="sm" variant="ghost" onClick={() => ack.mutate(a.id)}>
                        Acknowledge
                      </Button>
                    ) : (
                      <span className="text-xs text-muted">
                        resolved {relativeTime(a.resolved_at)}
                      </span>
                    ),
                },
              ]}
            />
          )}
        </Card>
        <Card title="Rules" flush>
          {(rules.data ?? []).length === 0 ? (
            <EmptyState title="No rules" />
          ) : (
            <ul>
              {(rules.data ?? []).map((r) => (
                <li
                  key={r.id}
                  className="flex items-start justify-between gap-3 border-b border-line px-4 py-3 last:border-0"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{r.name}</p>
                    <p className="mt-0.5 break-all font-mono text-xs text-muted">
                      {JSON.stringify(r.params)}
                    </p>
                    <p className="text-xs text-muted">cooldown {r.cooldown_s}s</p>
                  </div>
                  <Button
                    size="sm"
                    onClick={() => toggle.mutate({ id: r.id, enabled: !r.enabled })}
                    aria-pressed={r.enabled}
                  >
                    {r.enabled ? "On" : "Off"}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
