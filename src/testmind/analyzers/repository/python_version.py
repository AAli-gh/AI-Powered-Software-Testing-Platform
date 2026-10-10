"""Which Python a repository needs, read from its own files without executing anything.

Sources, all at the repository root:
- `.python-version` (pyenv) and `runtime.txt` (`python-3.8.10`): a pinned version;
- `requires-python` (pyproject.toml), `python_requires` (setup.cfg, setup.py) and Poetry's
  `python` dependency: the range the project declares;
- tox's `envlist` (`py37`, `py{38,39}`) and the trove classifiers
  (`Programming Language :: Python :: 3.8`): the versions it says it is tested on.

The result only describes the project; sandbox/python_versions.py chooses the interpreter.
"""

from __future__ import annotations

import ast
import configparser
import re
import tomllib
from collections.abc import Callable, Iterable
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from testmind.analyzers.repository.dependencies import setup_py_constants
from testmind.analyzers.repository.models import Issue, PythonRequirement

ReadFile = Callable[[str], str | None]

TOX_ENV = re.compile(r"\bpy(3)(\d{1,2})\b")
CLASSIFIER = re.compile(r"^Programming Language :: Python :: (3)\.(\d{1,2})$")
BRACES = re.compile(r"\{([^{}]*)\}")
POETRY_RANGE = re.compile(r"^([\^~])\s*(\d+)\.(\d+)")


class PythonVersionDetector:
    def __init__(self, read: ReadFile) -> None:
        self._read = read
        self.issues: list[Issue] = []
        self._requires: list[tuple[str, str]] = []  # (specifier, source)
        self._pinned: tuple[str, str] | None = None
        self._tested: dict[str, list[str]] = {}  # version -> sources
        self._evidence: list[str] = []

    def detect(self, paths: Iterable[str]) -> PythonRequirement:
        root = {p for p in paths if "/" not in p}
        for name, handler in (
            (".python-version", self._python_version_file),
            ("runtime.txt", self._runtime_txt),
            ("pyproject.toml", self._pyproject),
            ("setup.cfg", self._setup_cfg),
            ("setup.py", self._setup_py),
            ("tox.ini", self._tox),
        ):
            if name in root:
                handler(name)
        requires = None
        if self._requires:
            specifier, source = self._requires[0]
            requires = specifier
            self._evidence.append(f"requires Python {specifier} ({source})")
        tested = sorted(self._tested, key=_key)
        for version in tested:
            self._evidence.append(f"tested on {version} ({', '.join(self._tested[version])})")
        return PythonRequirement(
            requires=requires,
            pinned=self._pinned[0] if self._pinned else None,
            tested=tested,
            evidence=([f"pinned to {self._pinned[0]} ({self._pinned[1]})"] if self._pinned else [])
            + self._evidence,
        )

    # ------------------------------------------------------------------ pinned
    def _python_version_file(self, path: str) -> None:
        for raw in (self._read(path) or "").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                self._pin(line, path)
                return

    def _runtime_txt(self, path: str) -> None:
        text = (self._read(path) or "").strip()
        if text.startswith("python-"):
            self._pin(text.removeprefix("python-"), path)

    def _pin(self, text: str, path: str) -> None:
        match = re.match(r"^(\d+)\.(\d+)", text)
        if match and self._pinned is None:
            self._pinned = (f"{match.group(1)}.{match.group(2)}", path)

    # ------------------------------------------------------------------ declared ranges
    def _require(self, value: object, path: str) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        try:
            SpecifierSet(value)
        except InvalidSpecifier:
            self.issues.append(
                Issue(path=path, message=f"Python requirement {value!r} is not PEP 440")
            )
            return
        self._requires.append((value.strip(), path))

    def _tested_on(self, versions: Iterable[str], source: str) -> None:
        for version in versions:
            sources = self._tested.setdefault(version, [])
            if source not in sources:
                sources.append(source)

    def _classifiers(self, values: object, path: str) -> None:
        if isinstance(values, str):
            values = values.splitlines()
        if not isinstance(values, list):
            return
        versions = []
        for value in values:
            match = CLASSIFIER.match(str(value).strip())
            if match:
                versions.append(f"{match.group(1)}.{match.group(2)}")
        self._tested_on(versions, f"{path} classifiers")

    def _pyproject(self, path: str) -> None:
        try:
            data: dict[str, Any] = tomllib.loads(self._read(path) or "")
        except tomllib.TOMLDecodeError:
            return  # reported by the dependency collector
        project = data.get("project")
        if isinstance(project, dict):
            self._require(project.get("requires-python"), path)
            self._classifiers(project.get("classifiers"), path)
        tool = data.get("tool")
        poetry = tool.get("poetry") if isinstance(tool, dict) else None
        if isinstance(poetry, dict):
            dependencies = poetry.get("dependencies")
            python = dependencies.get("python") if isinstance(dependencies, dict) else None
            if isinstance(python, str):
                self._require(_poetry_specifier(python), path)
            self._classifiers(poetry.get("classifiers"), path)

    def _setup_cfg(self, path: str) -> None:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(self._read(path) or "")
        except configparser.Error:
            return  # reported by the dependency collector
        if parser.has_option("options", "python_requires"):
            self._require(parser.get("options", "python_requires"), path)
        if parser.has_option("metadata", "classifiers"):
            self._classifiers(parser.get("metadata", "classifiers"), path)

    def _setup_py(self, path: str) -> None:
        try:
            tree = ast.parse(self._read(path) or "", filename=path)
        except (SyntaxError, ValueError):
            return  # reported by the dependency collector
        constants = setup_py_constants(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _called(node) == "setup"):
                continue
            for keyword in node.keywords:
                if keyword.arg not in {"python_requires", "classifiers"}:
                    continue
                expression = keyword.value
                if isinstance(expression, ast.Name) and expression.id in constants:
                    expression = constants[expression.id]
                try:
                    value = ast.literal_eval(expression)
                except ValueError:
                    continue
                if keyword.arg == "python_requires":
                    self._require(value, path)
                else:
                    self._classifiers(value, path)

    def _tox(self, path: str) -> None:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(self._read(path) or "")
        except configparser.Error as exc:
            self.issues.append(Issue(path=path, message=f"Invalid tox.ini: {exc}"))
            return
        if not parser.has_option("tox", "envlist"):
            return
        versions = {
            f"{m.group(1)}.{m.group(2)}"
            for env in _expand(parser.get("tox", "envlist"))
            for m in TOX_ENV.finditer(env)
        }
        self._tested_on(sorted(versions, key=_key), f"{path} envlist")


def _called(call: ast.Call) -> str | None:
    function = call.func
    if isinstance(function, ast.Attribute):
        return function.attr
    return function.id if isinstance(function, ast.Name) else None


def _expand(text: str) -> list[str]:
    """tox's brace expansion: `py{37,38}-django` is `py37-django`, `py38-django`."""
    pending = [text]
    done: list[str] = []
    while pending:
        item = pending.pop()
        match = BRACES.search(item)
        if match is None:
            done.append(item)
            continue
        for option in match.group(1).split(","):
            pending.append(item[: match.start()] + option.strip() + item[match.end() :])
    return done


def _poetry_specifier(value: str) -> str:
    """Poetry's `^3.8` / `~3.8` as PEP 440; anything else is passed on as written."""
    match = POETRY_RANGE.match(value.strip())
    if match is None:
        return value
    major, minor = int(match.group(2)), int(match.group(3))
    upper = f"{major + 1}.0" if match.group(1) == "^" else f"{major}.{minor + 1}"
    return f">={major}.{minor},<{upper}"


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))
