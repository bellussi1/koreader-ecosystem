from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from koreader_ecosystem.application_state import ApplicationState, ApplicationStateError


class OperationHistoryError(RuntimeError):
    """Raised when the sanitized desktop operation history is invalid."""


OperationStatus = Literal["running", "success", "failed", "cancelled"]
_SCHEMA_VERSION = 1
_ALLOWED_KEYS = frozenset(
    {
        "hardcover_library_refresh",
        "hardcover_mutation",
        "koreader_calibre_preview",
        "koreader_calibre_apply",
        "local_backup",
        "local_restore",
        "remote_sync",
    }
)
_ALLOWED_STATUSES = frozenset({"running", "success", "failed", "cancelled"})
_REASON_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


@dataclass(frozen=True, slots=True)
class OperationRecord:
    key: str
    status: OperationStatus
    updated_at: str
    last_success_at: str | None
    reason_code: str | None = None


def _safe_path(path: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise OperationHistoryError("Operation history path must be absolute")
    current = candidate
    while current != current.parent:
        try:
            if current.is_symlink() or current.is_junction():
                raise OperationHistoryError("Operation history path cannot use a link")
        except OSError:
            raise OperationHistoryError("Operation history path cannot be checked") from None
        current = current.parent
    if candidate.exists() and not candidate.is_file():
        raise OperationHistoryError("Operation history path is not a file")
    return candidate


def _validate_timestamp(value: str) -> str:
    if not isinstance(value, str):
        raise OperationHistoryError("Operation timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise OperationHistoryError("Operation timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OperationHistoryError("Operation timestamp must include a timezone")
    return parsed.astimezone(UTC).isoformat(timespec="seconds")


class OperationHistory:
    """Persist a small allowlisted history without book or path metadata."""

    def __init__(self, path: Path, *, legacy_path: Path | None = None) -> None:
        self.path = _safe_path(path)
        self._database = (
            ApplicationState(self.path) if self.path.suffix.casefold() == ".sqlite3" else None
        )
        if self._database is not None:
            self._migrate_legacy_json(legacy_path or self.path.parent / "operation-history.json")

    @staticmethod
    def now() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")

    def get(self, key: str) -> OperationRecord | None:
        self._validate_key(key)
        operations = self._read()
        return operations.get(key)

    def latest(self) -> OperationRecord | None:
        operations = self._read()
        if not operations:
            return None
        return max(operations.values(), key=lambda record: record.updated_at)

    def record(
        self,
        *,
        key: str,
        status: OperationStatus,
        updated_at: str | None = None,
        reason_code: str | None = None,
    ) -> OperationRecord:
        self._validate_key(key)
        if status not in _ALLOWED_STATUSES:
            raise OperationHistoryError("Operation status is invalid")
        timestamp = _validate_timestamp(updated_at or self.now())
        if reason_code is not None and (
            not isinstance(reason_code, str) or not _REASON_CODE.fullmatch(reason_code)
        ):
            raise OperationHistoryError("Operation reason code is invalid")
        if status == "failed" and reason_code is None:
            reason_code = "operation_failed"
        if status != "failed":
            reason_code = None

        operations = self._read()
        previous = operations.get(key)
        last_success_at = previous.last_success_at if previous else None
        if status == "success":
            last_success_at = timestamp
        record = OperationRecord(
            key=key,
            status=status,
            updated_at=timestamp,
            last_success_at=last_success_at,
            reason_code=reason_code,
        )
        operations[key] = record
        self._write(operations)
        return record

    def recover_interrupted_backup(
        self, *, updated_at: str | None = None
    ) -> OperationRecord | None:
        """Mark a stale backup run as interrupted after its process lock was acquired."""
        previous = self.get("local_backup")
        if previous is None or previous.status != "running":
            return previous
        return self.record(
            key="local_backup",
            status="failed",
            updated_at=updated_at,
            reason_code="interrupted",
        )

    @staticmethod
    def _validate_key(key: str) -> None:
        if key not in _ALLOWED_KEYS:
            raise OperationHistoryError("Operation key is not allowlisted")

    def _read(self) -> dict[str, OperationRecord]:
        if self._database is not None:
            try:
                with self._database.connect() as connection:
                    rows = connection.execute(
                        "SELECT operation_key, status, updated_at, last_success_at, reason_code "
                        "FROM operations"
                    ).fetchall()
            except sqlite3.Error:
                raise OperationHistoryError("Operation history could not be read") from None
            db_records: dict[str, OperationRecord] = {}
            for row in rows:
                key = str(row["operation_key"])
                self._validate_key(key)
                status = row["status"]
                if not isinstance(status, str) or status not in _ALLOWED_STATUSES:
                    raise OperationHistoryError("Operation history status is invalid")
                updated_at = _validate_timestamp(row["updated_at"])
                last_success_at = (
                    _validate_timestamp(row["last_success_at"])
                    if row["last_success_at"] is not None
                    else None
                )
                reason_code = row["reason_code"]
                if reason_code is not None and (
                    not isinstance(reason_code, str) or not _REASON_CODE.fullmatch(reason_code)
                ):
                    raise OperationHistoryError("Operation history reason is invalid")
                if status != "failed" and reason_code is not None:
                    raise OperationHistoryError("Operation history reason is invalid")
                db_records[key] = OperationRecord(
                    key=key,
                    status=status,  # type: ignore[arg-type]
                    updated_at=updated_at,
                    last_success_at=last_success_at,
                    reason_code=reason_code,
                )
            return db_records
        if not self.path.exists():
            return {}
        try:
            if (
                self.path.is_symlink()
                or self.path.is_junction()
                or self.path.stat().st_size > 16_384
            ):
                raise OperationHistoryError("Operation history file is unsafe")
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except OperationHistoryError:
            raise
        except OSError, UnicodeError, json.JSONDecodeError:
            raise OperationHistoryError("Operation history could not be read") from None
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != _SCHEMA_VERSION
            or not isinstance(payload.get("operations"), dict)
        ):
            raise OperationHistoryError("Operation history data is invalid")
        records: dict[str, OperationRecord] = {}
        for key, raw in payload["operations"].items():
            self._validate_key(key)
            if not isinstance(raw, dict) or set(raw) != {
                "status",
                "updated_at",
                "last_success_at",
                "reason_code",
            }:
                raise OperationHistoryError("Operation history record is invalid")
            status = raw["status"]
            if not isinstance(status, str) or status not in _ALLOWED_STATUSES:
                raise OperationHistoryError("Operation history status is invalid")
            updated_at = _validate_timestamp(raw["updated_at"])
            last_success_at_raw = raw["last_success_at"]
            last_success_at = (
                _validate_timestamp(last_success_at_raw)
                if last_success_at_raw is not None
                else None
            )
            reason_code = raw["reason_code"]
            if reason_code is not None and (
                not isinstance(reason_code, str) or not _REASON_CODE.fullmatch(reason_code)
            ):
                raise OperationHistoryError("Operation history reason is invalid")
            if status != "failed" and reason_code is not None:
                raise OperationHistoryError("Operation history reason is invalid")
            records[key] = OperationRecord(
                key=key,
                status=status,  # type: ignore[arg-type]
                updated_at=updated_at,
                last_success_at=last_success_at,
                reason_code=reason_code,
            )
        return records

    def _write(self, operations: dict[str, OperationRecord]) -> None:
        if self._database is not None:
            try:
                with self._database.connect() as connection:
                    connection.executemany(
                        """
                        INSERT INTO operations(
                            operation_key, status, updated_at, last_success_at, reason_code
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(operation_key) DO UPDATE SET
                            status = excluded.status,
                            updated_at = excluded.updated_at,
                            last_success_at = excluded.last_success_at,
                            reason_code = excluded.reason_code
                        """,
                        [
                            (
                                record.key,
                                record.status,
                                record.updated_at,
                                record.last_success_at,
                                record.reason_code,
                            )
                            for record in operations.values()
                        ],
                    )
            except sqlite3.Error:
                raise OperationHistoryError("Operation history could not be saved") from None
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.parent.is_symlink() or self.path.parent.is_junction():
            raise OperationHistoryError("Operation history path cannot use a link")
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "operations": {
                key: {
                    "status": record.status,
                    "updated_at": record.updated_at,
                    "last_success_at": record.last_success_at,
                    "reason_code": record.reason_code,
                }
                for key, record in sorted(operations.items())
            },
        }
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        except OSError:
            raise OperationHistoryError("Operation history could not be saved") from None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _migrate_legacy_json(self, legacy_path: Path) -> None:
        source_path = _safe_path(legacy_path)
        if source_path == self.path or not source_path.exists():
            return
        assert self._database is not None
        source_key = "operation-history-json-v1"
        try:
            with self._database.connect() as connection:
                if connection.execute(
                    "SELECT 1 FROM legacy_imports WHERE source_key = ?", (source_key,)
                ).fetchone():
                    return
            payload = source_path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            records = OperationHistory(source_path)._read()
            with self._database.connect() as connection:
                existing = connection.execute(
                    "SELECT 1 FROM legacy_imports WHERE source_key = ?", (source_key,)
                ).fetchone()
                if existing is not None:
                    return
                connection.executemany(
                    """
                    INSERT INTO operations(
                        operation_key, status, updated_at, last_success_at, reason_code
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(operation_key) DO NOTHING
                    """,
                    [
                        (
                            record.key,
                            record.status,
                            record.updated_at,
                            record.last_success_at,
                            record.reason_code,
                        )
                        for record in records.values()
                    ],
                )
                connection.execute(
                    "INSERT INTO legacy_imports(source_key, source_digest, imported_at) "
                    "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                    (source_key, digest),
                )
        except ApplicationStateError, OSError, sqlite3.Error:
            raise OperationHistoryError("Legacy operation history could not be migrated") from None
