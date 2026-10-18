from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from devicelab.application.config import ControlPlaneConfig


class Settings(BaseSettings):
    """Process configuration. Everything is overridable as DEVICELAB_<NAME>."""

    model_config = SettingsConfigDict(env_prefix="DEVICELAB_", extra="ignore")

    database_url: str = "postgresql+asyncpg://devicelab:devicelab@localhost:5432/devicelab"
    migrate_on_start: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    bootstrap_admin_key: str | None = None
    demo: bool = False

    artifact_backend: Literal["local", "s3"] = "local"
    artifact_dir: Path = Path("./artifacts-data")
    s3_bucket: str = "devicelab-artifacts"
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"

    log_json: bool = True
    log_level: str = "INFO"

    lease_ttl_s: float = 45.0
    heartbeat_interval_s: float = 10.0
    offline_after_s: float = 35.0
    quarantine_threshold: int = 3
    default_max_attempts: int = 3
    aging_per_minute: float = 1.0
    artifact_retention_days: int = 14

    scheduler_interval_s: float = 0.5
    sweep_interval_s: float = 2.0
    schedule_interval_s: float = 5.0
    alert_interval_s: float = 15.0
    retention_interval_s: float = 3600.0

    db_pool_size: int = 10
    ws_max_message_bytes: int = 32 * 1024 * 1024

    def control_plane(self) -> ControlPlaneConfig:
        return ControlPlaneConfig(
            lease_ttl_s=self.lease_ttl_s,
            heartbeat_interval_s=self.heartbeat_interval_s,
            offline_after_s=self.offline_after_s,
            quarantine_threshold=self.quarantine_threshold,
            default_max_attempts=self.default_max_attempts,
            aging_per_minute=self.aging_per_minute,
            artifact_retention_days=self.artifact_retention_days,
        )
