import { Link, useParams } from "@tanstack/react-router";
import { useState } from "react";
import {
  useDevice,
  useDeviceAction,
  useDeviceTimeline,
  useExecutions,
  useSaveLabels,
} from "../api/hooks";
import type { HealthSample } from "../api/types";
import { BatteryBar } from "../components/DeviceGrid";
import { PageHeader, QueryError } from "../components/PageHeader";
import { StateBadge } from "../components/StateBadge";
import { Badge, Button, Card, EmptyState, Table, TextField } from "../components/ui";
import { formatSeconds, formatTime, relativeTime, shortId } from "../lib/format";

function HealthChart({ samples }: { samples: HealthSample[] }) {
  const points = samples.filter((s) => s.battery_pct !== null);
  if (points.length < 2) {
    return (
      <EmptyState
        title="Not enough history yet"
        description="Health samples appear as the agent reports changes."
      />
    );
  }
  const t0 = new Date(points[0]?.ts ?? 0).getTime();
  const t1 = new Date(points.at(-1)?.ts ?? 0).getTime();
  const span = Math.max(t1 - t0, 1);
  const x = (ts: string) => ((new Date(ts).getTime() - t0) / span) * 560 + 20;
  const y = (pct: number) => 120 - (pct / 100) * 100;
  const path = points
    .map(
      (p, i) => `${i === 0 ? "M" : "L"}${x(p.ts).toFixed(1)},${y(p.battery_pct ?? 0).toFixed(1)}`,
    )
    .join(" ");
  const hot = samples.filter((s) => s.thermal === "serious" || s.thermal === "critical");
  return (
    <figure>
      <svg
        viewBox="0 0 600 140"
        role="img"
        aria-label="Battery level over time, with thermal warnings marked"
        className="w-full"
      >
        {[0, 50, 100].map((g) => (
          <g key={g}>
            <line x1="20" x2="580" y1={y(g)} y2={y(g)} stroke="var(--line)" strokeDasharray="3 4" />
            <text x="0" y={y(g) + 4} fontSize="9" fill="var(--muted)">
              {g}
            </text>
          </g>
        ))}
        <path d={path} fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" />
        {hot.map((s) => (
          <rect
            key={s.ts}
            x={x(s.ts) - 2}
            y="6"
            width="4"
            height="114"
            fill="var(--maintenance-dot)"
            opacity="0.35"
          />
        ))}
      </svg>
      <figcaption className="mt-1 flex justify-between text-xs text-muted">
        <span>{formatTime(points[0]?.ts)}</span>
        <span>battery % · amber bands mark thermal warnings</span>
        <span>{formatTime(points.at(-1)?.ts)}</span>
      </figcaption>
    </figure>
  );
}

export function DevicePage() {
  const { deviceId } = useParams({ from: "/devices/$deviceId" });
  const device = useDevice(deviceId);
  const timeline = useDeviceTimeline(deviceId, 48);
  const executions = useExecutions({ device_id: deviceId });
  const act = useDeviceAction();
  const saveLabels = useSaveLabels();
  const [labels, setLabels] = useState<string | null>(null);

  if (device.isError) return <QueryError error={device.error} />;
  const d = device.data;
  if (!d) return <p className="text-sm text-muted">Loading device...</p>;

  const inUse = d.state === "leased" || d.state === "busy";
  return (
    <>
      <PageHeader
        title={d.name}
        description={`${d.model} · iOS ${d.ios_version} · ${d.site} (${d.region}) · ${d.kind}`}
        actions={
          <>
            <StateBadge state={d.state} />
            {(d.state === "quarantined" || d.state === "maintenance" || d.state === "offline") && (
              <Button
                variant="primary"
                onClick={() => act.mutate({ id: d.id, action: "return-to-service" })}
                busy={act.isPending}
              >
                Return to service
              </Button>
            )}
            {!inUse && d.state !== "quarantined" && (
              <Button
                onClick={() => act.mutate({ id: d.id, action: "quarantine" })}
                busy={act.isPending}
              >
                Quarantine
              </Button>
            )}
            {!inUse && d.state !== "maintenance" && d.state !== "quarantined" && (
              <Button
                onClick={() => act.mutate({ id: d.id, action: "maintenance", enabled: true })}
                busy={act.isPending}
              >
                Maintenance
              </Button>
            )}
          </>
        }
      />
      {act.isError && <QueryError error={act.error} />}
      {d.quarantine_reason && (
        <p className="mb-4 rounded-lg border border-line bg-surface p-3 text-sm">
          <strong>Quarantined:</strong> {d.quarantine_reason}
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <Card title="Battery and health" description="Last 48 hours, sampled on change.">
          <HealthChart samples={timeline.data ?? []} />
        </Card>
        <Card title="Facts">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted">Battery</dt>
            <dd>
              <BatteryBar pct={d.battery_pct} />
            </dd>
            <dt className="text-muted">Thermal</dt>
            <dd className="capitalize">{d.thermal}</dd>
            <dt className="text-muted">Storage free</dt>
            <dd>{d.storage_free_gb === null ? "-" : `${d.storage_free_gb} GB`}</dd>
            <dt className="text-muted">Connectivity</dt>
            <dd className="capitalize">{d.connectivity}</dd>
            <dt className="text-muted">Last seen</dt>
            <dd>{relativeTime(d.last_seen)}</dd>
            <dt className="text-muted">Infra failures</dt>
            <dd>{d.consecutive_infra_failures} in a row</dd>
            <dt className="text-muted">UDID</dt>
            <dd className="truncate font-mono text-xs" title={d.udid}>
              {d.udid}
            </dd>
          </dl>
          <div className="mt-4">
            <p className="mb-1.5 text-xs font-medium text-muted">Labels</p>
            <div className="mb-2 flex flex-wrap gap-1.5">
              {d.labels.length === 0 && <span className="text-xs text-muted">none</span>}
              {d.labels.map((l) => (
                <Badge key={l} tone="accent" dot={false}>
                  {l}
                </Badge>
              ))}
            </div>
            <form
              className="flex items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                const next = (labels ?? d.labels.join(", "))
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean);
                saveLabels.mutate({ id: d.id, labels: next }, { onSuccess: () => setLabels(null) });
              }}
            >
              <div className="flex-1">
                <TextField
                  label="Edit labels"
                  value={labels ?? d.labels.join(", ")}
                  onChange={(e) => setLabels(e.target.value)}
                  placeholder="wifi-only, dark-lab"
                />
              </div>
              <Button type="submit" size="sm" busy={saveLabels.isPending}>
                Save
              </Button>
            </form>
          </div>
        </Card>
      </div>

      <Card className="mt-4" title="Execution history" flush>
        <Table
          caption="Executions on this device"
          rows={executions.data?.items ?? []}
          rowKey={(e) => e.id}
          empty={{ title: "No executions yet", description: "Runs on this device appear here." }}
          columns={[
            {
              key: "id",
              header: "Execution",
              cell: (e) => (
                <Link
                  to="/executions/$executionId"
                  params={{ executionId: e.id }}
                  className="font-mono text-accent hover:underline"
                >
                  {shortId(e.id)}
                </Link>
              ),
            },
            { key: "state", header: "State", cell: (e) => <StateBadge state={e.state} /> },
            { key: "attempt", header: "Attempt", cell: (e) => e.attempt },
            { key: "failure", header: "Failure", cell: (e) => e.failure_class ?? "-" },
            { key: "started", header: "Started", cell: (e) => formatTime(e.started_at) },
            {
              key: "dur",
              header: "Duration",
              align: "right",
              cell: (e) =>
                formatSeconds(
                  e.started_at && e.finished_at
                    ? (new Date(e.finished_at).getTime() - new Date(e.started_at).getTime()) / 1000
                    : null,
                ),
            },
          ]}
        />
      </Card>
    </>
  );
}
