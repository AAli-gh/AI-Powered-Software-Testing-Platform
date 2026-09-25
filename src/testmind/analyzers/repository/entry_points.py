"""Ways a project can be started: console scripts, manage.py, __main__, ASGI/WSGI apps, scripts."""

from __future__ import annotations

import configparser
import tomllib
from collections.abc import Callable, Mapping
from pathlib import PurePosixPath
from typing import Any

from testmind.analyzers.repository.models import EntryPoint, EntryPointKind
from testmind.analyzers.repository.python_scanner import ASGI_KINDS, PythonFileFacts

ReadFile = Callable[[str], str | None]
NOT_SCRIPTS = frozenset({"setup.py", "manage.py", "__main__.py", "conftest.py"})


def module_name(path: str) -> str:
    """'src/pkg/api/main.py' → 'pkg.api.main' (a leading 'src/' layout directory is dropped)."""
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] == "src" and len(parts) > 1:
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def find_entry_points(
    python_files: list[PythonFileFacts],
    test_files: set[str],
    manifest_paths: list[str],
    read: ReadFile,
) -> list[EntryPoint]:
    found: list[EntryPoint] = []
    for path in sorted(manifest_paths):
        found.extend(_console_scripts(path, read))

    for facts in python_files:
        name = PurePosixPath(facts.path).name
        if name == "manage.py" and facts.sets_django_settings_module:
            found.append(EntryPoint(kind=EntryPointKind.DJANGO_MANAGE, path=facts.path))
        elif name == "__main__.py":
            package = module_name(str(PurePosixPath(facts.path).parent / "__init__.py"))
            found.append(
                EntryPoint(kind=EntryPointKind.PACKAGE_MAIN, path=facts.path, target=package)
            )
        elif facts.has_main_guard and name not in NOT_SCRIPTS and facts.path not in test_files:
            found.append(EntryPoint(kind=EntryPointKind.SCRIPT_MAIN, path=facts.path))

        for app in facts.app_instances:
            if app.variable is not None:
                attribute = app.variable
            elif app.factory is not None:
                attribute = f"{app.factory}()"  # application factory, e.g. create_app()
            else:
                continue  # created somewhere that cannot be addressed as module:attribute
            kind = (
                EntryPointKind.ASGI_APP if app.framework in ASGI_KINDS else EntryPointKind.WSGI_APP
            )
            target = f"{module_name(facts.path)}:{attribute}"
            found.append(EntryPoint(kind=kind, path=facts.path, target=target))
    return sorted(found, key=lambda e: (e.kind.value, e.path, e.target or ""))


def _console_scripts(path: str, read: ReadFile) -> list[EntryPoint]:
    text = read(path)
    if text is None:
        return []
    name = PurePosixPath(path).name
    scripts: dict[str, str] = {}
    if name == "pyproject.toml":
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return []  # already reported by the dependency collector
        scripts.update(_str_map(_get(data, "project", "scripts")))
        scripts.update(_str_map(_get(data, "tool", "poetry", "scripts")))
    elif name == "setup.cfg":
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(text)
        except configparser.Error:
            return []
        if parser.has_option("options.entry_points", "console_scripts"):
            for line in parser.get("options.entry_points", "console_scripts").splitlines():
                if "=" in line:
                    script, target = line.split("=", 1)
                    scripts[script.strip()] = target.strip()
    return [
        EntryPoint(kind=EntryPointKind.CONSOLE_SCRIPT, path=path, target=f"{script} = {target}")
        for script, target in sorted(scripts.items())
    ]


def _get(data: Mapping[str, Any], *keys: str) -> object:
    current: object = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _str_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items() if isinstance(v, str)}
