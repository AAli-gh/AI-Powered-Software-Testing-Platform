"""Language identification by file name / extension (deterministic, no content sniffing)."""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

from testmind.analyzers.repository.models import FileEntry, LanguageStat

EXTENSIONS: dict[str, str] = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".scala": "Scala",
    ".rb": "Ruby", ".php": "PHP", ".cs": "C#", ".c": "C", ".h": "C",
    ".cpp": "C++", ".cc": "C++", ".hpp": "C++", ".swift": "Swift",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".sql": "SQL", ".md": "Markdown", ".rst": "reStructuredText",
    ".yml": "YAML", ".yaml": "YAML", ".toml": "TOML", ".json": "JSON",
    ".ini": "INI", ".cfg": "INI",
}  # fmt: skip

FILENAMES: dict[str, str] = {
    "Dockerfile": "Dockerfile",
    "Makefile": "Makefile",
    "Jenkinsfile": "Groovy",
}

# Data / markup formats are listed per file but do not compete for "primary language".
NON_PROGRAMMING = frozenset(
    {"Markdown", "reStructuredText", "YAML", "TOML", "JSON", "INI", "HTML", "CSS", "SCSS"}
)


def detect_language(relative_path: str) -> str | None:
    path = PurePosixPath(relative_path)
    if path.name in FILENAMES:
        return FILENAMES[path.name]
    if path.name.startswith("Dockerfile") or path.suffix == ".dockerfile":
        return "Dockerfile"
    return EXTENSIONS.get(path.suffix.lower())


def summarise_languages(files: list[FileEntry]) -> tuple[list[LanguageStat], str | None]:
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # files, lines, bytes
    for entry in files:
        if entry.language is None:
            continue
        bucket = counts[entry.language]
        bucket[0] += 1
        bucket[1] += entry.lines or 0
        bucket[2] += entry.size_bytes

    total_lines = sum(lines for _, lines, _ in counts.values())
    stats = [
        LanguageStat(
            name=name,
            files=n_files,
            lines=lines,
            bytes=size,
            percentage=round(100 * lines / total_lines, 2) if total_lines else 0.0,
        )
        for name, (n_files, lines, size) in counts.items()
    ]
    stats.sort(key=lambda s: (-s.lines, -s.files, s.name))
    primary = next((s.name for s in stats if s.name not in NON_PROGRAMMING and s.lines), None)
    return stats, primary
