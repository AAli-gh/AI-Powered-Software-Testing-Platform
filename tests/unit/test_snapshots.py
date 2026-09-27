from pathlib import Path

import pytest

from testmind.analyzers.repository.limits import AnalysisLimits
from testmind.vcs import FileChange, FileStatus, GitError, Snapshot
from testmind.vcs.snapshots import SnapshotSource
from tests.unit.analyzers.helpers import make_repo


@pytest.fixture
def source(tmp_path: Path) -> SnapshotSource:
    base = make_repo(
        tmp_path / "base", {"a.py": "a\n", "b.py": "b\n", "c.py": "c\n", "big.py": "x"}
    )
    head = make_repo(
        tmp_path / "head",
        {"a.py": "a\n", "b.py": "B\n", "d.py": "d\n", "big.py": "y" * 50, "pkg/e.py": "e"},
    )
    (head / "link.py").symlink_to(head / "a.py")
    return SnapshotSource(
        {"base": Snapshot(base, 4, ()), "head": Snapshot(head, 5, ("x: skipped",))},
        AnalysisLimits(max_read_bytes=20),
    )


def test_changes_between_the_two_trees(source: SnapshotSource) -> None:
    assert source.changes("base", "head") == [
        FileChange("b.py", FileStatus.MODIFIED),
        FileChange("big.py", FileStatus.MODIFIED),  # same name, different size
        FileChange("c.py", FileStatus.DELETED),
        FileChange("d.py", FileStatus.ADDED),
        FileChange("pkg/e.py", FileStatus.ADDED),
    ]  # the symbolic link is not a file of the commit


def test_reading_like_git(source: SnapshotSource) -> None:
    assert source.read("head", "b.py") == "B\n"
    assert source.read("head", "c.py") is None
    assert source.read("head", "big.py") is None  # over the read limit
    assert source.read("head", "link.py") is None
    assert source.read("head", "pkg") is None


def test_materialize_copies(source: SnapshotSource, tmp_path: Path) -> None:
    copy = source.materialize("head", tmp_path / "copy")
    assert (copy.files, copy.skipped) == (5, ("x: skipped",))
    assert (tmp_path / "copy" / "pkg" / "e.py").read_text() == "e"
    with pytest.raises(GitError, match="No snapshot of commit"):
        source.materialize("other", tmp_path / "x")
    with pytest.raises(GitError, match="No snapshot of commit"):
        source.read("other", "a.py")


def test_same_size_different_content(tmp_path: Path) -> None:
    base = make_repo(tmp_path / "b", {"a.py": "1"})
    head = make_repo(tmp_path / "h", {"a.py": "2"})
    source = SnapshotSource({"b": Snapshot(base, 1, ()), "h": Snapshot(head, 1, ())})
    assert source.changes("b", "h") == [FileChange("a.py", FileStatus.MODIFIED)]
