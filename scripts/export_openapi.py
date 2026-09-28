"""Write the API's OpenAPI schema to web/openapi.json (the dashboard's types come from it).

Usage: uv run python scripts/export_openapi.py [--check]
With --check, fail if the committed file differs from the schema of the current code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from testmind.api.app import create_app
from testmind.core.config import Settings

TARGET = Path(__file__).resolve().parent.parent / "web" / "openapi.json"


def schema() -> str:
    # Placeholder connection settings: building the schema opens no connection.
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://schema:schema@localhost/schema",  # type: ignore[arg-type]
        redis_url="redis://localhost",  # type: ignore[arg-type]
        repos_root=Path.cwd(),
        log_json=False,
    )
    return json.dumps(create_app(settings).openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    current = schema()
    if "--check" in sys.argv:
        if not TARGET.exists() or TARGET.read_text("utf-8") != current:
            sys.stderr.write(f"{TARGET} is out of date: run `make openapi` and commit the result\n")
            return 1
        return 0
    TARGET.write_text(current, "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
