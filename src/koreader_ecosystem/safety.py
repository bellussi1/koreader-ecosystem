from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any


class SafetyViolation(RuntimeError):
    """Raised when an operation could risk active or unrelated data."""


def remove_tree(path: Path) -> None:
    """Remove an owned temporary tree, clearing read-only attributes when needed."""
    path = Path(path)
    if path.is_symlink() or path.is_junction():
        raise SafetyViolation("Refusing to remove a temporary tree link")
    if not path.exists():
        return

    def retry_after_clearing_read_only(
        function: Callable[..., Any], failed_path: str, error: BaseException
    ) -> None:
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(failed_path, stat.S_IWRITE)
        function(failed_path)

    shutil.rmtree(path, onexc=retry_after_clearing_read_only)


_CALIBRE_PROCESSES = {
    "calibre",
    "calibre-server",
    "calibre-parallel",
}


def require_calibre_closed(process_names: Iterable[str]) -> None:
    for process_name in process_names:
        name = Path(process_name.strip()).name.casefold()
        if name.endswith(".exe"):
            name = name[:-4]
        if name in _CALIBRE_PROCESSES:
            raise SafetyViolation("Close Calibre and its server before backing up the library")


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def validate_restore_target(sources: Sequence[Path], target: Path) -> None:
    target = Path(target)
    if target.exists() or target.is_symlink() or target.is_junction():
        raise SafetyViolation("Restore target must not exist")

    target_resolved = target.resolve(strict=False)
    source_paths: list[Path] = []
    for source in sources:
        source_path = Path(source)
        if not source_path.exists():
            raise SafetyViolation("Restore source does not exist")
        source_paths.append(source_path.resolve(strict=True))

    for source_path in source_paths:
        if _is_within(target_resolved, source_path) or _is_within(source_path, target_resolved):
            raise SafetyViolation("Restore target must not overlap a source path")
