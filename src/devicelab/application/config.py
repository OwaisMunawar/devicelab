from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ControlPlaneConfig:
    lease_ttl_s: float = 45.0
    heartbeat_interval_s: float = 10.0
    # A device with no heartbeat for this long is offline. Must exceed a few heartbeats.
    offline_after_s: float = 35.0
    quarantine_threshold: int = 3
    default_max_attempts: int = 3
    aging_per_minute: float = 1.0
    min_battery_pct: int = 20
    scan_limit: int = 1000
    enrolment_ttl_s: float = 3600.0
    artifact_retention_days: int = 14
    # Health history is sampled, not logged per heartbeat, to keep write volume flat.
    sample_interval_s: float = 300.0
    sample_battery_delta: int = 5
    reclaim_batch: int = 200
