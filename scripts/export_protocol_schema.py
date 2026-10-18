"""Regenerate docs/protocol/v1.schema.json. Run after an intentional protocol change."""

from __future__ import annotations

import json
from pathlib import Path

from devicelab.protocol.messages import json_schema

TARGET = Path(__file__).resolve().parents[1] / "docs" / "protocol" / "v1.schema.json"


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(json_schema(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {TARGET}")


if __name__ == "__main__":
    main()
