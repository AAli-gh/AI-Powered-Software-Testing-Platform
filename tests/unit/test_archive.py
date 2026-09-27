import io
import tarfile
from pathlib import Path

import pytest

from testmind.analyzers.repository.limits import AnalysisLimits
from testmind.vcs.archive import ArchiveError, extract_archive


def archive(tmp_path: Path, members: list[tuple[str, bytes | None]]) -> Path:
    """(name, content); content None is a directory."""
    path = tmp_path / "a.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, content in members:
            info = tarfile.TarInfo(name)
            if content is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
    return path


def test_regular_files_below_the_top_directory(tmp_path: Path) -> None:
    source = archive(
        tmp_path,
        [("top", None), ("top/pkg", None), ("top/pkg/a.py", b"a"), ("pax_global_header", b"x")],
    )
    snapshot = extract_archive(source, tmp_path / "out")
    assert (snapshot.files, snapshot.skipped) == (1, ())
    target = tmp_path / "out" / "pkg" / "a.py"
    assert target.read_bytes() == b"a"
    assert oct(target.stat().st_mode)[-3:] == "644"
    assert oct((tmp_path / "out" / "pkg").stat().st_mode)[-3:] == "755"


def test_duplicates_conflicts_and_the_file_limit(tmp_path: Path) -> None:
    source = archive(
        tmp_path,
        [
            ("top/a.py", b"first"),
            ("top/a.py", b"second"),  # the same path twice: the first one is kept
            ("top/a.py/b.py", b"under a file"),
            ("top/c.py", b"c"),
            ("top/d.py", b"d"),
        ],
    )
    snapshot = extract_archive(source, tmp_path / "out", AnalysisLimits(max_files=2))
    assert snapshot.files == 2
    assert snapshot.skipped == (
        "a.py: duplicate or conflicting entry",
        "a.py/b.py: duplicate or conflicting entry",
        "d.py: file limit reached",
    )
    assert (tmp_path / "out" / "a.py").read_bytes() == b"first"


def test_not_an_archive(tmp_path: Path) -> None:
    broken = tmp_path / "broken.tar.gz"
    broken.write_bytes(b"not a tarball")
    with pytest.raises(ArchiveError, match="cannot be read"):
        extract_archive(broken, tmp_path / "out")


def test_devices_and_fifos_are_not_written(tmp_path: Path) -> None:
    path = tmp_path / "a.tar"
    with tarfile.open(path, "w") as tar:
        fifo = tarfile.TarInfo("top/pipe")
        fifo.type = tarfile.FIFOTYPE
        tar.addfile(fifo)
    snapshot = extract_archive(path, tmp_path / "out")
    assert (snapshot.files, snapshot.skipped) == (0, ("pipe: not a regular file",))
