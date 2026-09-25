import os
from pathlib import Path

import pytest

from testmind.core.errors import InvalidSourceError
from testmind.services.source_locator import resolve_local_source, to_stored_location


def test_relative_location_resolves_inside_root(repos_root: Path) -> None:
    assert resolve_local_source(repos_root, "sample") == repos_root / "sample"


def test_absolute_location_inside_root_is_accepted(repos_root: Path) -> None:
    assert resolve_local_source(repos_root, str(repos_root / "sample")) == repos_root / "sample"


def test_nested_directory_is_accepted(repos_root: Path) -> None:
    (repos_root / "sample" / "pkg").mkdir()
    assert resolve_local_source(repos_root, "sample/pkg") == repos_root / "sample" / "pkg"


def test_redundant_segments_are_normalised(repos_root: Path) -> None:
    assert resolve_local_source(repos_root, "./sample/../sample/") == repos_root / "sample"


@pytest.mark.parametrize("location", ["../", "sample/../../", "../../etc", "/etc", "/"])
def test_paths_escaping_the_root_are_rejected(repos_root: Path, location: str) -> None:
    with pytest.raises(InvalidSourceError):
        resolve_local_source(repos_root, location)


def test_sibling_directory_with_common_prefix_is_rejected(repos_root: Path) -> None:
    # "/tmp/x/repos-evil" starts with the string "/tmp/x/repos" but is not inside it.
    evil = repos_root.parent / (repos_root.name + "-evil")
    evil.mkdir()
    with pytest.raises(InvalidSourceError, match="inside the repositories root"):
        resolve_local_source(repos_root, str(evil))


def test_symlink_pointing_outside_root_is_rejected(repos_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (repos_root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InvalidSourceError, match="inside the repositories root"):
        resolve_local_source(repos_root, "link")


def test_symlink_inside_root_is_followed(repos_root: Path) -> None:
    (repos_root / "alias").symlink_to(repos_root / "sample", target_is_directory=True)
    assert resolve_local_source(repos_root, "alias") == repos_root / "sample"


@pytest.mark.parametrize("location", [".", "", str(Path("x").resolve().anchor)])
def test_root_itself_or_empty_is_rejected(repos_root: Path, location: str) -> None:
    with pytest.raises(InvalidSourceError):
        resolve_local_source(repos_root, location)


def test_missing_directory_is_rejected(repos_root: Path) -> None:
    with pytest.raises(InvalidSourceError, match="does not exist"):
        resolve_local_source(repos_root, "missing")


def test_file_is_rejected(repos_root: Path) -> None:
    with pytest.raises(InvalidSourceError, match="not a directory"):
        resolve_local_source(repos_root, "sample/main.py")


def test_null_byte_is_rejected(repos_root: Path) -> None:
    with pytest.raises(InvalidSourceError, match="invalid characters"):
        resolve_local_source(repos_root, "sample\x00")


def test_symlink_loop_is_rejected(repos_root: Path) -> None:
    (repos_root / "loop").symlink_to(repos_root / "loop")
    with pytest.raises(InvalidSourceError):
        resolve_local_source(repos_root, "loop")


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permission checks")
def test_unreadable_parent_is_rejected(repos_root: Path) -> None:
    locked = repos_root / "locked"
    (locked / "inner").mkdir(parents=True)
    locked.chmod(0)
    try:
        with pytest.raises(InvalidSourceError):
            resolve_local_source(repos_root, "locked/inner")
    finally:
        locked.chmod(0o755)


def test_stored_location_is_relative_posix(repos_root: Path) -> None:
    (repos_root / "sample" / "pkg").mkdir()
    resolved = resolve_local_source(repos_root, str(repos_root / "sample" / "pkg"))
    assert to_stored_location(repos_root, resolved) == "sample/pkg"
