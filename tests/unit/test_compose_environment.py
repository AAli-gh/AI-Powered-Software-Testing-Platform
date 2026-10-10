"""Every setting documented in .env.example reaches the Docker stack.

Compose reads `.env` only to fill in `${...}` in the compose files; a variable that no compose
file names never reaches a container. That had silently disabled 16 documented settings
(the sandbox limits among them).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.sandbox.yml",
    "docker-compose.monitoring.yml",
)


def documented() -> set[str]:
    text = (ROOT / ".env.example").read_text("utf-8")
    return set(re.findall(r"^#?\s*(TESTMIND_[A-Z0-9_]+)=", text, re.MULTILINE))


def test_every_documented_setting_is_named_by_a_compose_file() -> None:
    compose = "\n".join((ROOT / name).read_text("utf-8") for name in COMPOSE_FILES)
    names = documented()
    assert len(names) > 30  # the parse found the documented settings
    missing = sorted(name for name in names if not re.search(rf"\b{name}\b", compose))
    assert missing == []


def test_the_shared_environment_passes_the_limits_to_api_and_worker() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text("utf-8"))
    shared = compose["x-app-environment"]
    for name in ("TESTMIND_SANDBOX_TIMEOUT_SECONDS", "TESTMIND_SANDBOX_MEMORY",
                 "TESTMIND_MUTATION_MAX_MUTANTS", "TESTMIND_COVERAGE_THRESHOLD"):  # fmt: skip
        assert shared[name] == f"${{{name}:-}}"  # empty: the default of core/config.py
    worker = compose["services"]["worker"]["environment"]
    assert all(worker[name] == value for name, value in shared.items())
