import textwrap
from pathlib import Path

from testmind.analyzers.code import CodeAnalyzer, CodeProfile
from testmind.analyzers.code.models import Finding


def analyze_source(tmp_path: Path, source: str, path: str = "pkg/mod.py") -> CodeProfile:
    file = tmp_path / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(textwrap.dedent(source))
    return CodeAnalyzer().analyze(tmp_path, [path])


def rule_ids(tmp_path: Path, source: str, path: str = "pkg/mod.py") -> list[str]:
    return [f.rule_id for f in analyze_source(tmp_path, source, path).findings]


def findings(tmp_path: Path, source: str, rule: str) -> list[Finding]:
    return [f for f in analyze_source(tmp_path, source).findings if f.rule_id == rule]
