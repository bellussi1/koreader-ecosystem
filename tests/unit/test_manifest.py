from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from koreader_ecosystem.manifest import (
    ManifestError,
    build_manifest,
    compare_manifests,
)


def _set_mtime(path: Path, value: int) -> None:
    os.utime(path, ns=(value, value))


def test_manifest_is_sorted_hashes_files_and_keeps_empty_directories(tmp_path: Path) -> None:
    (tmp_path / "z.txt").write_bytes(b"z")
    (tmp_path / "a.txt").write_bytes(b"hello")
    (tmp_path / "empty").mkdir()

    entries = build_manifest(tmp_path)

    assert tuple(entry.path for entry in entries) == ("a.txt", "empty", "z.txt")
    by_path = {entry.path: entry for entry in entries}
    assert by_path["a.txt"].kind == "file"
    assert by_path["a.txt"].size == 5
    assert (
        by_path["a.txt"].sha256
        == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )
    assert by_path["a.txt"].mtime_ns is not None
    assert by_path["empty"].kind == "directory"
    assert by_path["empty"].size == 0
    assert by_path["empty"].sha256 is None


def test_manifest_comparison_reports_removed_added_and_changed_paths(tmp_path: Path) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    before_same = before / "same.txt"
    after_same = after / "same.txt"
    before_changed = before / "changed.txt"
    after_changed = after / "changed.txt"
    before_same.write_text("same", encoding="utf-8")
    before_changed.write_text("old", encoding="utf-8")
    (before / "removed.txt").write_text("gone", encoding="utf-8")
    after_same.write_text("same", encoding="utf-8")
    after_changed.write_text("new", encoding="utf-8")
    (after / "added.txt").write_text("new", encoding="utf-8")
    fixed_time = 1_700_000_000_000_000_000
    for path in (before_same, after_same, before_changed, after_changed):
        _set_mtime(path, fixed_time)

    diff = compare_manifests(build_manifest(before), build_manifest(after))

    assert diff.missing == ("removed.txt",)
    assert diff.extra == ("added.txt",)
    assert diff.changed == ("changed.txt",)
    assert diff.matches is False


def test_manifest_comparison_matches_identical_trees(tmp_path: Path) -> None:
    source = tmp_path / "source"
    restore = tmp_path / "restore"
    source.mkdir()
    restore.mkdir()
    source_file = source / "book.txt"
    restore_file = restore / "book.txt"
    source_file.write_bytes(b"same bytes")
    restore_file.write_bytes(b"same bytes")
    fixed_time = 1_700_000_000_000_000_000
    _set_mtime(source_file, fixed_time)
    _set_mtime(restore_file, fixed_time)

    assert compare_manifests(build_manifest(source), build_manifest(restore)).matches is True


def test_manifest_rejects_a_missing_root(tmp_path: Path) -> None:
    with pytest.raises(ManifestError):
        build_manifest(tmp_path / "missing")


def test_manifest_rejects_a_junction(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    junction = tmp_path / "junction"
    result = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("Windows did not allow a temporary directory junction")

    try:
        assert junction.is_junction()
        with pytest.raises(ManifestError):
            build_manifest(tmp_path)
    finally:
        junction.rmdir()


def test_manifest_rejects_symlinks_when_windows_allows_creation(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("content", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Windows does not permit symlink creation here: {exc}")

    with pytest.raises(ManifestError):
        build_manifest(tmp_path)
