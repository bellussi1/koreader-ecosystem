from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from koreader_ecosystem.application_state import ApplicationState
from koreader_ecosystem.platform_csv import (
    MAX_CSV_ROWS,
    CsvReadingRecord,
    CsvRowIssue,
    ReadingCsvPreview,
)

CsvRowType = Literal["record", "issue"]
ImportStatus = Literal["preview", "applied", "failed"]
_SUPPORTED_SOURCES = frozenset({"goodreads", "storygraph", "maratona"})
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_RECORD_JSON_BYTES = 16_777_216


class PlatformCsvStoreError(RuntimeError):
    """A safe reason why a CSV import could not be persisted or retrieved."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class PlatformCsvImportBatch:
    import_id: str
    source: str
    source_digest: str
    status: ImportStatus
    created_at: str
    completed_at: str | None
    record_count: int
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class StoredCsvEntry:
    row_number: int
    row_type: CsvRowType
    record: dict[str, object] | None
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class StoredCsvEntryPage:
    entries: tuple[StoredCsvEntry, ...]
    has_more: bool


@dataclass(frozen=True, slots=True)
class AppliedCsvRecordPage:
    records: tuple[dict[str, object], ...]
    has_more: bool


@dataclass(frozen=True, slots=True)
class PlatformCsvImportPage:
    batches: tuple[PlatformCsvImportBatch, ...]
    has_more: bool


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _import_id(source: str, source_digest: str) -> str:
    return hashlib.sha256(f"{source}\x00{source_digest}".encode("ascii")).hexdigest()


def _canonical_record(record: CsvReadingRecord) -> str:
    try:
        encoded = json.dumps(
            asdict(record),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except TypeError, ValueError, OverflowError:
        raise PlatformCsvStoreError("invalid_preview") from None
    if len(encoded.encode("utf-8")) > _MAX_RECORD_JSON_BYTES:
        raise PlatformCsvStoreError("invalid_preview")
    return encoded


def _valid_row_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 2 <= value <= MAX_CSV_ROWS + 1


def _validate_preview(
    preview: ReadingCsvPreview,
) -> tuple[tuple[int, str, str | None, str | None], ...]:
    if (
        not isinstance(preview, ReadingCsvPreview)
        or not isinstance(preview.source, str)
        or preview.source not in _SUPPORTED_SOURCES
        or not isinstance(preview.source_digest, str)
        or not _DIGEST.fullmatch(preview.source_digest)
        or not isinstance(preview.mapping_digest, str)
        or not _DIGEST.fullmatch(preview.mapping_digest)
        or not isinstance(preview.records, tuple)
        or not isinstance(preview.issues, tuple)
        or len(preview.records) + len(preview.issues) > MAX_CSV_ROWS
    ):
        raise PlatformCsvStoreError("invalid_preview")

    rows: list[tuple[int, str, str | None, str | None]] = []
    seen: set[int] = set()
    try:
        for record in preview.records:
            if (
                not isinstance(record, CsvReadingRecord)
                or not _valid_row_number(record.row_number)
                or record.row_number in seen
                or not isinstance(record.title, str)
                or not record.title
                or not isinstance(record.authors, tuple)
                or not record.authors
                or any(not isinstance(author, str) or not author for author in record.authors)
            ):
                raise PlatformCsvStoreError("invalid_preview")
            seen.add(record.row_number)
            rows.append((record.row_number, "record", _canonical_record(record), None))

        for issue in preview.issues:
            if (
                not isinstance(issue, CsvRowIssue)
                or not _valid_row_number(issue.row_number)
                or issue.row_number in seen
                or not isinstance(issue.reason_code, str)
                or not _REASON_CODE.fullmatch(issue.reason_code)
            ):
                raise PlatformCsvStoreError("invalid_preview")
            seen.add(issue.row_number)
            rows.append((issue.row_number, "issue", None, issue.reason_code))
    except AttributeError, TypeError:
        raise PlatformCsvStoreError("invalid_preview") from None
    return tuple(sorted(rows, key=lambda row: row[0]))


class PlatformCsvStore:
    """Persist local-only reading-platform CSV previews and confirmed imports."""

    def __init__(self, state: ApplicationState) -> None:
        self.state = state

    def stage_preview(self, preview: ReadingCsvPreview) -> PlatformCsvImportBatch:
        """Store normalized records and safe row issues without activating the import."""
        rows = _validate_preview(preview)
        now = _utc_now()
        import_id = _import_id(preview.source, preview.source_digest)
        try:
            with self.state.connect() as connection:
                existing = connection.execute(
                    "SELECT * FROM import_batches WHERE source=? AND source_digest=?",
                    (preview.source, preview.source_digest),
                ).fetchone()
                if existing is not None:
                    if existing["mapping_digest"] != preview.mapping_digest:
                        raise PlatformCsvStoreError("mapping_conflict")
                    if existing["status"] != "failed":
                        return self._batch(existing)
                    connection.execute(
                        "DELETE FROM csv_import_rows WHERE import_id=?", (existing["import_id"],)
                    )
                    connection.execute(
                        "UPDATE import_batches SET status='preview', completed_at=NULL, "
                        "record_count=?, reason_code=NULL WHERE import_id=?",
                        (len(preview.records), existing["import_id"]),
                    )
                    import_id = existing["import_id"]
                else:
                    connection.execute(
                        """
                        INSERT INTO import_batches(
                            import_id, source, source_digest, status, created_at, record_count,
                            mapping_digest
                        ) VALUES (?, ?, ?, 'preview', ?, ?, ?)
                        """,
                        (
                            import_id,
                            preview.source,
                            preview.source_digest,
                            now,
                            len(preview.records),
                            preview.mapping_digest,
                        ),
                    )
                connection.executemany(
                    """
                    INSERT INTO csv_import_rows(
                        import_id, row_number, row_type, record_json, reason_code
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    ((import_id, *row) for row in rows),
                )
                stored = connection.execute(
                    "SELECT * FROM import_batches WHERE import_id=?", (import_id,)
                ).fetchone()
                if stored is None:
                    raise PlatformCsvStoreError("persistence_failed")
                return self._batch(stored)
        except PlatformCsvStoreError:
            raise
        except sqlite3.Error:
            raise PlatformCsvStoreError("persistence_failed") from None

    def get_import(self, import_id: str) -> PlatformCsvImportBatch:
        if not isinstance(import_id, str) or not import_id:
            raise PlatformCsvStoreError("invalid_import")
        try:
            with self.state.connect() as connection:
                return self._batch(self._get_batch_row(connection, import_id))
        except PlatformCsvStoreError:
            raise
        except sqlite3.Error:
            raise PlatformCsvStoreError("persistence_failed") from None

    def list_imports(
        self, *, offset: int, limit: int, source: str | None = None
    ) -> PlatformCsvImportPage:
        """List CSV batches without returning stored row contents or review text."""
        _validate_pagination(offset=offset, limit=limit)
        if source is not None and (not isinstance(source, str) or source not in _SUPPORTED_SOURCES):
            raise PlatformCsvStoreError("unsupported_source")
        try:
            with self.state.connect() as connection:
                if source is None:
                    rows = connection.execute(
                        "SELECT * FROM import_batches WHERE source IN (?, ?, ?) "
                        "ORDER BY created_at DESC, import_id LIMIT ? OFFSET ?",
                        (*sorted(_SUPPORTED_SOURCES), limit + 1, offset),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT * FROM import_batches WHERE source=? "
                        "ORDER BY created_at DESC, import_id LIMIT ? OFFSET ?",
                        (source, limit + 1, offset),
                    ).fetchall()
            return PlatformCsvImportPage(
                tuple(self._batch(row) for row in rows[:limit]),
                len(rows) > limit,
            )
        except PlatformCsvStoreError:
            raise
        except sqlite3.Error:
            raise PlatformCsvStoreError("persistence_failed") from None

    def get_preview_page(self, import_id: str, *, offset: int, limit: int) -> StoredCsvEntryPage:
        """Read a bounded page of staged records and safe issues for review."""
        _validate_pagination(offset=offset, limit=limit)
        try:
            with self.state.connect() as connection:
                self._get_batch_row(connection, import_id)
                rows = connection.execute(
                    "SELECT row_number, row_type, record_json, reason_code "
                    "FROM csv_import_rows WHERE import_id=? ORDER BY row_number LIMIT ? OFFSET ?",
                    (import_id, limit + 1, offset),
                ).fetchall()
            entries: list[StoredCsvEntry] = []
            for row in rows[:limit]:
                record = json.loads(row["record_json"]) if row["record_json"] is not None else None
                if record is not None and not isinstance(record, dict):
                    raise PlatformCsvStoreError("invalid_stored_data")
                entries.append(
                    StoredCsvEntry(
                        row_number=row["row_number"],
                        row_type=cast(CsvRowType, row["row_type"]),
                        record=record,
                        reason_code=row["reason_code"],
                    )
                )
            return StoredCsvEntryPage(tuple(entries), len(rows) > limit)
        except PlatformCsvStoreError:
            raise
        except sqlite3.Error:
            raise PlatformCsvStoreError("persistence_failed") from None
        except TypeError, ValueError:
            raise PlatformCsvStoreError("invalid_stored_data") from None

    def confirm_import(
        self, import_id: str, source_digest: str, *, confirmed: bool = False
    ) -> PlatformCsvImportBatch:
        """Activate a staged import only after explicit confirmation of its digest."""
        if (
            not isinstance(source_digest, str)
            or not _DIGEST.fullmatch(source_digest)
            or not isinstance(confirmed, bool)
        ):
            raise PlatformCsvStoreError("invalid_import")
        now = _utc_now()
        try:
            with self.state.connect() as connection:
                row = self._get_batch_row(connection, import_id)
                if row["source_digest"] != source_digest:
                    raise PlatformCsvStoreError("source_digest_mismatch")
                if row["status"] == "applied" or not confirmed:
                    return self._batch(row)
                if row["status"] != "preview":
                    raise PlatformCsvStoreError("import_not_pending")
                connection.execute(
                    "UPDATE import_batches SET status='applied', completed_at=?, reason_code=NULL "
                    "WHERE import_id=? AND status='preview'",
                    (now, import_id),
                )
                return self._batch(self._get_batch_row(connection, import_id))
        except PlatformCsvStoreError:
            raise
        except sqlite3.Error:
            raise PlatformCsvStoreError("persistence_failed") from None

    def fail_import(self, import_id: str, reason_code: str) -> PlatformCsvImportBatch:
        """Mark a pending preview failed without retaining free-form error text."""
        if not isinstance(reason_code, str) or not _REASON_CODE.fullmatch(reason_code):
            raise PlatformCsvStoreError("invalid_reason_code")
        try:
            with self.state.connect() as connection:
                row = self._get_batch_row(connection, import_id)
                if row["status"] != "preview":
                    raise PlatformCsvStoreError("import_not_pending")
                connection.execute(
                    "UPDATE import_batches SET status='failed', completed_at=?, reason_code=? "
                    "WHERE import_id=? AND status='preview'",
                    (_utc_now(), reason_code, import_id),
                )
                return self._batch(self._get_batch_row(connection, import_id))
        except PlatformCsvStoreError:
            raise
        except sqlite3.Error:
            raise PlatformCsvStoreError("persistence_failed") from None

    def get_applied_records(
        self, import_id: str, *, offset: int, limit: int
    ) -> AppliedCsvRecordPage:
        """Read imported records only when the batch has been confirmed."""
        _validate_pagination(offset=offset, limit=limit)
        try:
            with self.state.connect() as connection:
                batch = self._get_batch_row(connection, import_id)
                if batch["status"] != "applied":
                    return AppliedCsvRecordPage((), False)
                rows = connection.execute(
                    "SELECT record_json FROM csv_import_rows "
                    "WHERE import_id=? AND row_type='record' ORDER BY row_number LIMIT ? OFFSET ?",
                    (import_id, limit + 1, offset),
                ).fetchall()
            records: list[dict[str, object]] = []
            for row in rows[:limit]:
                record = json.loads(row["record_json"])
                if not isinstance(record, dict):
                    raise PlatformCsvStoreError("invalid_stored_data")
                records.append(record)
            return AppliedCsvRecordPage(tuple(records), len(rows) > limit)
        except PlatformCsvStoreError:
            raise
        except sqlite3.Error:
            raise PlatformCsvStoreError("persistence_failed") from None
        except TypeError, ValueError:
            raise PlatformCsvStoreError("invalid_stored_data") from None

    @staticmethod
    def _batch(row: sqlite3.Row) -> PlatformCsvImportBatch:
        return PlatformCsvImportBatch(
            import_id=row["import_id"],
            source=row["source"],
            source_digest=row["source_digest"],
            status=cast(ImportStatus, row["status"]),
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            record_count=row["record_count"],
            reason_code=row["reason_code"],
        )

    @staticmethod
    def _get_batch_row(connection: sqlite3.Connection, import_id: str) -> sqlite3.Row:
        if not isinstance(import_id, str) or not import_id:
            raise PlatformCsvStoreError("invalid_import")
        row = connection.execute(
            "SELECT * FROM import_batches WHERE import_id=? AND source IN (?, ?, ?)",
            (import_id, "goodreads", "storygraph", "maratona"),
        ).fetchone()
        if row is None:
            raise PlatformCsvStoreError("import_not_found")
        return cast(sqlite3.Row, row)


def _validate_pagination(*, offset: int, limit: int) -> None:
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or not 0 <= offset <= MAX_CSV_ROWS
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 100
    ):
        raise PlatformCsvStoreError("invalid_pagination")
