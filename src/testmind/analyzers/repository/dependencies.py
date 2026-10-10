"""Declared Python dependencies, read from manifests without executing anything.

Supported: pyproject.toml (PEP 621, PEP 735 dependency groups, Poetry), requirements*.txt
(including `-r` includes inside the repository), setup.cfg, setup.py (static `ast` literals only)
and Pipfile.
"""

from __future__ import annotations

import ast
import configparser
import re
import tomllib
from collections.abc import Callable, Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from testmind.analyzers.repository.models import Dependency, DependencyGroup, Issue

DEV_GROUP_NAMES = frozenset({"dev", "develop", "development", "test", "tests", "testing", "lint"})
MAX_INCLUDE_DEPTH = 5

ReadFile = Callable[[str], str | None]


class DependencyCollector:
    def __init__(self, read: ReadFile) -> None:
        self._read = read
        self.dependencies: list[Dependency] = []
        self.issues: list[Issue] = []
        self._seen: set[tuple[str, DependencyGroup, str]] = set()

    def collect(self, paths: Iterable[str]) -> list[Dependency]:
        for path in sorted(paths):
            name = PurePosixPath(path).name
            if name == "pyproject.toml":
                self._pyproject(path)
            elif _is_requirements_file(path):
                self._requirements(path, _requirements_group(path), depth=0, stack=())
            elif name == "setup.cfg":
                self._setup_cfg(path)
            elif name == "setup.py":
                self._setup_py(path)
            elif name == "Pipfile":
                self._pipfile(path)
        return self.dependencies

    # ------------------------------------------------------------------ helpers
    def _add_requirement(self, line: str, group: DependencyGroup, source: str) -> None:
        try:
            req = Requirement(line)
        except InvalidRequirement as exc:
            self.issues.append(Issue(path=source, message=f"Invalid requirement {line!r}: {exc}"))
            return
        self._add(
            Dependency(
                name=canonicalize_name(req.name),
                specifier=str(req.specifier),
                group=group,
                source=source,
                extras=sorted(req.extras),
                marker=str(req.marker) if req.marker else None,
            )
        )

    def _add(self, dependency: Dependency) -> None:
        key = (dependency.name, dependency.group, dependency.source)
        if key not in self._seen:
            self._seen.add(key)
            self.dependencies.append(dependency)

    def _load_toml(self, path: str) -> dict[str, Any] | None:
        text = self._read(path)
        if text is None:
            return None
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            self.issues.append(Issue(path=path, message=f"Invalid TOML: {exc}"))
            return None

    # ------------------------------------------------------------------ pyproject.toml
    def _pyproject(self, path: str) -> None:
        data = self._load_toml(path)
        if data is None:
            return
        project = _mapping(data.get("project"))
        for line in _strings(project.get("dependencies")):
            self._add_requirement(line, DependencyGroup.RUNTIME, path)
        for extra, lines in _mapping(project.get("optional-dependencies")).items():
            group = _group_for_name(str(extra), default=DependencyGroup.OPTIONAL)
            for line in _strings(lines):
                self._add_requirement(line, group, path)
        for name, lines in _mapping(data.get("dependency-groups")).items():  # PEP 735
            group = _group_for_name(str(name), default=DependencyGroup.OPTIONAL)
            for line in _strings(lines):  # {include-group = ...} tables are skipped
                self._add_requirement(line, group, path)

        poetry = _mapping(_mapping(data.get("tool")).get("poetry"))
        self._poetry_table(_mapping(poetry.get("dependencies")), DependencyGroup.RUNTIME, path)
        self._poetry_table(
            _mapping(poetry.get("dev-dependencies")), DependencyGroup.DEVELOPMENT, path
        )
        for name, table in _mapping(poetry.get("group")).items():
            group = _group_for_name(str(name), default=DependencyGroup.OPTIONAL)
            self._poetry_table(_mapping(_mapping(table).get("dependencies")), group, path)

    def _poetry_table(self, table: Mapping[str, Any], group: DependencyGroup, path: str) -> None:
        for name, spec in table.items():
            if str(name).lower() == "python":
                continue
            version = spec if isinstance(spec, str) else _mapping(spec).get("version", "")
            extras = _strings(_mapping(spec).get("extras")) if isinstance(spec, dict) else []
            self._add(
                Dependency(
                    name=canonicalize_name(str(name)),
                    specifier="" if version == "*" else str(version),
                    group=group,
                    source=path,
                    extras=sorted(extras),
                )
            )

    # ------------------------------------------------------------------ requirements*.txt
    def _requirements(
        self, path: str, group: DependencyGroup, *, depth: int, stack: tuple[str, ...]
    ) -> None:
        if depth > MAX_INCLUDE_DEPTH or path in stack:
            self.issues.append(Issue(path=path, message="Requirement include cycle or too deep"))
            return
        text = self._read(path)
        if text is None:
            return
        for raw in _logical_lines(text):
            line = raw.split(" #", 1)[0].strip()
            if not line or line.startswith("#"):
                continue
            include = re.match(r"^(-r|--requirement)\s*=?\s*(\S+)$", line)
            if include:
                target = _join_inside_repo(path, include.group(2))
                if target is None:
                    self.issues.append(Issue(path=path, message=f"Ignored include {line!r}"))
                else:
                    # An included file keeps its own group: `-r requirements.txt` inside
                    # requirements-dev.txt pulls in *runtime* dependencies.
                    self._requirements(
                        target, _requirements_group(target), depth=depth + 1, stack=(*stack, path)
                    )
                continue
            if line.startswith("-") or "://" in line:
                # Options (-e, -c, --index-url, --hash ...) and direct URLs are not dependencies
                # we can name reliably.
                continue
            self._add_requirement(line.split(" --hash", 1)[0].strip(), group, path)

    # ------------------------------------------------------------------ setup.cfg
    def _setup_cfg(self, path: str) -> None:
        text = self._read(path)
        if text is None:
            return
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(text, source=path)
        except configparser.Error as exc:
            self.issues.append(Issue(path=path, message=f"Invalid setup.cfg: {exc}"))
            return
        if parser.has_option("options", "install_requires"):
            for line in _cfg_list(parser.get("options", "install_requires")):
                self._add_requirement(line, DependencyGroup.RUNTIME, path)
        if parser.has_section("options.extras_require"):
            for extra, value in parser.items("options.extras_require"):
                group = _group_for_name(extra, default=DependencyGroup.OPTIONAL)
                for line in _cfg_list(value):
                    self._add_requirement(line, group, path)

    # ------------------------------------------------------------------ setup.py
    def _setup_py(self, path: str) -> None:
        """Read `install_requires` / `extras_require` only when they are literal values.

        `setup(install_requires=requires)` is resolved when `requires` is bound once, at module
        level, to a literal and never changed (see `_constants`). Nothing is executed.
        """
        text = self._read(path)
        if text is None:
            return
        try:
            tree = ast.parse(text, filename=path)
        except (SyntaxError, ValueError) as exc:
            self.issues.append(Issue(path=path, message=f"Cannot parse setup.py: {exc}"))
            return
        constants = setup_py_constants(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) == "setup":
                for keyword in node.keywords:
                    if keyword.arg in {"install_requires", "extras_require"}:
                        self._setup_keyword(path, keyword, constants)

    def _setup_keyword(
        self, path: str, keyword: ast.keyword, constants: Mapping[str, ast.expr]
    ) -> None:
        expression = keyword.value
        if isinstance(expression, ast.Name) and expression.id in constants:
            expression = constants[expression.id]
        try:
            value = ast.literal_eval(expression)
        except ValueError:
            self.issues.append(Issue(path=path, message=f"{keyword.arg} is not a static literal"))
            return
        if keyword.arg == "install_requires":
            for line in _strings(value):
                self._add_requirement(line, DependencyGroup.RUNTIME, path)
        elif isinstance(value, dict):
            for extra, lines in value.items():
                group = _group_for_name(str(extra), default=DependencyGroup.OPTIONAL)
                for line in _strings(lines):
                    self._add_requirement(line, group, path)

    # ------------------------------------------------------------------ Pipfile
    def _pipfile(self, path: str) -> None:
        data = self._load_toml(path)
        if data is None:
            return
        for section, group in (
            ("packages", DependencyGroup.RUNTIME),
            ("dev-packages", DependencyGroup.DEVELOPMENT),
        ):
            self._poetry_table(_mapping(data.get(section)), group, path)


# ---------------------------------------------------------------------- module helpers
MANIFEST_NAMES = frozenset({"pyproject.toml", "setup.cfg", "setup.py", "Pipfile"})


def is_dependency_manifest(path: str) -> bool:
    return PurePosixPath(path).name in MANIFEST_NAMES or _is_requirements_file(path)


def _is_requirements_file(path: str) -> bool:
    """`requirements*.txt` anywhere, or any `.txt` inside a `requirements/` directory."""
    pure = PurePosixPath(path)
    if pure.suffix != ".txt":
        return False
    return pure.name.startswith("requirements") or pure.parent.name == "requirements"


DEV_REQUIREMENT_TOKENS = DEV_GROUP_NAMES | {"ci", "docs", "dev-requirements"}


def _requirements_group(path: str) -> DependencyGroup:
    """`requirements-dev.txt`, `requirements/test.txt`, ... are development dependencies."""
    tokens = set(re.split(r"[^a-z0-9]+", PurePosixPath(path).with_suffix("").as_posix().lower()))
    return (
        DependencyGroup.DEVELOPMENT if tokens & DEV_REQUIREMENT_TOKENS else DependencyGroup.RUNTIME
    )


def _group_for_name(name: str, *, default: DependencyGroup) -> DependencyGroup:
    return DependencyGroup.DEVELOPMENT if name.lower() in DEV_GROUP_NAMES else default


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _strings(value: object) -> list[str]:
    if isinstance(value, list | tuple):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, set | frozenset):  # extras_require={"tests": {"pytest", ...}}
        return sorted(item for item in value if isinstance(item, str))
    if isinstance(value, str):
        return [value]
    return []


def _logical_lines(text: str) -> Iterable[str]:
    buffer = ""
    for line in text.splitlines():
        if line.rstrip().endswith("\\"):
            buffer += line.rstrip()[:-1] + " "
            continue
        yield buffer + line
        buffer = ""
    if buffer:
        yield buffer


def _cfg_list(value: str) -> list[str]:
    return [
        line.strip() for line in value.splitlines() if line.strip() and not line.startswith("#")
    ]


def _join_inside_repo(base: str, target: str) -> str | None:
    """Resolve an include relative to `base`; refuse anything that leaves the repository."""
    if target.startswith("/") or "://" in target:
        return None
    parts: list[str] = []
    # PurePosixPath already drops "." segments; only ".." needs resolving.
    for part in (*PurePosixPath(base).parent.parts, *PurePosixPath(target).parts):
        if part != "..":
            parts.append(part)
        elif parts:
            parts.pop()
        else:
            return None
    return "/".join(parts)


def setup_py_constants(tree: ast.Module) -> dict[str, ast.expr]:
    """Names bound exactly once, by a plain module-level assignment, and never changed.

    A name assigned twice, augmented (`+=`), mutated through a method (`.append(...)`) or
    bound anywhere else (a loop, a function, `global`) is left out: its value at the
    `setup()` call cannot be known without running the file.
    """
    bindings: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bindings[node.id] = bindings.get(node.id, 0) + 1
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            bindings[node.target.id] = bindings.get(node.target.id, 0) + 1
        elif isinstance(node, ast.Global | ast.Nonlocal):
            for name in node.names:
                bindings[name] = bindings.get(name, 0) + 1
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        ):
            # requires.append(...), extras.update(...): possibly mutated
            bindings[node.func.value.id] = bindings.get(node.func.value.id, 0) + 1
    constants: dict[str, ast.expr] = {}
    for statement in tree.body:
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and bindings.get(statement.targets[0].id) == 1
        ):
            constants[statement.targets[0].id] = statement.value
    return constants


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None
