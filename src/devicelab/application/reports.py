from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from devicelab.application.ports import Clock, UowFactory
from devicelab.domain.stats import percentile

CSV_COLUMNS = (
    "execution_id",
    "job_id",
    "template",
    "device",
    "site",
    "attempt",
    "state",
    "failure_class",
    "started_at",
    "finished_at",
    "duration_s",
    "queue_wait_s",
)


class ReportService:
    def __init__(self, uow_factory: UowFactory, clock: Clock) -> None:
        self._uow = uow_factory
        self._clock = clock

    @staticmethod
    def _day_bounds(day: date) -> tuple[datetime, datetime]:
        start = datetime.combine(day, time.min, tzinfo=UTC)
        return start, start + timedelta(days=1)

    async def daily_summary(self, day: date | None = None) -> dict[str, Any]:
        day = day or self._clock.now().astimezone(UTC).date()
        start, end = self._day_bounds(day)
        async with self._uow() as uow:
            rows = await uow.stats.execution_rows(start, end)
            classes = await uow.stats.failure_classes(start, end)
            waits = await uow.stats.queue_waits(start, end)
            devices = await uow.devices.count_by_state()
        by_state: dict[str, int] = {}
        for row in rows:
            by_state[row["state"]] = by_state.get(row["state"], 0) + 1
        durations = [r["duration_s"] for r in rows if r["duration_s"] is not None]
        finished = sum(n for s, n in by_state.items() if s in ("succeeded", "failed", "lost"))
        succeeded = by_state.get("succeeded", 0)
        return {
            "date": day.isoformat(),
            "executions": {"total": len(rows), "by_state": by_state},
            "success_rate": round(succeeded / finished, 4) if finished else None,
            "failure_classes": classes,
            "duration_s": {
                "p50": round(percentile(durations, 50), 2),
                "p95": round(percentile(durations, 95), 2),
            },
            "queue_wait_s": {
                "p50": round(percentile(waits, 50), 2),
                "p95": round(percentile(waits, 95), 2),
                "p99": round(percentile(waits, 99), 2),
            },
            "devices": devices,
        }

    async def export_csv(self, start: datetime, end: datetime) -> str:
        async with self._uow() as uow:
            rows = await uow.stats.execution_rows(start, end)
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            # A leading = + - @ makes spreadsheets evaluate the cell as a formula.
            writer.writerow({k: _csv_safe(v) for k, v in row.items()})
        return out.getvalue()


def _csv_safe(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value
