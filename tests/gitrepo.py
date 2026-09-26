"""Build real git repositories for tests, from git objects.

dulwich's porcelain `commit` reads the user's global git configuration (commit signing, for
example). Tests must not depend on that, so commits are assembled from blobs, trees and
commit objects directly: the result is an ordinary repository, byte-for-byte deterministic.
"""

from __future__ import annotations

import stat
from collections.abc import Mapping
from pathlib import Path

from dulwich.objects import Blob, Commit, ObjectID, Tree
from dulwich.refs import Ref
from dulwich.repo import Repo

Content = str | tuple[int, bytes]  # text, or (mode, raw bytes) for symlinks and oddities
AUTHOR = b"TestMind Tests <tests@example.com>"


def make_git_repo(
    root: Path,
    history: list[tuple[str, Mapping[str, Content | None]]],
    *,
    branch: str = "main",
    tags: Mapping[str, int] | None = None,
) -> list[str]:
    """Create `root` as a repository with one commit per (message, changes) entry.

    Changes are applied cumulatively: a path maps to its new content, or None to delete it.
    The working tree is left empty; TestMind reads objects only. Returns the commit shas.
    """
    root.mkdir(parents=True, exist_ok=True)
    repo = Repo.init(str(root))
    files: dict[str, Content] = {}
    shas: list[ObjectID] = []
    for index, (message, changes) in enumerate(history):
        for path, content in changes.items():
            if content is None:
                files.pop(path, None)
            else:
                files[path] = content
        commit = Commit()
        commit.tree = _tree(repo, files).id
        commit.parents = shas[-1:]
        commit.author = commit.committer = AUTHOR
        commit.author_time = commit.commit_time = 1_700_000_000 + index * 60
        commit.author_timezone = commit.commit_timezone = 0
        commit.encoding = b"UTF-8"
        commit.message = message.encode() + b"\n"
        repo.object_store.add_object(commit)
        shas.append(commit.id)
    repo.refs[Ref(f"refs/heads/{branch}".encode())] = shas[-1]
    repo.refs.set_symbolic_ref(Ref(b"HEAD"), Ref(f"refs/heads/{branch}".encode()))
    for name, index in (tags or {}).items():
        repo.refs[Ref(f"refs/tags/{name}".encode())] = shas[index]
    repo.close()
    return [sha.decode() for sha in shas]


def _tree(repo: Repo, files: Mapping[str, Content]) -> Tree:
    nested: dict[str, object] = {}
    for path, content in files.items():
        parts = path.split("/")
        level = nested
        for part in parts[:-1]:
            level = level.setdefault(part, {})  # type: ignore[assignment]
        level[parts[-1]] = content
    return _build(repo, nested)


def _build(repo: Repo, level: Mapping[str, object]) -> Tree:
    tree = Tree()
    for name, value in level.items():
        if isinstance(value, dict):
            tree.add(name.encode(), stat.S_IFDIR, _build(repo, value).id)
            continue
        mode, raw = value if isinstance(value, tuple) else (0o100644, str(value).encode())
        blob = Blob.from_string(raw)
        repo.object_store.add_object(blob)
        tree.add(name.encode(), mode, blob.id)
    repo.object_store.add_object(tree)
    return tree
