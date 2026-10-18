from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path


class CredentialStore:
    """Keeps the long-lived credential issued at enrolment, readable by the owner only."""

    def __init__(self, directory: Path, host_name: str) -> None:
        self._path = directory / f"{host_name}.json"

    def load(self) -> str | None:
        try:
            data = json.loads(self._path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        credential = data.get("credential")
        return credential if isinstance(credential, str) else None

    def save(self, credential: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump({"credential": credential}, handle)
        tmp.replace(self._path)

    def clear(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._path.unlink()
