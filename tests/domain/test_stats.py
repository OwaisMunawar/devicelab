from __future__ import annotations

import pytest

from devicelab.domain.stats import percentile


def test_percentile_edges() -> None:
    assert percentile([], 95) == 0.0
    assert percentile([7.0], 99) == 7.0


@pytest.mark.parametrize(("p", "expected"), [(0, 1.0), (50, 3.0), (100, 5.0), (25, 2.0), (90, 4.6)])
def test_percentile_interpolates(p: float, expected: float) -> None:
    assert percentile([5.0, 1.0, 3.0, 2.0, 4.0], p) == pytest.approx(expected)
