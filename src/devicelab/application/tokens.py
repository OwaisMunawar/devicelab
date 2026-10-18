"""Secret generation and hashing for API keys, enrolment tokens and agent credentials.

All of these are 256-bit random values, so a fast hash is the right tool: there is no
low-entropy password to stretch, and lookups stay cheap on every authenticated request.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def verify_secret(secret: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(secret), expected_hash)


def generate_token(kind: str) -> tuple[str, str]:
    """Return (full secret, display prefix). Format: dl<kind>_<random>."""
    secret = f"dl{kind}_{secrets.token_urlsafe(32)}"
    return secret, secret[:10]
