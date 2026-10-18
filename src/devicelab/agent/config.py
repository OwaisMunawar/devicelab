from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AgentConfig:
    server_url: str
    host_name: str
    state_dir: Path
    enrol_token: str | None = None
    app_root: Path | None = None
    inventory_interval_s: float = 60.0
    reconnect_base_s: float = 1.0
    reconnect_max_s: float = 30.0
    max_message_bytes: int = 32 * 1024 * 1024
    allow_reboot: bool = True
    agent_version: str = "0.1.0"
