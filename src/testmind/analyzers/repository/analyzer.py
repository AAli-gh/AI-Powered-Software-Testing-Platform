"""Orchestrates the repository analysis. Pure function of the files on disk: no network, no
imports of analysed code, no subprocesses."""

from __future__ import annotations

import configparser
import tomllib
from pathlib import Path, PurePosixPath

from testmind.analyzers.repository.dependencies import (
    MANIFEST_NAMES,
    DependencyCollector,
    ReadFile,
    is_dependency_manifest,
)
from testmind.analyzers.repository.entry_points import find_entry_points
from testmind.analyzers.repository.frameworks import (
    FrameworkInputs,
    detect_frameworks,
    is_test_file,
)
from testmind.analyzers.repository.infrastructure import analyse_ci, analyse_docker, is_config_file
from testmind.analyzers.repository.languages import detect_language, summarise_languages
from testmind.analyzers.repository.limits import AnalysisLimits
from testmind.analyzers.repository.models import FileEntry, Issue, RepositoryProfile
from testmind.analyzers.repository.python_scanner import PythonFileFacts, scan_python
from testmind.analyzers.repository.walker import WalkedFile, read_text, walk_repository

# Bump when the output changes meaningfully; stored with every analysis.
ANALYZER_VERSION = "1.1.0"


class RepositoryAnalyzer:
    def __init__(self, limits: AnalysisLimits | None = None) -> None:
        self._limits = limits or AnalysisLimits()

    def analyze(self, root: Path) -> RepositoryProfile:
        walked = walk_repository(root, self._limits)
        by_path = {f.relative: f for f in walked.files}
        cache: dict[str, str | None] = {}

        def read(path: str) -> str | None:
            if path not in cache:
                file = by_path.get(path)
                cache[path] = read_text(file, self._limits) if file else None
            return cache[path]

        issues: list[Issue] = []
        python_facts = self._scan_python(walked.files, read, issues)
        test_files = {f.path for f in python_facts if is_test_file(f)}

        files = [
            FileEntry(
                path=f.relative,
                size_bytes=f.size_bytes,
                language=(language := detect_language(f.relative)),
                lines=_count_lines(read(f.relative)) if language else None,
                is_test=f.relative in test_files,
            )
            for f in walked.files
        ]
        languages, primary = summarise_languages(files)

        paths = [f.relative for f in walked.files]
        collector = DependencyCollector(read)
        dependencies = collector.collect(p for p in paths if is_dependency_manifest(p))
        issues.extend(collector.issues)

        frameworks, test_frameworks = detect_frameworks(
            FrameworkInputs(
                dependencies=dependencies,
                python_files=python_facts,
                test_files=test_files,
                all_paths=paths,
                pytest_config_sections=_pytest_config_sections(paths, read),
            )
        )

        manifests = [p for p in paths if PurePosixPath(p).name in MANIFEST_NAMES]
        return RepositoryProfile(
            file_count=len(files),
            total_size_bytes=sum(f.size_bytes for f in files),
            truncated=walked.truncated,
            languages=languages,
            primary_language=primary,
            dependencies=sorted(dependencies, key=lambda d: (d.group.value, d.name, d.source)),
            frameworks=frameworks,
            test_frameworks=test_frameworks,
            test_files=sorted(test_files),
            entry_points=find_entry_points(python_facts, test_files, manifests, read),
            config_files=sorted(p for p in paths if is_config_file(p)),
            docker=analyse_docker(paths, read, issues),
            ci=analyse_ci(paths, read, issues),
            files=files,
            issues=sorted(issues, key=lambda i: (i.path, i.message)),
            django_settings_module=_django_settings(python_facts),
        )

    def _scan_python(
        self,
        files: list[WalkedFile],
        read: ReadFile,
        issues: list[Issue],
    ) -> list[PythonFileFacts]:
        facts: list[PythonFileFacts] = []
        for file in files:
            if detect_language(file.relative) != "Python" or file.relative.endswith(".pyi"):
                continue
            source = read(file.relative)
            if source is None:
                issues.append(
                    Issue(path=file.relative, message="Not analysed (too large or binary)")
                )
                continue
            scanned = scan_python(file.relative, source)
            if scanned.syntax_error:
                issues.append(
                    Issue(path=file.relative, message=f"Syntax error: {scanned.syntax_error}")
                )
            facts.append(scanned)
        return facts


def _count_lines(text: str | None) -> int | None:
    if text is None:
        return None
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


def _pytest_config_sections(paths: list[str], read: ReadFile) -> list[str]:
    sections: list[str] = []
    for path in paths:
        name = PurePosixPath(path).name
        text = read(path) if name in {"pyproject.toml", "setup.cfg", "tox.ini"} else None
        if text is None:
            continue
        if name == "pyproject.toml":
            try:
                tool = tomllib.loads(text).get("tool", {})
            except tomllib.TOMLDecodeError:
                continue
            if isinstance(tool, dict) and "pytest" in tool:
                sections.append(f"{path} [tool.pytest]")
        else:
            parser = configparser.ConfigParser(interpolation=None)
            try:
                parser.read_string(text)
            except configparser.Error:
                continue
            section = "tool:pytest" if name == "setup.cfg" else "pytest"
            if parser.has_section(section):
                sections.append(f"{path} [{section}]")
    return sections


def _django_settings(python_facts: list[PythonFileFacts]) -> str | None:
    """manage.py is authoritative; otherwise the first module that sets the variable."""
    values = sorted(
        (PurePosixPath(f.path).name != "manage.py", f.path, f.django_settings_value)
        for f in python_facts
        if f.django_settings_value
    )
    return values[0][2] if values else None
