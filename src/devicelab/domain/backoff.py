from __future__ import annotations

from collections.abc import Callable


def backoff_delay(
    attempt: int, *, base_s: float, cap_s: float, jitter: float, rng: Callable[[], float]
) -> float:
    """Exponential backoff with symmetric jitter. `attempt` is the one that just failed (1-based).

    `rng` is injected so tests are deterministic and the domain holds no global random state.
    """
    raw = min(cap_s, base_s * (2 ** max(attempt - 1, 0)))
    factor = 1.0 + jitter * (2.0 * rng() - 1.0)
    return float(max(raw * factor, 0.0))
