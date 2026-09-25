import pytest

from testmind.analyzers.repository.languages import detect_language, summarise_languages
from testmind.analyzers.repository.models import FileEntry


@pytest.mark.parametrize(
    ("path", "language"),
    [
        ("a/b.py", "Python"), ("stubs.pyi", "Python"), ("x.TS", "TypeScript"),
        ("Dockerfile", "Dockerfile"), ("Dockerfile.prod", "Dockerfile"),
        ("api.dockerfile", "Dockerfile"), ("Makefile", "Makefile"), ("ci/Jenkinsfile", "Groovy"),
        ("README.md", "Markdown"), ("LICENSE", None), ("image.png", None),
    ],
)  # fmt: skip
def test_detect_language(path: str, language: str | None) -> None:
    assert detect_language(path) == language


def entry(path: str, lines: int | None, size: int = 10) -> FileEntry:
    return FileEntry(path=path, size_bytes=size, language=detect_language(path), lines=lines)


def test_summary_percentages_and_order() -> None:
    stats, primary = summarise_languages(
        [entry("a.py", 60), entry("b.py", 15), entry("c.js", 25), entry("logo.png", None)]
    )
    assert [(s.name, s.files, s.lines, s.percentage) for s in stats] == [
        ("Python", 2, 75, 75.0),
        ("JavaScript", 1, 25, 25.0),
    ]
    assert primary == "Python"


def test_markup_never_becomes_primary_language() -> None:
    _, primary = summarise_languages([entry("docs.md", 1000), entry("x.sh", 5)])
    assert primary == "Shell"


def test_empty_and_unreadable_files() -> None:
    stats, primary = summarise_languages([entry("big.py", None)])
    assert stats[0].percentage == 0.0
    assert primary is None
    assert summarise_languages([]) == ([], None)
