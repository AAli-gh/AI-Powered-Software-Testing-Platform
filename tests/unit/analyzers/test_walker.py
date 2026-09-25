import os
from pathlib import Path

import pytest

from testmind.analyzers.repository.limits import AnalysisLimits
from testmind.analyzers.repository.walker import WalkedFile, read_text, walk_repository
from tests.unit.analyzers.helpers import make_repo


def paths(root: Path, limits: AnalysisLimits | None = None) -> list[str]:
    return [f.relative for f in walk_repository(root, limits or AnalysisLimits()).files]


def test_lists_regular_files_sorted(tmp_path: Path) -> None:
    make_repo(tmp_path, {"b.py": "", "a/c.py": "", "a/b/d.txt": ""})
    assert paths(tmp_path) == ["a/b/d.txt", "a/c.py", "b.py"]


@pytest.mark.parametrize(
    "skipped",
    [".git/config", "node_modules/x.js", ".venv/lib.py", "pkg/__pycache__/m.pyc",
     "build/out.py", "dist/w.whl", "pkg.egg-info/PKG-INFO", ".tox/log"],
)  # fmt: skip
def test_skips_vendored_and_generated_directories(tmp_path: Path, skipped: str) -> None:
    make_repo(tmp_path, {skipped: "x", "keep.py": ""})
    assert paths(tmp_path) == ["keep.py"]


def test_honours_root_gitignore(tmp_path: Path) -> None:
    make_repo(
        tmp_path,
        {
            ".gitignore": "*.log\nsecret/\n!important.log\n",
            "app.log": "",
            "important.log": "",
            "secret/key.pem": "",
            "src/app.py": "",
        },
    )
    assert paths(tmp_path) == [".gitignore", "important.log", "src/app.py"]


def test_symlinks_are_never_followed_or_listed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    make_repo(outside, {"passwd": "root:x:0:0"})
    repo = make_repo(tmp_path / "repo", {"main.py": ""})
    (repo / "linked_dir").symlink_to(outside, target_is_directory=True)
    (repo / "linked_file").symlink_to(outside / "passwd")
    assert paths(repo) == ["main.py"]


def test_max_files_truncates(tmp_path: Path) -> None:
    make_repo(tmp_path, {f"f{i}.py": "" for i in range(5)})
    result = walk_repository(tmp_path, AnalysisLimits(max_files=3))
    assert result.truncated is True
    assert len(result.files) == 3


def test_not_truncated_at_exact_limit(tmp_path: Path) -> None:
    make_repo(tmp_path, {f"f{i}.py": "" for i in range(3)})
    assert walk_repository(tmp_path, AnalysisLimits(max_files=3)).truncated is False


def test_max_depth(tmp_path: Path) -> None:
    make_repo(tmp_path, {"a/b/c/deep.py": "", "a/shallow.py": ""})
    assert paths(tmp_path, AnalysisLimits(max_depth=1)) == ["a/shallow.py"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permission checks")
def test_unreadable_directory_is_skipped(tmp_path: Path) -> None:
    make_repo(tmp_path, {"locked/x.py": "", "ok.py": ""})
    (tmp_path / "locked").chmod(0)
    try:
        assert paths(tmp_path) == ["ok.py"]
    finally:
        (tmp_path / "locked").chmod(0o755)


def walked(path: Path) -> WalkedFile:
    return WalkedFile(path, path.name, path.stat().st_size)


def test_read_text_decodes_utf8_with_replacement(tmp_path: Path) -> None:
    file = tmp_path / "f.py"
    file.write_bytes("héllo".encode() + b"\xff")
    assert read_text(walked(file), AnalysisLimits()) == "héllo\ufffd"


def test_read_text_refuses_binary(tmp_path: Path) -> None:
    file = tmp_path / "f.bin"
    file.write_bytes(b"\x89PNG\x00\x00")
    assert read_text(walked(file), AnalysisLimits()) is None


def test_read_text_refuses_large_files(tmp_path: Path) -> None:
    file = tmp_path / "big.py"
    file.write_text("x" * 101)
    assert read_text(walked(file), AnalysisLimits(max_read_bytes=100)) is None


def test_read_text_refuses_file_that_grew_after_listing(tmp_path: Path) -> None:
    file = tmp_path / "f.py"
    file.write_text("x")
    entry = walked(file)
    file.write_text("x" * 200)
    assert read_text(entry, AnalysisLimits(max_read_bytes=100)) is None


def test_read_text_refuses_file_swapped_for_symlink(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_text("token")
    file = tmp_path / "f.py"
    file.write_text("x")
    entry = walked(file)
    file.unlink()
    file.symlink_to(secret)
    assert read_text(entry, AnalysisLimits()) is None


def test_gitignore_that_is_a_symlink_is_ignored(tmp_path: Path) -> None:
    make_repo(tmp_path, {"real_ignore": "*.py\n", "a.py": ""})
    (tmp_path / ".gitignore").symlink_to(tmp_path / "real_ignore")
    assert "a.py" in paths(tmp_path)


def test_special_files_are_ignored(tmp_path: Path) -> None:
    make_repo(tmp_path, {"a.py": ""})
    os.mkfifo(tmp_path / "pipe")
    assert paths(tmp_path) == ["a.py"]


def test_entry_that_vanishes_during_traversal_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_repo(tmp_path, {"a.py": "", "gone.py": ""})
    real_scandir = os.scandir

    class Vanishing:
        def __init__(self, entry: os.DirEntry[str]) -> None:
            self._entry = entry
            self.name, self.path = entry.name, entry.path

        def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
            if self.name == "gone.py":
                raise FileNotFoundError(self.path)
            return self._entry.stat(follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "scandir", lambda d: [Vanishing(e) for e in real_scandir(d)])
    assert paths(tmp_path) == ["a.py"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permission checks")
def test_unreadable_gitignore_is_ignored(tmp_path: Path) -> None:
    make_repo(tmp_path, {".gitignore": "*.py\n", "a.py": ""})
    (tmp_path / ".gitignore").chmod(0)
    try:
        assert "a.py" in paths(tmp_path)
    finally:
        (tmp_path / ".gitignore").chmod(0o644)
