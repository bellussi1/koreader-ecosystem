from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from koreader_ecosystem.config import AppConfig
from koreader_ecosystem.manifest import (
    ManifestEntry,
    ManifestError,
    build_manifest,
    compare_manifests,
)
from koreader_ecosystem.mtp import MtpStageSummary
from koreader_ecosystem.operation_history import OperationHistory, OperationHistoryError
from koreader_ecosystem.processes import run_hidden
from koreader_ecosystem.restic import ResticResult
from koreader_ecosystem.safety import (
    SafetyViolation,
    remove_tree,
    require_calibre_closed,
    validate_restore_target,
)


class BackupError(RuntimeError):
    """Raised when a backup cannot be completed safely."""


class ResticOperations(Protocol):
    def ensure_password(self) -> None: ...

    def run(self, args: Sequence[str], *, cwd: Path | None = None) -> ResticResult: ...


@dataclass(frozen=True, slots=True)
class BackupResult:
    snapshot_id: str
    file_count: int
    total_bytes: int
    calibre_file_count: int
    kindle_file_count: int
    restore_verified: bool = False
    application_state_file_count: int = 0


@dataclass(frozen=True, slots=True)
class RestoreResult:
    snapshot_id: str
    file_count: int
    total_bytes: int
    manifest: tuple[ManifestEntry, ...]


def _running_process_names() -> tuple[str, ...]:
    try:
        result = run_hidden(
            ["tasklist.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except OSError, subprocess.SubprocessError:
        raise BackupError("Could not inspect running processes") from None
    if result.returncode != 0:
        raise BackupError("Could not inspect running processes")
    return tuple(row[0] for row in csv.reader(result.stdout.splitlines()) if row)


def _check_calibre_integrity(library: Path) -> None:
    database = library / "metadata.db"
    if not database.is_file():
        raise BackupError("Calibre metadata database is missing")

    try:
        connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
        try:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        raise BackupError("Calibre metadata integrity check failed") from None
    if row != ("ok",):
        raise BackupError("Calibre metadata integrity check failed")


def _file_stats(manifest: Sequence[ManifestEntry]) -> tuple[int, int]:
    files = tuple(entry for entry in manifest if entry.kind == "file")
    return len(files), sum(entry.size for entry in files)


def _validate_device_stage(stage: MtpStageSummary) -> tuple[ManifestEntry, ...]:
    if not stage.destination.is_absolute() or not stage.destination.is_dir():
        raise BackupError("Kindle staging directory is unavailable")
    try:
        actual = build_manifest(stage.destination)
    except ManifestError:
        raise BackupError("Kindle staging manifest is invalid") from None
    if not compare_manifests(stage.manifest, actual).matches:
        raise BackupError("Kindle staging changed after verification")

    files, total_bytes = _file_stats(actual)
    directories = sum(entry.kind == "directory" for entry in actual)
    roots = tuple(
        sorted(
            (entry.path for entry in actual if entry.kind == "directory" and "/" not in entry.path),
            key=str.casefold,
        )
    )
    if (
        files != stage.file_count
        or directories != stage.directory_count
        or total_bytes != stage.total_bytes
        or roots != stage.roots
    ):
        raise BackupError("Kindle staging does not match its MTP inventory")
    return actual


def _validate_repository_location(repository: Path, sources: Sequence[Path]) -> None:
    resolved_repository = repository.resolve(strict=False)
    for source in sources:
        resolved_source = source.resolve(strict=False)
        if (
            resolved_repository == resolved_source
            or resolved_repository in resolved_source.parents
            or resolved_source in resolved_repository.parents
        ):
            raise BackupError("Restic repository overlaps a backup source")
    if repository.exists() and not repository.is_dir():
        raise BackupError("Restic repository path is not a directory")


def _relative_backup_sources(sources: Sequence[Path]) -> tuple[Path, tuple[str, ...]]:
    try:
        root = Path(os.path.commonpath([str(source) for source in sources]))
        if root in sources:
            root = root.parent
        relative_paths = tuple(str(source.relative_to(root)) for source in sources)
    except ValueError:
        raise BackupError("Calibre and Kindle staging must be on the same volume") from None
    if any(path in {"", ".", ".."} or path.startswith("..\\") for path in relative_paths):
        raise BackupError("Backup sources cannot be represented relative to a safe root")
    return root, relative_paths


def _check_free_space(repository: Path, required_bytes: int) -> None:
    existing = repository
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    try:
        free_bytes = shutil.disk_usage(existing).free
    except OSError:
        raise BackupError("Could not inspect available backup space") from None
    headroom = max(64 * 1024 * 1024, int(required_bytes * 0.10))
    if free_bytes < required_bytes + headroom:
        raise BackupError("Not enough free space for a complete backup")


def _verify_snapshot_restore(
    snapshot_id: str,
    config: AppConfig,
    restic: ResticOperations,
    relative_sources: Sequence[str],
    expected_manifests: Sequence[Sequence[ManifestEntry]],
    protected_sources: Sequence[Path],
) -> None:
    target_parent = config.state_root / "restore-validation"
    target = target_parent / uuid.uuid4().hex
    try:
        validate_restore_target(protected_sources, target)
    except SafetyViolation:
        raise BackupError("Temporary restore location overlaps a protected source") from None
    try:
        target_parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise BackupError("Temporary restore location is unavailable") from None
    try:
        result = restic.run(["restore", snapshot_id, "--target", str(target), "--json"])
        if not result.ok:
            raise BackupError("Restic snapshot could not be restored for validation")
        for relative_source, expected in zip(relative_sources, expected_manifests, strict=True):
            restored_source = target / relative_source
            try:
                actual = build_manifest(restored_source)
            except ManifestError:
                raise BackupError("Restic restore is incomplete") from None
            if not compare_manifests(expected, actual).matches:
                raise BackupError("Restored files differ from the backup sources")
    finally:
        try:
            remove_tree(target)
        except OSError, SafetyViolation:
            raise BackupError("Temporary restore could not be removed") from None
        if target.exists():
            raise BackupError("Temporary restore could not be removed")


_APPLICATION_STATE_FILES = (
    "config.json",
    "application-state.sqlite3",
    "koreader-calibre.sqlite3",
    "hardcover-requests.sqlite3",
    "operation-history.json",
)
_APPLICATION_CONFIG_KEYS = frozenset(
    {
        "calibre_library",
        "restic_repository",
        "restic_remote_repository",
        "restic_remote_region",
        "state_root",
    }
)


def _copy_application_state(state_root: Path, destination: Path) -> bool:
    """Copy an allowlisted set of non-secret local state into a backup staging folder."""
    copied_any = False
    for filename in _APPLICATION_STATE_FILES:
        source = state_root / filename
        try:
            source.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise BackupError("Application state could not be inspected") from None

        if source.is_symlink() or source.is_junction() or not source.is_file():
            raise BackupError("Application state contains an unsafe file")
        if filename == "config.json" and source.stat().st_size > 65_536:
            raise BackupError("Application configuration is unexpectedly large")
        target = destination / filename

        if filename in {
            "application-state.sqlite3",
            "koreader-calibre.sqlite3",
            "hardcover-requests.sqlite3",
        }:
            source_uri = f"{source.resolve().as_uri()}?mode=ro"
            source_connection: sqlite3.Connection | None = None
            target_connection: sqlite3.Connection | None = None
            try:
                source_connection = sqlite3.connect(source_uri, uri=True, timeout=10)
                target_connection = sqlite3.connect(target, timeout=10)
                source_connection.backup(target_connection)
                integrity = target_connection.execute("PRAGMA integrity_check").fetchone()
                if integrity != ("ok",):
                    raise BackupError("Application state database failed its integrity check")
            except sqlite3.Error:
                raise BackupError("Application state database could not be copied safely") from None
            finally:
                if source_connection is not None:
                    source_connection.close()
                if target_connection is not None:
                    target_connection.close()
        elif filename == "operation-history.json":
            try:
                OperationHistory(source).latest()
                shutil.copyfile(source, target)
            except OperationHistoryError, OSError:
                raise BackupError(
                    "Application operation history could not be copied safely"
                ) from None
        elif filename == "config.json":
            try:
                value = json.loads(source.read_text(encoding="utf-8"))
            except OSError, UnicodeError, json.JSONDecodeError:
                raise BackupError("Application configuration could not be copied safely") from None
            if (
                not isinstance(value, dict)
                or not all(isinstance(key, str) for key in value)
                or set(value) - _APPLICATION_CONFIG_KEYS
            ):
                raise BackupError("Application configuration contains unsupported settings")
            try:
                shutil.copyfile(source, target)
            except OSError:
                raise BackupError("Application configuration could not be copied safely") from None
        copied_any = True
    return copied_any


@contextmanager
def _staged_application_state(
    state_root: Path, staging_parent: Path
) -> Iterator[tuple[Path | None, tuple[ManifestEntry, ...]]]:
    """Create a consistent, allowlisted state snapshot beside the Calibre source."""
    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        try:
            temporary = tempfile.TemporaryDirectory(prefix=".kre-app-state-", dir=staging_parent)
            state_directory = Path(temporary.name) / "application-state"
            state_directory.mkdir()
            if _copy_application_state(state_root, state_directory):
                try:
                    manifest = build_manifest(state_directory)
                except ManifestError:
                    raise BackupError("Application state snapshot is invalid") from None
                staged_state: tuple[Path | None, tuple[ManifestEntry, ...]] = (
                    state_directory,
                    manifest,
                )
            else:
                staged_state = (None, ())
        except BackupError:
            raise
        except OSError, sqlite3.Error:
            raise BackupError("Application state could not be staged safely") from None
        yield staged_state
    finally:
        if temporary is not None:
            try:
                temporary.cleanup()
            except OSError:
                raise BackupError("Temporary application state could not be removed") from None


def _repository_is_initialized(repository: Path, restic: ResticOperations) -> bool:
    if not repository.exists():
        return False
    if not repository.is_dir():
        raise BackupError("Restic repository path is not a directory")
    try:
        has_entries = next(repository.iterdir(), None) is not None
    except OSError:
        raise BackupError("Restic repository is not readable") from None
    if not has_entries:
        return False
    result = restic.run(["snapshots", "--json"])
    if result.ok:
        return True
    raise BackupError("Existing Restic repository could not be opened")


def create_backup(
    config: AppConfig,
    device_stage: MtpStageSummary,
    restic: ResticOperations,
    *,
    verify_restore: bool = False,
) -> BackupResult:
    """Back up Calibre, Kindle and the allowlisted local application state."""
    library = config.calibre_library.resolve(strict=False)
    stage_path = device_stage.destination.resolve(strict=False)
    if not library.is_dir():
        raise BackupError("Calibre library is unavailable")

    try:
        require_calibre_closed(_running_process_names())
    except SafetyViolation:
        raise BackupError("Close Calibre and its server before backing up") from None

    _check_calibre_integrity(library)
    try:
        calibre_manifest = build_manifest(library)
    except ManifestError:
        raise BackupError("Calibre library cannot be represented safely") from None
    device_manifest = _validate_device_stage(device_stage)

    repository = config.restic_repository.resolve(strict=False)
    with _staged_application_state(config.state_root, library.parent) as (
        app_state_path,
        app_state_manifest,
    ):
        sources_list = [library, stage_path]
        manifests = [calibre_manifest, device_manifest]
        if app_state_path is not None:
            sources_list.append(app_state_path)
            manifests.append(app_state_manifest)
        sources = tuple(sources_list)
        _validate_repository_location(repository, sources)
        backup_root, relative_sources = _relative_backup_sources(sources)
        calibre_files, calibre_bytes = _file_stats(calibre_manifest)
        kindle_files, kindle_bytes = _file_stats(device_manifest)
        state_files, state_bytes = _file_stats(app_state_manifest)
        file_count = calibre_files + kindle_files + state_files
        total_bytes = calibre_bytes + kindle_bytes + state_bytes
        same_volume = repository.anchor.casefold() == config.state_root.anchor.casefold()
        _check_free_space(repository, total_bytes * (2 if verify_restore and same_volume else 1))
        if verify_restore and not same_volume:
            _check_free_space(config.state_root, total_bytes)

        try:
            restic.ensure_password()
        except Exception:
            raise BackupError("Restic password is unavailable") from None

        initialized = _repository_is_initialized(repository, restic)
        if not initialized:
            try:
                repository.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                raise BackupError("Restic repository location is unavailable") from None
            init_result = restic.run(["init"])
            if not init_result.ok:
                raise BackupError("Restic repository could not be initialized")

        backup_args = ["backup", "--json", "--tag", "koreader-ecosystem"]
        if app_state_path is not None:
            backup_args.extend(["--tag", "application-state-v1"])
        backup_args.extend(relative_sources)
        backup_result = restic.run(backup_args, cwd=backup_root)
        if not backup_result.ok:
            raise BackupError("Restic did not complete the backup")
        snapshot_id = backup_result.summary.get("snapshot_id")
        if not isinstance(snapshot_id, str) or len(snapshot_id) != 64:
            raise BackupError("Restic did not return a valid snapshot identifier")

        check_result = restic.run(["check"])
        if not check_result.ok:
            raise BackupError("Restic repository verification failed")

        if verify_restore:
            _verify_snapshot_restore(
                snapshot_id,
                config,
                restic,
                relative_sources,
                tuple(manifests),
                (*sources, repository),
            )

        return BackupResult(
            snapshot_id=snapshot_id,
            file_count=file_count,
            total_bytes=total_bytes,
            calibre_file_count=calibre_files,
            kindle_file_count=kindle_files,
            restore_verified=verify_restore,
            application_state_file_count=state_files,
        )


def create_calibre_backup(
    config: AppConfig,
    restic: ResticOperations,
    *,
    verify_restore: bool = False,
) -> BackupResult:
    """Create and check a Restic snapshot with Calibre and local app state."""
    library = config.calibre_library.resolve(strict=False)
    if not library.is_dir():
        raise BackupError("Calibre library is unavailable")
    try:
        require_calibre_closed(_running_process_names())
    except SafetyViolation:
        raise BackupError("Close Calibre and its server before backing up") from None
    _check_calibre_integrity(library)
    try:
        manifest = build_manifest(library)
    except ManifestError:
        raise BackupError("Calibre library cannot be represented safely") from None

    relative_source = library.name
    if (
        not relative_source
        or relative_source in {".", ".."}
        or Path(relative_source).name != relative_source
    ):
        raise BackupError("Calibre library path cannot be represented safely")
    repository = config.restic_repository.resolve(strict=False)
    with _staged_application_state(config.state_root, library.parent) as (
        app_state_path,
        app_state_manifest,
    ):
        sources = (library, app_state_path) if app_state_path is not None else (library,)
        manifests = (manifest, app_state_manifest) if app_state_path is not None else (manifest,)
        _validate_repository_location(repository, sources)
        backup_root, relative_sources = _relative_backup_sources(sources)
        calibre_files, calibre_bytes = _file_stats(manifest)
        state_files, state_bytes = _file_stats(app_state_manifest)
        file_count = calibre_files + state_files
        total_bytes = calibre_bytes + state_bytes
        _check_free_space(repository, total_bytes * (2 if verify_restore else 1))
        if verify_restore:
            _check_free_space(config.state_root, total_bytes)

        try:
            restic.ensure_password()
        except Exception:
            raise BackupError("Restic password is unavailable") from None
        initialized = _repository_is_initialized(repository, restic)
        if not initialized:
            try:
                repository.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                raise BackupError("Restic repository location is unavailable") from None
            if not restic.run(["init"]).ok:
                raise BackupError("Restic repository could not be initialized")

        backup_args = [
            "backup",
            "--json",
            "--tag",
            "koreader-ecosystem",
            "--tag",
            "calibre-only",
        ]
        if app_state_path is not None:
            backup_args.extend(["--tag", "application-state-v1"])
        backup_args.extend(relative_sources)
        result = restic.run(backup_args, cwd=backup_root)
        snapshot_id = result.summary.get("snapshot_id") if result.ok else None
        if not isinstance(snapshot_id, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot_id):
            raise BackupError("Restic did not return a valid Calibre snapshot identifier")
        if not restic.run(["check"]).ok:
            raise BackupError("Restic repository verification failed")
        if verify_restore:
            _verify_snapshot_restore(
                snapshot_id,
                config,
                restic,
                relative_sources,
                manifests,
                (*sources, repository),
            )
        return BackupResult(
            snapshot_id=snapshot_id,
            file_count=file_count,
            total_bytes=total_bytes,
            calibre_file_count=calibre_files,
            kindle_file_count=0,
            restore_verified=verify_restore,
            application_state_file_count=state_files,
        )


def preflight_before_device_copy(config: AppConfig, restic: ResticOperations) -> None:
    """Check local blockers before spending time copying the Kindle over MTP."""
    library = config.calibre_library.resolve(strict=False)
    if not library.is_dir():
        raise BackupError("Calibre library is unavailable")
    try:
        require_calibre_closed(_running_process_names())
    except SafetyViolation:
        raise BackupError("Close Calibre and its server before backing up") from None
    _check_calibre_integrity(library)
    _validate_repository_location(config.restic_repository, (library, config.state_root))
    _relative_backup_sources((library, (config.state_root / "staging").resolve(strict=False)))
    if getattr(restic, "available", True) is False:
        raise BackupError("Restic is not installed")
    try:
        restic.ensure_password()
    except Exception:
        raise BackupError("Restic password is unavailable") from None


def validate_restore(
    snapshot: str,
    target: Path,
    restic: ResticOperations,
    sources: Sequence[Path] = (),
) -> RestoreResult:
    """Restore a complete snapshot to a new path and produce its local manifest."""
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot):
        raise BackupError("Snapshot identifier is invalid")
    target = Path(target)
    if not target.is_absolute():
        raise BackupError("Restore target must be absolute")
    try:
        validate_restore_target(sources, target)
    except SafetyViolation:
        raise BackupError("Restore target is existing or overlaps a source") from None
    try:
        restic.ensure_password()
    except Exception:
        raise BackupError("Restic password is unavailable") from None

    created_parents: list[Path] = []
    ancestor = target.parent
    while not ancestor.exists():
        created_parents.append(ancestor)
        if ancestor.parent == ancestor:
            break
        ancestor = ancestor.parent

    target_created = False
    restore_validated = False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir()
        target_created = True
        result = restic.run(["restore", snapshot, "--target", str(target), "--json"])
        if not result.ok:
            raise BackupError("Restic restore did not complete")
        if not target.is_dir():
            raise BackupError("Restic restore target was not created")
        try:
            manifest = build_manifest(target)
        except ManifestError:
            raise BackupError("Restored tree cannot be verified") from None
        file_count, total_bytes = _file_stats(manifest)
        restore_validated = True
        return RestoreResult(
            snapshot_id=snapshot,
            file_count=file_count,
            total_bytes=total_bytes,
            manifest=manifest,
        )
    finally:
        if not restore_validated:
            cleanup_error = False
            if target_created:
                try:
                    remove_tree(target)
                except OSError, SafetyViolation:
                    cleanup_error = True
            for created_parent in created_parents:
                try:
                    created_parent.rmdir()
                except OSError:
                    pass
            if cleanup_error:
                raise BackupError("Failed partial restore could not be removed") from None
