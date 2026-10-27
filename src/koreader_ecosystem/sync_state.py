from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from koreader_ecosystem.application_state import ApplicationState, ApplicationStateError


class SyncStateError(RuntimeError):
    """Raised when the private KOReader–Calibre state cannot be used safely."""


_SCHEMA_VERSION = 2
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IMPORT_FIELDS = frozenset(
    {
        "koreader_progress",
        "koreader_status",
        "koreader_position",
        "koreader_rating",
        "koreader_note",
        "koreader_annotations",
    }
)


def _safe_path(path: Path) -> Path:
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise SyncStateError("State database path must be absolute")
    current = path
    while current != current.parent:
        if current.is_symlink() or current.is_junction():
            raise SyncStateError("State database path cannot use a link")
        current = current.parent
    if path.exists() and not path.is_file():
        raise SyncStateError("State database path is not a file")
    return path


def _json_text(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except TypeError, ValueError, OverflowError:
        raise SyncStateError("State data is not valid JSON") from None


def _json_object(value: str) -> dict[str, object]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError, TypeError:
        raise SyncStateError("Stored state data is invalid") from None
    if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
        raise SyncStateError("Stored state data is invalid")
    return decoded


class SyncState:
    """Versioned SQLite storage kept outside the Git checkout."""

    def __init__(self, database: Path) -> None:
        self.database = _safe_path(Path(database))
        try:
            ApplicationState(self.database)
        except SyncStateError:
            raise
        except ApplicationStateError:
            raise SyncStateError("State database could not be initialized") from None
        except OSError, sqlite3.Error:
            raise SyncStateError("State database could not be initialized") from None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.database, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            yield connection
            connection.commit()
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _validate_digest(value: str, setting: str) -> None:
        if not isinstance(value, str) or not _DIGEST.fullmatch(value):
            raise SyncStateError(f"{setting} must be a SHA-256 digest")

    def save_preview(
        self,
        preview_id: str,
        plan_digest: str,
        created_at: str,
        items: Sequence[Mapping[str, object]],
    ) -> None:
        self._validate_digest(preview_id, "Preview ID")
        self._validate_digest(plan_digest, "Plan digest")
        if preview_id != plan_digest:
            raise SyncStateError("Preview ID must match its plan digest")
        if not isinstance(created_at, str) or not created_at:
            raise SyncStateError("Preview timestamp is invalid")

        normalized_items: list[tuple[str, str, str, int | None, str | None, str]] = []
        seen_keys: set[str] = set()
        for item in items:
            sidecar_key = item.get("sidecar_key")
            relative_path = item.get("relative_path")
            source_digest = item.get("source_digest")
            book_id = item.get("book_id")
            calibre_uuid = item.get("calibre_uuid")
            if not isinstance(sidecar_key, str) or not _DIGEST.fullmatch(sidecar_key):
                raise SyncStateError("Preview contains an invalid sidecar key")
            if sidecar_key in seen_keys:
                raise SyncStateError("Preview contains duplicate sidecar keys")
            seen_keys.add(sidecar_key)
            if not isinstance(relative_path, str) or not relative_path:
                raise SyncStateError("Preview contains an invalid sidecar path")
            if not isinstance(source_digest, str) or not _DIGEST.fullmatch(source_digest):
                raise SyncStateError("Preview contains an invalid source digest")
            if book_id is not None and (
                not isinstance(book_id, int) or isinstance(book_id, bool) or book_id < 1
            ):
                raise SyncStateError("Preview contains an invalid Calibre book ID")
            if calibre_uuid is not None and not isinstance(calibre_uuid, str):
                raise SyncStateError("Preview contains an invalid Calibre UUID")
            normalized_items.append(
                (
                    sidecar_key,
                    relative_path,
                    source_digest,
                    book_id,
                    calibre_uuid,
                    _json_text(dict(item)),
                )
            )

        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT plan_digest FROM previews WHERE preview_id = ?", (preview_id,)
                ).fetchone()
                if row is not None:
                    if row["plan_digest"] != plan_digest:
                        raise SyncStateError("Preview ID is already bound to another plan")
                    return

                connection.execute(
                    "INSERT INTO previews(preview_id, plan_digest, created_at) VALUES (?, ?, ?)",
                    (preview_id, plan_digest, created_at),
                )
                connection.executemany(
                    """
                    INSERT INTO preview_items(
                        preview_id, sidecar_key, relative_path, source_digest,
                        book_id, calibre_uuid, item_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (preview_id, key, path, digest, book_id, calibre_uuid, item_json)
                        for key, path, digest, book_id, calibre_uuid, item_json in normalized_items
                    ],
                )
        except SyncStateError:
            raise
        except sqlite3.Error:
            raise SyncStateError("Preview could not be stored") from None

    def load_preview(self, preview_id: str) -> dict[str, object]:
        self._validate_digest(preview_id, "Preview ID")
        try:
            with self._connect() as connection:
                preview = connection.execute(
                    "SELECT preview_id, plan_digest, created_at FROM previews WHERE preview_id = ?",
                    (preview_id,),
                ).fetchone()
                if preview is None:
                    raise SyncStateError("Preview does not exist")
                rows = connection.execute(
                    "SELECT item_json FROM preview_items WHERE preview_id = ? ORDER BY sidecar_key",
                    (preview_id,),
                ).fetchall()
                items = [_json_object(row["item_json"]) for row in rows]
        except SyncStateError:
            raise
        except sqlite3.Error:
            raise SyncStateError("Preview could not be read") from None
        return {
            "preview_id": preview["preview_id"],
            "plan_digest": preview["plan_digest"],
            "created_at": preview["created_at"],
            "items": items,
        }

    def save_manual_mapping(self, sidecar_key: str, book_id: int) -> None:
        self._validate_digest(sidecar_key, "Sidecar key")
        if not isinstance(book_id, int) or isinstance(book_id, bool) or book_id < 1:
            raise SyncStateError("Manual mapping requires a positive Calibre book ID")
        updated_at = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO manual_maps(sidecar_key, book_id, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(sidecar_key) DO UPDATE SET
                        book_id = excluded.book_id,
                        updated_at = excluded.updated_at
                    """,
                    (sidecar_key, book_id, updated_at),
                )
        except sqlite3.Error:
            raise SyncStateError("Manual mapping could not be stored") from None

    def manual_mappings(self) -> dict[str, int]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT sidecar_key, book_id FROM manual_maps ORDER BY sidecar_key"
                ).fetchall()
        except sqlite3.Error:
            raise SyncStateError("Manual mappings could not be read") from None
        return {str(row["sidecar_key"]): int(row["book_id"]) for row in rows}

    def load_checkpoint(self, calibre_uuid: str) -> dict[str, object] | None:
        if not isinstance(calibre_uuid, str) or not calibre_uuid.strip():
            raise SyncStateError("Calibre UUID is invalid")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT book_id, sidecar_key, source_digest, values_json, applied_at
                    FROM book_checkpoints WHERE calibre_uuid = ?
                    """,
                    (calibre_uuid,),
                ).fetchone()
        except sqlite3.Error:
            raise SyncStateError("Book checkpoint could not be read") from None
        if row is None:
            return None
        values = _json_object(str(row["values_json"]))
        return {
            "book_id": int(row["book_id"]),
            "sidecar_key": str(row["sidecar_key"]),
            "source_digest": str(row["source_digest"]),
            "values": values,
            "applied_at": str(row["applied_at"]),
        }

    def record_applied_field(
        self,
        preview_id: str,
        sidecar_key: str,
        calibre_uuid: str,
        book_id: int,
        source_digest: str,
        field: str,
        value: object,
        applied_at: str,
    ) -> None:
        self._validate_digest(preview_id, "Preview ID")
        self._validate_digest(sidecar_key, "Sidecar key")
        self._validate_digest(source_digest, "Source digest")
        if field not in _IMPORT_FIELDS:
            raise SyncStateError("Applied field is not a KOReader column")
        if (
            not isinstance(calibre_uuid, str)
            or not calibre_uuid.strip()
            or not isinstance(book_id, int)
            or isinstance(book_id, bool)
            or book_id < 1
            or not isinstance(applied_at, str)
            or not applied_at
        ):
            raise SyncStateError("Applied checkpoint metadata is invalid")

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT item_json FROM preview_items
                    WHERE preview_id = ? AND sidecar_key = ? AND calibre_uuid = ? AND book_id = ?
                    """,
                    (preview_id, sidecar_key, calibre_uuid, book_id),
                ).fetchone()
                if row is None:
                    raise SyncStateError("Applied field does not belong to this preview")
                item = _json_object(str(row["item_json"]))
                applied_values_raw = item.get("applied_values", {})
                if not isinstance(applied_values_raw, dict):
                    raise SyncStateError("Preview checkpoint data is invalid")
                applied_values = dict(applied_values_raw)
                applied_values[field] = value
                item["applied_values"] = applied_values
                item["applied_at"] = applied_at
                item_json = _json_text(item)
                connection.execute(
                    """
                    UPDATE preview_items SET item_json = ?
                    WHERE preview_id = ? AND sidecar_key = ?
                    """,
                    (item_json, preview_id, sidecar_key),
                )

                checkpoint = connection.execute(
                    "SELECT values_json FROM book_checkpoints WHERE calibre_uuid = ?",
                    (calibre_uuid,),
                ).fetchone()
                checkpoint_values: dict[str, object] = (
                    _json_object(str(checkpoint["values_json"])) if checkpoint else {}
                )
                checkpoint_values[field] = value
                connection.execute(
                    """
                    INSERT INTO book_checkpoints(
                        calibre_uuid, book_id, sidecar_key, source_digest, values_json, applied_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(calibre_uuid) DO UPDATE SET
                        book_id = excluded.book_id,
                        sidecar_key = excluded.sidecar_key,
                        source_digest = excluded.source_digest,
                        values_json = excluded.values_json,
                        applied_at = excluded.applied_at
                    """,
                    (
                        calibre_uuid,
                        book_id,
                        sidecar_key,
                        source_digest,
                        _json_text(checkpoint_values),
                        applied_at,
                    ),
                )
        except SyncStateError:
            raise
        except sqlite3.Error:
            raise SyncStateError("Applied checkpoint could not be stored") from None
