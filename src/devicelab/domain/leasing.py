"""Lease and fencing rules.

A lease is the only thing that entitles an agent to drive a device. Each grant bumps a
per-device fencing token; every report from an agent carries the token it was given, and
anything older is rejected. That is what makes a partitioned agent harmless after its
lease has been reclaimed.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from devicelab.domain.errors import StaleFenceError


def expiry_for(now: datetime, ttl_s: float) -> datetime:
    return now + timedelta(seconds=ttl_s)


def is_expired(expires_at: datetime, now: datetime) -> bool:
    return expires_at <= now


def next_fence(current: int) -> int:
    return current + 1


def check_fence(active_token: int, presented: int) -> None:
    if presented != active_token:
        raise StaleFenceError(
            "fencing token is stale",
            details={"active": active_token, "presented": presented},
        )


def renewal_expiry(
    now: datetime, ttl_s: float, current_expiry: datetime, max_extension_s: float
) -> datetime:
    """Renewals never move the expiry backwards and are bounded to one TTL from now."""
    proposed = now + timedelta(seconds=min(ttl_s, max_extension_s))
    return max(proposed, current_expiry)
