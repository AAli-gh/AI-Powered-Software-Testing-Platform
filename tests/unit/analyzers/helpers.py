from collections.abc import Mapping
from pathlib import Path

from testmind.analyzers.repository import RepositoryAnalyzer, RepositoryProfile


def make_repo(root: Path, files: Mapping[str, str | bytes]) -> Path:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
    return root


def analyze(root: Path, files: Mapping[str, str | bytes]) -> RepositoryProfile:
    return RepositoryAnalyzer().analyze(make_repo(root, files))
