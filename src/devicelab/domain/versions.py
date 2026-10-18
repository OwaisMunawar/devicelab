from __future__ import annotations

from devicelab.domain.errors import ValidationFailedError

type Version = tuple[int, ...]


def parse_version(raw: str) -> Version:
    """Parse "17.5.1" into (17, 5, 1). Tuples compare the way release numbers should."""
    parts = raw.strip().split(".")
    try:
        parsed = tuple(int(p) for p in parts)
    except ValueError as exc:
        raise ValidationFailedError(f"not a version: {raw!r}", code="invalid_version") from exc
    if not parsed:
        raise ValidationFailedError(f"not a version: {raw!r}", code="invalid_version")
    return parsed


def _pad(v: Version, width: int) -> Version:
    return v + (0,) * (width - len(v))


def compare_versions(a: str, b: str) -> int:
    """Return -1, 0 or 1. "17" and "17.0.0" are equal."""
    va, vb = parse_version(a), parse_version(b)
    width = max(len(va), len(vb))
    pa, pb = _pad(va, width), _pad(vb, width)
    return (pa > pb) - (pa < pb)
