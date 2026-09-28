"""Derive src/testmind/sandbox/seccomp.json from Docker's default seccomp profile.

Usage: uv run python scripts/make_seccomp_profile.py <moby default.json>
The input is https://raw.githubusercontent.com/moby/profiles/main/seccomp/default.json.
What is removed, and why: src/testmind/sandbox/seccomp.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from testmind.sandbox.docker_sandbox import SECCOMP_PROFILE
from testmind.sandbox.seccomp import derive


def main() -> int:
    if len(sys.argv) != 2:  # noqa: PLR2004
        sys.stderr.write(__doc__ or "")
        return 2
    upstream = json.loads(Path(sys.argv[1]).read_text("utf-8"))
    SECCOMP_PROFILE.write_text(json.dumps(derive(upstream), indent=1) + "\n", "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
