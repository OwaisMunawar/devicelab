# Changelog

## 0.1.0

First public version.

- Control plane: FastAPI, SQLAlchemy 2 async, Alembic, PostgreSQL job queue and leases
  (`SKIP LOCKED`, TTL with heartbeat renewal, fencing tokens).
- Scheduler with priority aging, capability matching, per-host concurrency limits and
  multi-device jobs.
- Workflow engine: typed, allow-listed steps, parameters, retry with backoff and jitter,
  `continue_on_error`, failure policy; four example templates.
- Error recovery: failure classification, session restart, reboot, requeue on another
  device, quarantine after repeated infrastructure failures, audit-logged.
- Cron schedules with time zones and a skip / run-once misfire policy.
- Artifacts behind a storage interface (local filesystem, S3-compatible) with retention.
- Alerts with deduplication and cooldown, HMAC-signed Slack-compatible webhooks, daily report
  and CSV export.
- REST API v1 with API keys (hashed), RBAC, idempotency keys, pagination, typed errors and an
  append-only audit log.
- Host agent with outbound WebSocket, simulator, USB and simulated-fleet providers, and an
  Appium 3 / WebDriverAgent driver.
- React 19 dashboard with live updates, light and dark themes.
- Demo mode with 24 simulated devices across 4 sites; load, chaos and property tests.
