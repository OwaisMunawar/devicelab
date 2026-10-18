import { Link } from "@tanstack/react-router";
import clsx from "clsx";
import type { Device, Host } from "../api/types";
import { DEVICE_STATES, STATE_LABEL, isDeviceState } from "../lib/states";
import { relativeTime } from "../lib/format";
import { Badge, EmptyState } from "./ui";

export function BatteryBar({ pct }: { pct: number | null }) {
  if (pct === null) return <span className="text-xs text-muted">no battery</span>;
  const low = pct < 20;
  return (
    <span className="inline-flex items-center gap-1.5 text-xs tabular-nums">
      <span
        role="img"
        aria-label={`battery ${pct} percent`}
        className="relative h-2 w-8 overflow-hidden rounded-full bg-black/10 dark:bg-white/15"
      >
        <span
          className="absolute inset-y-0 left-0 rounded-full"
          style={{
            width: `${Math.max(pct, 3)}%`,
            background: low ? "var(--quarantined-dot)" : "var(--available-dot)",
          }}
        />
      </span>
      {pct}%
    </span>
  );
}

export function DeviceTile({ device }: { device: Device }) {
  const state = isDeviceState(device.state) ? device.state : "offline";
  const hot = device.thermal === "serious" || device.thermal === "critical";
  return (
    <Link
      to="/devices/$deviceId"
      params={{ deviceId: device.id }}
      aria-label={`${device.name}, ${STATE_LABEL[state]}, ${
        device.battery_pct === null ? "no battery" : `battery ${device.battery_pct} percent`
      }`}
      data-testid="device-tile"
      data-state={state}
      className={clsx(
        "group block rounded-lg border p-3 transition-shadow hover:shadow-md",
        "focus-visible:outline-offset-2",
      )}
      style={{ background: `var(--${state}-bg)`, borderColor: `var(--${state}-dot)` }}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="truncate text-sm font-semibold" style={{ color: `var(--${state}-fg)` }}>
          {device.name}
        </p>
        <span
          aria-hidden="true"
          className={clsx(
            "mt-1 size-2 shrink-0 rounded-full",
            state === "busy" && "motion-safe:animate-[pulse-dot_1.4s_ease-in-out_infinite]",
          )}
          style={{ background: `var(--${state}-dot)` }}
        />
      </div>
      <p className="mt-0.5 truncate text-xs" style={{ color: `var(--${state}-fg)` }}>
        {device.model} · iOS {device.ios_version}
      </p>
      <div
        className="mt-2 flex items-center justify-between gap-2"
        style={{ color: `var(--${state}-fg)` }}
      >
        <BatteryBar pct={device.battery_pct} />
        <span className="text-xs font-medium">{STATE_LABEL[state]}</span>
      </div>
      {hot && (
        <p className="mt-1.5 text-xs font-medium" style={{ color: `var(--${state}-fg)` }}>
          Thermal: {device.thermal}
        </p>
      )}
    </Link>
  );
}

interface DeviceGridProps {
  devices: Device[];
  hosts: Host[];
}

/** Devices grouped by site, each site headed by its host connection status. */
export function DeviceGrid({ devices, hosts }: DeviceGridProps) {
  if (devices.length === 0) {
    return (
      <EmptyState
        title="No devices match"
        description="Clear a filter, or connect a host agent to add devices to the fleet."
      />
    );
  }
  const sites = [...new Set(devices.map((d) => d.site))].sort();
  return (
    <div className="space-y-6">
      {sites.map((site) => {
        const inSite = devices.filter((d) => d.site === site);
        const siteHosts = hosts.filter((h) => h.site === site);
        const connected = siteHosts.length > 0 && siteHosts.every((h) => h.connected);
        return (
          <section key={site} aria-labelledby={`site-${site}`}>
            <div className="mb-2 flex flex-wrap items-center gap-3">
              <h3 id={`site-${site}`} className="text-sm font-semibold capitalize">
                {site}
              </h3>
              <Badge tone={connected ? "success" : "offline"}>
                {connected ? "Agent connected" : "Agent offline"}
              </Badge>
              <span className="text-xs text-muted">
                {inSite.length} devices
                {siteHosts[0]?.last_seen
                  ? ` · heartbeat ${relativeTime(siteHosts[0].last_seen)}`
                  : ""}
              </span>
            </div>
            <ul className="grid grid-cols-[repeat(auto-fill,minmax(10.5rem,1fr))] gap-2.5">
              {inSite.map((d) => (
                <li key={d.id}>
                  <DeviceTile device={d} />
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

export function StateLegend({ counts }: { counts: Record<string, number> }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1.5" aria-label="Device states">
      {DEVICE_STATES.map((s) => (
        <li key={s} className="flex items-center gap-1.5 text-xs text-muted">
          <span
            aria-hidden="true"
            className="size-2 rounded-full"
            style={{ background: `var(--${s}-dot)` }}
          />
          {STATE_LABEL[s]}
          <span className="font-semibold tabular-nums text-fg">{counts[s] ?? 0}</span>
        </li>
      ))}
    </ul>
  );
}
