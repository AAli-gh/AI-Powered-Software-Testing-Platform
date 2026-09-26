"""Read-only git access through dulwich, on real repositories built by tests/gitrepo.py."""

import re
import stat
from pathlib import Path

import pytest

from testmind.analyzers.repository.limits import AnalysisLimits
from testmind.vcs import FileChange, FileStatus, GitError, GitRepository
from tests.gitrepo import make_git_repo


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, list[str]]:
    root = tmp_path / "repo"
    shas = make_git_repo(
        root,
        [
            ("first", {"app/core.py": "x = 1\n", "README.md": "hi\n", "old.txt": "old\n"}),
            ("second", {"app/core.py": "x = 2\n", "app/new.py": "y = 1\n", "old.txt": None}),
            ("third\n\nbody", {"README.md": "hello\n"}),
        ],
        tags={"v1": 0},
    )
    return root, shas


def test_revisions(repo: tuple[Path, list[str]]) -> None:
    root, shas = repo
    git = GitRepository(root)
    assert git.resolve("HEAD").sha == shas[2]
    assert git.resolve("main").sha == shas[2]
    assert git.resolve("main~2").sha == shas[0]
    assert git.resolve("HEAD~1").sha == shas[1]
    assert git.resolve("v1").sha == shas[0]
    assert git.resolve(shas[1][:8]).sha == shas[1]
    head = git.resolve("HEAD")
    assert (head.ref, head.subject, head.author) == (
        "HEAD",
        "third",
        "TestMind Tests <tests@example.com>",
    )
    assert head.timestamp == 1_700_000_120


@pytest.mark.parametrize(
    ("revision", "message"),
    [
        ("nope", "Unknown revision 'nope'"),
        ("HEAD~3", "goes back past the first commit"),
        ("main..HEAD", "Invalid revision"),
        ("-rf", "Unknown revision"),
        ("a b", "Invalid revision"),
        ("x" * 300, "Invalid revision"),
    ],
)
def test_bad_revisions(repo: tuple[Path, list[str]], revision: str, message: str) -> None:
    root, _ = repo
    with pytest.raises(GitError, match=message):
        GitRepository(root).resolve(revision)


def test_changes_and_reads(repo: tuple[Path, list[str]]) -> None:
    root, shas = repo
    git = GitRepository(root)
    assert git.changes(shas[0], shas[2]) == [
        FileChange("README.md", FileStatus.MODIFIED),
        FileChange("app/core.py", FileStatus.MODIFIED),
        FileChange("app/new.py", FileStatus.ADDED),
        FileChange("old.txt", FileStatus.DELETED),
    ]
    assert git.read(shas[0], "app/core.py") == "x = 1\n"
    assert git.read(shas[2], "app/core.py") == "x = 2\n"
    assert git.read(shas[2], "old.txt") is None
    assert git.read(shas[2], "app") is None  # a directory
    git.close()


def test_materialize_writes_regular_files_only(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    [sha] = make_git_repo(
        root,
        [
            (
                "odd",
                {
                    "pkg/ok.py": "ok = 1\n",
                    "link": (stat.S_IFLNK, b"/etc/passwd"),
                    "big.bin": (0o100644, b"x" * 50),
                    "evil/..": (0o100644, b"escape"),  # a tree entry literally named ".."
                },
            )
        ],
    )
    git = GitRepository(root, AnalysisLimits(max_read_bytes=20))
    out = tmp_path / "snapshot"
    out.mkdir()
    snapshot = git.materialize(sha, out)

    assert (out / "pkg" / "ok.py").read_text() == "ok = 1\n"
    assert not (out / "link").exists()
    assert not (out / "big.bin").exists()
    assert snapshot.files == 1
    assert sorted(snapshot.skipped) == [
        "big.bin: too large",
        "evil/..: unsafe path",
        "link: symbolic link",
    ]
    assert stat.S_IMODE((out / "pkg" / "ok.py").stat().st_mode) == 0o644
    assert stat.S_IMODE((out / "pkg").stat().st_mode) == 0o755
    assert git.read(sha, "big.bin") is None  # over the read limit


def test_file_limit(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    [sha] = make_git_repo(root, [("many", {f"f{n}.py": "" for n in range(3)})])
    out = tmp_path / "out"
    out.mkdir()
    snapshot = GitRepository(root, AnalysisLimits(max_files=2)).materialize(sha, out)
    assert snapshot.files == 2
    assert snapshot.skipped == ("f2.py: file limit reached",)


def test_redirecting_layouts_are_refused(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(GitError, match="Not a git repository"):
        GitRepository(plain)

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /somewhere/else\n")
    with pytest.raises(GitError, match="worktrees and submodules are not supported"):
        GitRepository(worktree)

    linked = tmp_path / "linked"
    linked.mkdir()
    make_git_repo(tmp_path / "real", [("c", {"a": "1"})])
    (linked / ".git").symlink_to(tmp_path / "real" / ".git")
    with pytest.raises(GitError, match=r"a \.git directory is required"):
        GitRepository(linked)

    for redirect in ("objects/info/alternates", "commondir"):
        root = tmp_path / redirect.replace("/", "_")
        make_git_repo(root, [("c", {"a": "1"})])
        target = root / ".git" / redirect
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("/elsewhere\n")
        with pytest.raises(GitError, match=re.escape(f"layout: .git/{redirect} is present")):
            GitRepository(root)

    broken = tmp_path / "broken"
    (broken / ".git").mkdir(parents=True)
    with pytest.raises(GitError, match="Not a git repository"):
        GitRepository(broken)


def test_objects_that_are_not_commits_are_refused(
    repo: tuple[Path, list[str]], tmp_path: Path
) -> None:
    root, shas = repo
    git = GitRepository(root)
    tree = git._commit(shas[0]).tree.decode()  # a tree, not a commit
    with pytest.raises(GitError, match="is not a commit"):
        git.materialize(tree, tmp_path)
