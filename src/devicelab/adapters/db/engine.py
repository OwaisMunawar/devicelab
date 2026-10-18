from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(url: str, *, pool_size: int = 10, max_overflow: int = 10) -> AsyncEngine:
    return create_async_engine(
        url, pool_size=pool_size, max_overflow=max_overflow, pool_pre_ping=True
    )


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: records are copied out of rows immediately, never lazily reloaded.
    return async_sessionmaker(engine, expire_on_commit=False)
