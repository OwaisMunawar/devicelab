import { useState } from "react";
import { useDailyReport, useDevices, useFleetSummary, useHosts } from "../api/hooks";
import { DeviceGrid, StateLegend } from "../components/DeviceGrid";
import { PageHeader, QueryError } from "../components/PageHeader";
import { Card, Stat } from "../components/ui";
import { DEVICE_STATES, STATE_LABEL } from "../lib/states";
import { formatSeconds, percent } from "../lib/format";

export function FleetPage() {
  const [site, setSite] = useState("");
  const [state, setState] = useState("");
  const summary = useFleetSummary();
  const devices = useDevices({ site, state });
  const hosts = useHosts();
  const report = useDailyReport();

  const counts = summary.data?.devices ?? {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const ready = counts.available ?? 0;
  const working = (counts.leased ?? 0) + (counts.busy ?? 0);
  const out = (counts.offline ?? 0) + (counts.quarantined ?? 0) + (counts.maintenance ?? 0);

  return (
    <>
      <PageHeader
        title="Fleet overview"
        description="Every device across every site, coloured by state. Updates live as agents report."
      />
      {summary.isError && <QueryError error={summary.error} />}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat
          label="Devices"
          value={total}
          hint={`${summary.data?.hosts_connected ?? 0}/${summary.data?.hosts_total ?? 0} agents connected`}
        />
        <Stat label="Ready" value={ready} tone="good" hint="available to lease" />
        <Stat
          label="Working"
          value={working}
          hint={`${summary.data?.leases_active ?? 0} active leases`}
        />
        <Stat
          label="Out of service"
          value={out}
          tone={out > 0 ? "warn" : "default"}
          hint="offline, quarantined, maintenance"
        />
        <Stat
          label="Queue depth"
          value={summary.data?.queue_depth ?? 0}
          hint="jobs waiting for a device"
        />
        <Stat
          label="Success rate today"
          value={percent(report.data?.success_rate)}
          hint={`queue wait p95 ${formatSeconds(report.data?.queue_wait_s.p95)}`}
          tone={(report.data?.success_rate ?? 1) < 0.8 ? "bad" : "good"}
        />
      </div>

      <Card
        className="mt-6"
        title="Devices"
        actions={
          <>
            <label className="sr-only" htmlFor="site-filter">
              Site
            </label>
            <select
              id="site-filter"
              value={site}
              onChange={(e) => setSite(e.target.value)}
              className="rounded-lg border border-line bg-surface px-2 py-1.5 text-xs"
            >
              <option value="">All sites</option>
              {(summary.data?.sites ?? []).map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <label className="sr-only" htmlFor="state-filter">
              State
            </label>
            <select
              id="state-filter"
              value={state}
              onChange={(e) => setState(e.target.value)}
              className="rounded-lg border border-line bg-surface px-2 py-1.5 text-xs"
            >
              <option value="">All states</option>
              {DEVICE_STATES.map((s) => (
                <option key={s} value={s}>
                  {STATE_LABEL[s]}
                </option>
              ))}
            </select>
          </>
        }
      >
        <div className="mb-4">
          <StateLegend counts={counts} />
        </div>
        {devices.isError ? (
          <QueryError error={devices.error} />
        ) : (
          <DeviceGrid devices={devices.data?.items ?? []} hosts={hosts.data ?? []} />
        )}
      </Card>
    </>
  );
}
