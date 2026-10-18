# Scaling

Capacity arithmetic for 10, 50 and 200+ devices, what was measured, and where the design
would have to change. Numbers marked *measured* come from `scripts/loadtest.py` (see the
README for the run); everything else is arithmetic you can check.

## Assumptions

- About 10 devices per Mac (USB hubs and a Mac's thermal limits make that a realistic ceiling),
  so hosts = devices / 10. With one device per host the connection numbers below multiply by 10.
- Heartbeat every 10 s; lease TTL 45 s.
- A typical execution: 6 steps, 20 log lines, 2 artifacts, 60 s long.
- Worst case for write load: every device busy all the time.

## Connections

One WebSocket per host, held open.

| Devices | Hosts | Sockets on the API |
| --- | --- | --- |
| 10 | 1 | 1 |
| 50 | 5 | 5 |
| 200 | 20 | 20 |

Hundreds of sockets are ordinary for an asyncio server, so the socket count is not the
constraint at any size here. Agents hold no inbound ports.

## Heartbeat load

Each heartbeat is three short transactions: renew the host's leases, update that host's
devices (one `UPDATE` per device, in id order), update the host row.

| Devices | Heartbeats/s | Device rows updated/s | Health samples/s (worst case) |
| --- | --- | --- | --- |
| 10 | 0.1 | 1 | under 0.04 |
| 50 | 0.5 | 5 | under 0.17 |
| 200 | 2 | 20 | under 0.7 |

Samples are written on meaningful change or every 5 minutes (ADR-009), so the table is an
upper bound.

## Execution write load

Roughly 1 lease + 1 execution + S steps (bulk insert) + 2S step updates + L log lines + A
artifact rows + a handful of job and device updates, so about 50 row writes for the typical
execution above. With every device busy and 60 s executions:

| Devices | Executions/s | Row writes/s |
| --- | --- | --- |
| 10 | 0.17 | about 8 |
| 50 | 0.83 | about 42 |
| 200 | 3.3 | about 170 |

Postgres on modest hardware sustains thousands of small writes per second, so the database is
not the limit before roughly an order of magnitude beyond 200 devices. Log lines dominate: a
chatty workflow (hundreds of lines) is the first thing to move to batching or a log store.

## The scheduler

A tick is one transaction: lock free devices (`SKIP LOCKED`), scan up to `scan_limit` queued
jobs as a handful of small columns, plan in memory, then write leases and executions for the
assignments. Cost is proportional to the free devices, the scanned queue and the assignments
made, not to the total fleet.

*Measured* (50 devices, 5 sites, 525 jobs, Postgres in Docker on a laptop, simulated steps):
see the README table for p50/p95/p99 tick time and assignments per second of tick time. At
200 devices and 60 s executions the system needs about 3.3 assignments/s, a few percent of
one scheduler's capacity at the measured rate, which is why there is only one by default.

## Where it stops being enough, and what changes

1. **Queue scan.** Jobs beyond `scan_limit` (1000) are not considered in a tick. Priority and
   age ordering is applied before the cut, so the oldest and highest-priority work is always
   in the window; raise the limit or add a partial index on `(priority DESC, queued_at)`
   (present) if queues routinely exceed it.
2. **One API process.** The scheduler, the agent sockets and the event stream live in one
   process. Moving to several API replicas needs: (a) the event bus on Postgres
   `LISTEN/NOTIFY` (already behind the `EventPublisher` port), and (b) dispatch wake-ups
   routed to the replica that holds a host's socket. Several schedulers are already safe:
   rows are partitioned by `SKIP LOCKED` and the unique index on active leases is the backstop.
3. **Sharding the scheduler.** Jobs and devices both carry `site` and `region`. A scheduler per
   region is a `WHERE` clause on both scans, and removes cross-region contention entirely.
   Jobs with no site constraint would be assigned to a home region.
4. **Hot rows.** `devices` is the contended table (heartbeats, leases, operator actions).
   Lock order is fixed (ADR-008) and heartbeats batch per host; if update contention shows up,
   split mutable health columns into a side table keyed by device id.
5. **Artifacts.** Uploads ride the agent socket as base64 (20 MB cap). At 200 devices
   capturing many screenshots, switch to presigned URLs so bytes bypass the API.
6. **Retention.** `execution_logs` and `device_health_samples` grow unbounded without pruning;
   artifact retention exists, log and sample pruning is a roadmap item. Partition them by month
   before they reach tens of millions of rows.

## What was not measured

- Anything above 50 simulated devices.
- Real device latency: simulated steps take 50 to 150 ms; real Appium steps take seconds, which
  lowers the scheduler's duty cycle and so favours the design.
- Postgres on real server hardware; the measured database ran in Docker on a laptop.
