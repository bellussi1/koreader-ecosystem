from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from koreader_ecosystem.application_state import ApplicationState, ApplicationStateError
from koreader_ecosystem.backup_automation import AutomationError, BackupAutomationSettings
from koreader_ecosystem.config import (
    AppConfig,
    ConfigError,
    save_config,
    validate_s3_region,
    validate_s3_repository,
)


class ProfileRecoveryError(RuntimeError):
    """Raised when a restored profile cannot be inspected or installed safely."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class ProfileRecoveryPreview:
    source_fingerprint: str
    schema_version: int
    row_counts: dict[str, int]
    configured_calibre: bool
    configured_restic: bool
    configured_remote_backup: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProfileRecoveryResult:
    checkpoint_id: str
    schema_version: int
    requires_restart: bool


_MARKER_NAME = ".kre-verified-restore.json"
_DATABASE_NAME = "application-state.sqlite3"
_CONFIG_NAME = "config.json"
_MAX_CONFIG_BYTES = 65_536
_MAX_DATABASE_BYTES = 512 * 1024 * 1024
_SUPPORTED_SCHEMA_VERSION = 6
_CONFIG_KEYS = frozenset(
    {
        "calibre_library",
        "restic_repository",
        "restic_remote_repository",
        "restic_remote_region",
        "state_root",
        "daily_backup_enabled",
        "daily_backup_time",
        "backup_on_kindle_connect",
    }
)
_ROW_TABLES = (
    "book_identities",
    "identity_links",
    "reading_events",
    "annotation_versions",
    "import_batches",
    "csv_import_rows",
    "remote_cache",
    "sync_outbox",
    "sync_outbox_reviews",
)


def mark_verified_restore(
    restore_root: Path,
    *,
    snapshot_id: str,
    file_count: int,
    total_bytes: int,
) -> None:
    """Mark a newly completed Restic restore as a profile-recovery source."""
    root = _safe_root(restore_root)
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot_id):
        raise ProfileRecoveryError("profile_restore_marker_invalid")
    if (
        type(file_count) is not int
        or file_count < 0
        or type(total_bytes) is not int
        or total_bytes < 0
    ):
        raise ProfileRecoveryError("profile_restore_marker_invalid")
    marker = root / _MARKER_NAME
    if marker.exists() or marker.is_symlink() or marker.is_junction():
        raise ProfileRecoveryError("profile_restore_marker_invalid")
    payload = {
        "format_version": 1,
        "snapshot_id": snapshot_id,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "verified_at": datetime.now(UTC).isoformat(),
    }
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=root,
            prefix=".kre-restore-marker-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, marker)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise ProfileRecoveryError("profile_restore_marker_write_failed") from None


def preview_profile_recovery(restore_root: Path) -> ProfileRecoveryPreview:
    root = _safe_root(restore_root)
    _validate_restore_marker(root)
    profile_dir = _find_profile_directory(root)
    config_path = _safe_file(profile_dir / _CONFIG_NAME, maximum_bytes=_MAX_CONFIG_BYTES)
    database_path = _safe_file(profile_dir / _DATABASE_NAME, maximum_bytes=_MAX_DATABASE_BYTES)
    raw_config = _read_config(config_path)
    schema_version, counts = _inspect_database(database_path)
    fingerprint = _fingerprint((root / _MARKER_NAME, config_path, database_path))
    return ProfileRecoveryPreview(
        source_fingerprint=fingerprint,
        schema_version=schema_version,
        row_counts=counts,
        configured_calibre=isinstance(raw_config.get("calibre_library"), str),
        configured_restic=isinstance(raw_config.get("restic_repository"), str),
        configured_remote_backup=isinstance(raw_config.get("restic_remote_repository"), str),
    )


def apply_profile_recovery(
    restore_root: Path,
    active_state_root: Path,
    expected_fingerprint: str,
    *,
    active_config: AppConfig,
    confirmed: bool,
) -> ProfileRecoveryResult:
    if confirmed is not True:
        raise ProfileRecoveryError("profile_recovery_confirmation_required")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_fingerprint):
        raise ProfileRecoveryError("profile_recovery_preview_invalid")
    active_root = _safe_or_create_directory(active_state_root)
    if active_config.state_root.resolve(strict=False) != active_root:
        raise ProfileRecoveryError("profile_recovery_active_state_invalid")
    root = _safe_root(restore_root)
    _validate_restore_marker(root)
    profile_dir = _find_profile_directory(root)
    fingerprint = _fingerprint(
        (root / _MARKER_NAME, profile_dir / _CONFIG_NAME, profile_dir / _DATABASE_NAME)
    )
    if fingerprint != expected_fingerprint:
        raise ProfileRecoveryError("profile_recovery_source_changed")
    source_config_path = profile_dir / _CONFIG_NAME
    source_database_path = profile_dir / _DATABASE_NAME
    recovered_config = _recovered_config(
        _read_config(source_config_path), active_root, active_config
    )
    active_database_path = active_root / _DATABASE_NAME
    active_config_path = active_root / _CONFIG_NAME
    _require_regular_or_missing(active_database_path)
    _require_regular_or_missing(active_config_path)

    checkpoint_id = uuid.uuid4().hex
    checkpoint_root = active_root / "profile-recovery-checkpoints"
    _safe_or_create_directory(checkpoint_root)
    checkpoint_dir = checkpoint_root / checkpoint_id
    try:
        checkpoint_dir.mkdir()
    except OSError:
        raise ProfileRecoveryError("profile_recovery_checkpoint_failed") from None

    config_installed = False
    active_touched = False
    try:
        with tempfile.TemporaryDirectory(
            prefix=".profile-recovery-stage-", dir=active_root
        ) as stage_name:
            stage = Path(stage_name)
            staged_database = stage / _DATABASE_NAME
            staged_config = stage / _CONFIG_NAME
            _sqlite_backup(source_database_path, staged_database)
            try:
                ApplicationState(staged_database)
                staged_schema, _ = _inspect_database(staged_database)
                with closing(sqlite3.connect(staged_database, timeout=10)) as staged_connection:
                    staged_connection.execute("DELETE FROM remote_cache WHERE provider='hardcover'")
                    staged_connection.commit()
            except ApplicationStateError:
                raise ProfileRecoveryError("profile_recovery_database_invalid") from None
            save_config(recovered_config, staged_config)
            _checkpoint_active(active_database_path, active_config_path, checkpoint_dir)
            _write_checkpoint_manifest(
                checkpoint_dir,
                had_database=active_database_path.exists(),
                had_config=active_config_path.exists(),
            )
            active_touched = True
            _discard_database_sidecars(active_database_path, checkpoint_dir)
            os.replace(staged_config, active_config_path)
            config_installed = True
            os.replace(staged_database, active_database_path)
    except ProfileRecoveryError:
        if active_touched or config_installed:
            try:
                _rollback_active(active_database_path, active_config_path, checkpoint_dir)
            except OSError, sqlite3.Error, KeyError, TypeError, ValueError:
                raise ProfileRecoveryError("profile_recovery_rollback_failed") from None
        raise
    except OSError, sqlite3.Error, ConfigError:
        if active_touched or config_installed:
            try:
                _rollback_active(active_database_path, active_config_path, checkpoint_dir)
            except OSError, sqlite3.Error, KeyError, TypeError, ValueError:
                raise ProfileRecoveryError("profile_recovery_rollback_failed") from None
        raise ProfileRecoveryError("profile_recovery_install_failed") from None
    return ProfileRecoveryResult(
        checkpoint_id=checkpoint_id,
        schema_version=staged_schema,
        requires_restart=True,
    )


def _safe_root(path: Path) -> Path:
    candidate = Path(path).expanduser()
    try:
        if not candidate.is_absolute() or candidate.is_symlink() or candidate.is_junction():
            raise ProfileRecoveryError("profile_recovery_source_invalid")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_dir():
            raise ProfileRecoveryError("profile_recovery_source_invalid")
        return resolved
    except ProfileRecoveryError:
        raise
    except OSError:
        raise ProfileRecoveryError("profile_recovery_source_invalid") from None


def _safe_or_create_directory(path: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ProfileRecoveryError("profile_recovery_active_state_invalid")
    current = candidate
    missing: list[Path] = []
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            raise ProfileRecoveryError("profile_recovery_active_state_invalid")
        current = current.parent
    try:
        if current.is_symlink() or current.is_junction() or not current.is_dir():
            raise ProfileRecoveryError("profile_recovery_active_state_invalid")
        for parent in missing:
            parent.mkdir()
        resolved = candidate.resolve(strict=True)
        if resolved.is_symlink() or resolved.is_junction():
            raise ProfileRecoveryError("profile_recovery_active_state_invalid")
        return resolved
    except ProfileRecoveryError:
        raise
    except OSError:
        raise ProfileRecoveryError("profile_recovery_active_state_invalid") from None


def _safe_file(path: Path, *, maximum_bytes: int) -> Path:
    try:
        if path.is_symlink() or path.is_junction() or not path.is_file():
            raise ProfileRecoveryError("profile_recovery_profile_incomplete")
        size = path.stat().st_size
        if size < 1 or size > maximum_bytes:
            raise ProfileRecoveryError("profile_recovery_profile_incomplete")
        return path.resolve(strict=True)
    except ProfileRecoveryError:
        raise
    except OSError:
        raise ProfileRecoveryError("profile_recovery_profile_incomplete") from None


def _validate_restore_marker(root: Path) -> None:
    marker_path = root / _MARKER_NAME
    if not marker_path.exists():
        raise ProfileRecoveryError("profile_restore_unverified")
    marker = _safe_file(marker_path, maximum_bytes=4096)
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except OSError, UnicodeError, json.JSONDecodeError:
        raise ProfileRecoveryError("profile_restore_unverified") from None
    if (
        not isinstance(value, dict)
        or set(value)
        != {"format_version", "snapshot_id", "file_count", "total_bytes", "verified_at"}
        or value.get("format_version") != 1
        or not isinstance(value.get("snapshot_id"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["snapshot_id"])
        or type(value.get("file_count")) is not int
        or value["file_count"] < 0
        or type(value.get("total_bytes")) is not int
        or value["total_bytes"] < 0
        or not isinstance(value.get("verified_at"), str)
    ):
        raise ProfileRecoveryError("profile_restore_unverified")


def _find_profile_directory(root: Path) -> Path:
    candidates: set[Path] = set()
    try:
        for current, directories, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            if current_path.is_symlink() or current_path.is_junction():
                raise ProfileRecoveryError("profile_recovery_source_invalid")
            for name in (*directories, *files):
                child = current_path / name
                if child.is_symlink() or child.is_junction():
                    raise ProfileRecoveryError("profile_recovery_source_invalid")
            if _DATABASE_NAME in files:
                candidate = current_path
                if (candidate / _CONFIG_NAME).is_file():
                    candidates.add(candidate)
    except OSError:
        raise ProfileRecoveryError("profile_recovery_profile_incomplete") from None
    if not candidates:
        raise ProfileRecoveryError("profile_recovery_profile_incomplete")
    if len(candidates) != 1:
        raise ProfileRecoveryError("profile_recovery_ambiguous")
    return next(iter(candidates))


def _read_config(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeError, json.JSONDecodeError:
        raise ProfileRecoveryError("profile_recovery_config_invalid") from None
    if (
        not isinstance(raw, dict)
        or not all(isinstance(key, str) for key in raw)
        or set(raw) - _CONFIG_KEYS
        or not isinstance(raw.get("calibre_library"), str)
        or not isinstance(raw.get("restic_repository"), str)
        or not Path(raw["calibre_library"]).is_absolute()
        or not Path(raw["restic_repository"]).is_absolute()
    ):
        raise ProfileRecoveryError("profile_recovery_config_invalid")
    try:
        BackupAutomationSettings(
            daily_backup_enabled=raw.get("daily_backup_enabled", False),
            daily_backup_time=raw.get("daily_backup_time", "20:00"),
            backup_on_kindle_connect=raw.get("backup_on_kindle_connect", False),
        )
        if raw.get("restic_remote_repository") is None:
            if raw.get("restic_remote_region") is not None:
                raise ConfigError("remote region without repository")
        else:
            validate_s3_repository(raw["restic_remote_repository"])
            validate_s3_region(raw.get("restic_remote_region", "auto"))
    except ConfigError, AutomationError:
        raise ProfileRecoveryError("profile_recovery_config_invalid") from None
    return raw


def _inspect_database(path: Path) -> tuple[int, dict[str, int]]:
    try:
        uri = path.resolve(strict=True).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as connection:
            connection.execute("PRAGMA query_only = ON")
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ProfileRecoveryError("profile_recovery_database_invalid")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if not 1 <= version <= _SUPPORTED_SCHEMA_VERSION:
                raise ProfileRecoveryError("profile_recovery_schema_unsupported")
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                if isinstance(row[0], str)
            }
            if "schema_migrations" not in tables:
                raise ProfileRecoveryError("profile_recovery_database_invalid")
            counts = {
                table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                for table in _ROW_TABLES
                if table in tables
            }
            return version, counts
    except ProfileRecoveryError:
        raise
    except OSError, sqlite3.Error, ValueError:
        raise ProfileRecoveryError("profile_recovery_database_invalid") from None


def _fingerprint(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    try:
        for path in paths:
            digest.update(path.name.encode("utf-8"))
            digest.update(b"\0")
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    except OSError:
        raise ProfileRecoveryError("profile_recovery_profile_incomplete") from None
    return digest.hexdigest()


def _recovered_config(
    raw: dict[str, Any], active_root: Path, active_config: AppConfig
) -> AppConfig:
    try:
        remote_repository = raw.get("restic_remote_repository")
        remote_region = raw.get("restic_remote_region")
        if remote_repository is not None:
            remote_repository = validate_s3_repository(remote_repository)
            remote_region = validate_s3_region(remote_region)
        elif remote_region is not None:
            raise ConfigError("remote region without repository")
        calibre = Path(raw.get("calibre_library", active_config.calibre_library)).expanduser()
        repository = Path(
            raw.get("restic_repository", active_config.restic_repository)
        ).expanduser()
        if not calibre.is_absolute() or not repository.is_absolute():
            raise ConfigError("recovered paths must be absolute")
        return AppConfig(
            calibre_library=calibre.resolve(strict=False),
            restic_repository=repository.resolve(strict=False),
            state_root=active_root,
            restic_remote_repository=remote_repository,
            restic_remote_region=remote_region,
            daily_backup_enabled=raw.get("daily_backup_enabled", False),
            daily_backup_time=raw.get("daily_backup_time", "20:00"),
            backup_on_kindle_connect=raw.get("backup_on_kindle_connect", False),
        )
    except ConfigError, OSError, TypeError:
        raise ProfileRecoveryError("profile_recovery_config_invalid") from None


def _require_regular_or_missing(path: Path) -> None:
    try:
        if path.is_symlink() or path.is_junction():
            raise ProfileRecoveryError("profile_recovery_active_state_invalid")
        if path.exists() and not path.is_file():
            raise ProfileRecoveryError("profile_recovery_active_state_invalid")
    except OSError:
        raise ProfileRecoveryError("profile_recovery_active_state_invalid") from None


def _sqlite_backup(source: Path, destination: Path) -> None:
    try:
        source_uri = source.resolve(strict=True).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True, timeout=10)) as source_connection:
            with closing(sqlite3.connect(destination, timeout=10)) as destination_connection:
                source_connection.backup(destination_connection)
                if destination_connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise ProfileRecoveryError("profile_recovery_database_invalid")
    except ProfileRecoveryError:
        raise
    except OSError, sqlite3.Error:
        raise ProfileRecoveryError("profile_recovery_database_invalid") from None


def _checkpoint_active(database: Path, config: Path, checkpoint: Path) -> None:
    if database.exists():
        _sqlite_backup(database, checkpoint / _DATABASE_NAME)
    if config.exists():
        try:
            shutil.copyfile(config, checkpoint / _CONFIG_NAME)
        except OSError:
            raise ProfileRecoveryError("profile_recovery_checkpoint_failed") from None
    try:
        sidecars = checkpoint / "database-sidecars"
        sidecars.mkdir()
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(database) + suffix)
            if sidecar.exists():
                _require_regular_or_missing(sidecar)
                shutil.copyfile(sidecar, sidecars / (database.name + suffix))
    except OSError, ProfileRecoveryError:
        raise ProfileRecoveryError("profile_recovery_checkpoint_failed") from None


def _write_checkpoint_manifest(checkpoint: Path, *, had_database: bool, had_config: bool) -> None:
    try:
        (checkpoint / "checkpoint.json").write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "created_at": datetime.now(UTC).isoformat(),
                    "had_database": had_database,
                    "had_config": had_config,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        raise ProfileRecoveryError("profile_recovery_checkpoint_failed") from None


def _discard_database_sidecars(database: Path, checkpoint: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        active = Path(str(database) + suffix)
        if active.exists():
            _require_regular_or_missing(active)
            saved = checkpoint / "database-sidecars" / (database.name + suffix)
            if not saved.is_file():
                raise ProfileRecoveryError("profile_recovery_checkpoint_failed")
            active.unlink()


def _rollback_active(database: Path, config: Path, checkpoint: Path) -> None:
    metadata = json.loads((checkpoint / "checkpoint.json").read_text(encoding="utf-8"))
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(database) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    if metadata["had_database"]:
        rollback_database = checkpoint / ".rollback-application-state.sqlite3"
        shutil.copyfile(checkpoint / _DATABASE_NAME, rollback_database)
        os.replace(rollback_database, database)
    elif database.exists():
        database.unlink()
    if metadata["had_config"]:
        rollback_config = checkpoint / ".rollback-config.json"
        shutil.copyfile(checkpoint / _CONFIG_NAME, rollback_config)
        os.replace(rollback_config, config)
    elif config.exists():
        config.unlink()
