from __future__ import annotations

from pathlib import Path

import pytest

from koreader_ecosystem.safety import (
    SafetyViolation,
    remove_tree,
    require_calibre_closed,
    validate_restore_target,
)


@pytest.mark.parametrize(
    "process_name",
    [
        "calibre.exe",
        "CALIBRE.EXE",
        r"C:\Program Files\Calibre2\calibre.exe",
        "calibre-server.exe",
        "calibre-parallel.exe",
    ],
)
def test_requires_calibre_to_be_closed(process_name: str) -> None:
    with pytest.raises(SafetyViolation):
        require_calibre_closed([process_name])


def test_allows_backup_when_only_other_processes_are_running() -> None:
    require_calibre_closed(["python.exe", "explorer.exe"])


def test_allows_restore_to_a_new_unrelated_directory(tmp_path: Path) -> None:
    source = tmp_path / "library"
    source.mkdir()

    validate_restore_target([source], tmp_path / "restore")


def test_rejects_an_existing_restore_target(tmp_path: Path) -> None:
    source = tmp_path / "library"
    source.mkdir()
    target = tmp_path / "restore"
    target.mkdir()

    with pytest.raises(SafetyViolation):
        validate_restore_target([source], target)


def test_rejects_restore_target_nested_under_source(tmp_path: Path) -> None:
    source = tmp_path / "library"
    source.mkdir()

    with pytest.raises(SafetyViolation):
        validate_restore_target([source], source / "restore")


def test_rejects_restore_target_that_contains_source(tmp_path: Path) -> None:
    source = tmp_path / "library"
    source.mkdir()

    with pytest.raises(SafetyViolation):
        validate_restore_target([source], tmp_path)


def test_remove_tree_clears_read_only_files_from_owned_temporary_tree(tmp_path: Path) -> None:
    temporary_tree = tmp_path / "temporary"
    temporary_tree.mkdir()
    read_only_file = temporary_tree / "readonly.txt"
    read_only_file.write_text("temporary", encoding="utf-8")
    read_only_file.chmod(0o444)

    remove_tree(temporary_tree)

    assert not temporary_tree.exists()
