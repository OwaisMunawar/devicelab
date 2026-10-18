from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from devicelab.adapters.db.engine import create_engine, session_factory
from devicelab.adapters.db.migrate import upgrade
from devicelab.adapters.db.repositories import SqlUnitOfWork

ADMIN_URL = os.environ.get(
    "DEVICELAB_TEST_ADMIN_URL",
    "postgresql+asyncpg://devicelab:devicelab@localhost:55432/postgres",
)
TEST_DB = "devicelab_test"
TEST_URL = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB}"


@pytest_asyncio.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    await admin.dispose()
    # Alembic's env uses asyncio.run(), so migrate from a worker thread.
    import asyncio

    await asyncio.to_thread(upgrade, TEST_URL)
    eng = create_engine(TEST_URL, pool_size=20, max_overflow=20)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(autouse=False)
async def db(engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """A clean schema per test. The audit trigger blocks TRUNCATE by design, so lift it here."""
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE audit_log DISABLE TRIGGER USER"))
        await conn.execute(
            text(
                "TRUNCATE hosts, enrolment_tokens, devices, device_health_samples, "
                "workflow_templates, jobs, leases, executions, execution_steps, execution_logs, "
                "artifacts, schedules, alert_rules, alerts, webhooks, api_keys, audit_log CASCADE"
            )
        )
        await conn.execute(text("ALTER TABLE audit_log ENABLE TRIGGER USER"))
    yield engine


@pytest.fixture
def uow_factory(db: AsyncEngine):  # type: ignore[no-untyped-def]
    factory = session_factory(db)
    return lambda: SqlUnitOfWork(factory)
