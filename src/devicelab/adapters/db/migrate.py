"""Run Alembic programmatically so the API container can migrate on start."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

_SCRIPTS = Path(__file__).parent / "migrations"


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_SCRIPTS))
    # '%' must be doubled for ConfigParser interpolation.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)
