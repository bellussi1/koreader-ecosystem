from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from koreader_ecosystem.application_state import ApplicationState
from koreader_ecosystem.koreader_reading_history import (
    PageEvent,
    ReadingBook,
    ReadingHistoryPreview,
    ReadingSidecar,
)


class ReadingHistoryStateError(RuntimeError):
    """Raised when a KOReader import cannot be recorded safely."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class ReadingImportBatch:
    import_id: str
    source: str
    source_digest: str
    status: Literal["preview", "applied", "failed"]
    created_at: str
    completed_at: str | None
    record_count: int
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class StoredPageEvent:
    event_id: str
    identity_id: str
    source_event_id: str
    occurred_at: str
    observed_at: str
    source_digest: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class StoredPageEventPage:
    events: tuple[StoredPageEvent, ...]
    has_more: bool


_SOURCE = "koreader"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_JSON_BYTES = 16_777_216


def _iso_utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ReadingHistoryStateError("invalid_preview")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_json(value: object, *, reason_code: str = "invalid_preview") -> str:
    try:
        result = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except TypeError, ValueError, OverflowError:
        raise ReadingHistoryStateError(reason_code) from None
    if len(result.encode("utf-8")) > _MAX_JSON_BYTES:
        raise ReadingHistoryStateError(reason_code)
    return result


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _import_id(source_digest: str) -> str:
    return _sha256(f"{_SOURCE}\x00{source_digest}".encode("ascii"))


def _identity_id(external_type: str, external_id: str) -> str:
    return _sha256(f"{_SOURCE}\x00{external_type}\x00{external_id}".encode())


def _datetime_from_epoch(value: datetime) -> str:
    return _iso_utc(value)


class KoreaderReadingHistoryStore:
    """Persist reviewed KOReader history in the private application database."""

    def __init__(self, state: ApplicationState) -> None:
        self.state = state

    def stage_preview(self, preview: ReadingHistoryPreview) -> ReadingImportBatch:
        """Persist an inactive preview, its events, and immutable annotation versions."""
        if (
            not isinstance(preview, ReadingHistoryPreview)
            or not isinstance(preview.source_digest, str)
            or not _DIGEST.fullmatch(preview.source_digest)
        ):
            raise ReadingHistoryStateError("invalid_preview")

        now = _iso_utc(preview.observed_at)
        import_id = _import_id(preview.source_digest)
        record_count = (
            len(preview.books)
            + len(preview.events)
            + len(preview.sidecars)
            + sum(len(sidecar.annotations) for sidecar in preview.sidecars)
        )
        try:
            with self.state.connect() as connection:
                existing = connection.execute(
                    "SELECT * FROM import_batches WHERE source=? AND source_digest=?",
                    (_SOURCE, preview.source_digest),
                ).fetchone()
                if existing is not None:
                    if existing["status"] == "failed":
                        previous_import_id = existing["import_id"]
                        connection.execute(
                            "UPDATE import_batches SET status='preview', completed_at=NULL, "
                            "reason_code=NULL WHERE import_id=?",
                            (previous_import_id,),
                        )
                        refreshed = connection.execute(
                            "SELECT * FROM import_batches WHERE import_id=?",
                            (previous_import_id,),
                        ).fetchone()
                        if refreshed is None:
                            raise ReadingHistoryStateError("persistence_failed")
                        return self._batch(refreshed)
                    return self._batch(cast(sqlite3.Row, existing))

                connection.execute(
                    """
                    INSERT INTO import_batches(
                        import_id, source, source_digest, status, created_at, record_count
                    ) VALUES (?, ?, ?, 'preview', ?, ?)
                    """,
                    (import_id, _SOURCE, preview.source_digest, now, record_count),
                )

                books_by_id: dict[int, str] = {}
                for book in preview.books:
                    identity_id = self._ensure_identity(
                        connection,
                        external_type="book_id",
                        external_id=str(book.koreader_book_id),
                        observed_at=now,
                    )
                    books_by_id[book.koreader_book_id] = identity_id

                for book in preview.books:
                    self._store_book_snapshot(
                        connection,
                        book,
                        identity_id=books_by_id[book.koreader_book_id],
                        import_id=import_id,
                        source_digest=preview.source_digest,
                        observed_at=now,
                    )

                for event in preview.events:
                    page_identity_id = books_by_id.get(event.koreader_book_id)
                    if page_identity_id is None or event.source_digest != preview.source_digest:
                        raise ReadingHistoryStateError("invalid_preview")
                    self._store_page_event(
                        connection,
                        event,
                        identity_id=page_identity_id,
                        import_id=import_id,
                        observed_at=now,
                    )

                for sidecar in preview.sidecars:
                    identity_id = self._ensure_identity(
                        connection,
                        external_type="sidecar_id",
                        external_id=sidecar.sidecar_id,
                        observed_at=now,
                    )
                    linked_book_id = sidecar.koreader_book_id
                    if linked_book_id is not None:
                        linked_identity = books_by_id.get(linked_book_id)
                        if linked_identity is None:
                            raise ReadingHistoryStateError("invalid_preview")
                        self._link_exact_identities(
                            connection,
                            sidecar_identity=identity_id,
                            book_identity=linked_identity,
                            observed_at=now,
                        )
                    self._store_sidecar_snapshot(
                        connection,
                        sidecar,
                        identity_id=identity_id,
                        import_id=import_id,
                        observed_at=now,
                    )
                    for annotation in sidecar.annotations:
                        self._store_annotation(
                            connection,
                            sidecar,
                            annotation,
                            identity_id=identity_id,
                            import_id=import_id,
                            source_digest=preview.source_digest,
                            observed_at=now,
                        )

                row = connection.execute(
                    "SELECT * FROM import_batches WHERE import_id=?", (import_id,)
                ).fetchone()
                if row is None:
                    raise ReadingHistoryStateError("persistence_failed")
                return self._batch(row)
        except ReadingHistoryStateError:
            raise
        except sqlite3.Error:
            raise ReadingHistoryStateError("persistence_failed") from None

    def confirm_import(self, import_id: str, source_digest: str) -> ReadingImportBatch:
        """Make an unchanged preview visible after the caller confirms its digest."""
        if (
            not isinstance(import_id, str)
            or not isinstance(source_digest, str)
            or not _DIGEST.fullmatch(source_digest)
        ):
            raise ReadingHistoryStateError("invalid_import")
        now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        try:
            with self.state.connect() as connection:
                row = self._get_batch_row(connection, import_id)
                if row["source_digest"] != source_digest:
                    raise ReadingHistoryStateError("source_digest_mismatch")
                if row["status"] == "applied":
                    return self._batch(row)
                if row["status"] != "preview":
                    raise ReadingHistoryStateError("import_not_pending")
                connection.execute(
                    "UPDATE import_batches SET status='applied', completed_at=?, reason_code=NULL "
                    "WHERE import_id=? AND status='preview'",
                    (now, import_id),
                )
                return self._batch(self._get_batch_row(connection, import_id))
        except ReadingHistoryStateError:
            raise
        except sqlite3.Error:
            raise ReadingHistoryStateError("persistence_failed") from None

    def fail_import(self, import_id: str, reason_code: str) -> ReadingImportBatch:
        """Mark an unconfirmed preview failed using a non-sensitive reason code."""
        if (
            not isinstance(import_id, str)
            or not isinstance(reason_code, str)
            or not _REASON_CODE.fullmatch(reason_code)
        ):
            raise ReadingHistoryStateError("invalid_reason_code")
        now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        try:
            with self.state.connect() as connection:
                row = self._get_batch_row(connection, import_id)
                if row["status"] != "preview":
                    raise ReadingHistoryStateError("import_not_pending")
                connection.execute(
                    "UPDATE import_batches SET status='failed', completed_at=?, reason_code=? "
                    "WHERE import_id=? AND status='preview'",
                    (now, reason_code, import_id),
                )
                return self._batch(self._get_batch_row(connection, import_id))
        except ReadingHistoryStateError:
            raise
        except sqlite3.Error:
            raise ReadingHistoryStateError("persistence_failed") from None

    def get_import(self, import_id: str) -> ReadingImportBatch:
        if not isinstance(import_id, str):
            raise ReadingHistoryStateError("invalid_import")
        try:
            with self.state.connect() as connection:
                return self._batch(self._get_batch_row(connection, import_id))
        except ReadingHistoryStateError:
            raise
        except sqlite3.Error:
            raise ReadingHistoryStateError("persistence_failed") from None

    def get_events(self, *, limit: int = 100, offset: int = 0) -> tuple[StoredPageEvent, ...]:
        """Read page events only after their import batch has been applied."""
        return self.get_events_page(limit=limit, offset=offset).events

    def get_events_page(self, *, limit: int = 100, offset: int = 0) -> StoredPageEventPage:
        """Read a bounded event page and determine exactly whether another page exists."""
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
            or isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
        ):
            raise ReadingHistoryStateError("invalid_pagination")
        try:
            with self.state.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT e.event_id, e.identity_id, e.source_event_id, e.occurred_at,
                           e.observed_at, e.source_digest, e.payload_json
                    FROM reading_events AS e
                    JOIN import_batches AS b ON b.import_id=e.import_id
                    WHERE b.status='applied' AND e.source=? AND e.event_type='page'
                    ORDER BY e.occurred_at, e.event_id LIMIT ? OFFSET ?
                    """,
                    (_SOURCE, limit + 1, offset),
                ).fetchall()
            events = tuple(
                StoredPageEvent(
                    event_id=row[0],
                    identity_id=row[1],
                    source_event_id=row[2],
                    occurred_at=row[3],
                    observed_at=row[4],
                    source_digest=row[5],
                    payload=json.loads(row[6]),
                )
                for row in rows[:limit]
            )
            return StoredPageEventPage(events, len(rows) > limit)
        except sqlite3.Error:
            raise ReadingHistoryStateError("persistence_failed") from None
        except TypeError, ValueError:
            raise ReadingHistoryStateError("invalid_stored_data") from None

    @staticmethod
    def _batch(row: sqlite3.Row) -> ReadingImportBatch:
        return ReadingImportBatch(
            import_id=row["import_id"],
            source=row["source"],
            source_digest=row["source_digest"],
            status=row["status"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            record_count=row["record_count"],
            reason_code=row["reason_code"],
        )

    @staticmethod
    def _get_batch_row(connection: sqlite3.Connection, import_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM import_batches WHERE import_id=? AND source=?",
            (import_id, _SOURCE),
        ).fetchone()
        if row is None:
            raise ReadingHistoryStateError("import_not_found")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _ensure_identity(
        connection: sqlite3.Connection,
        *,
        external_type: str,
        external_id: str,
        observed_at: str,
    ) -> str:
        if not external_id or len(external_id) > 512:
            raise ReadingHistoryStateError("invalid_preview")
        identity_id = _identity_id(external_type, external_id)
        connection.execute(
            """
            INSERT INTO book_identities(
                identity_id, provider, external_type, external_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, external_type, external_id) DO NOTHING
            """,
            (identity_id, _SOURCE, external_type, external_id, observed_at, observed_at),
        )
        row = connection.execute(
            "SELECT identity_id FROM book_identities "
            "WHERE provider=? AND external_type=? AND external_id=?",
            (_SOURCE, external_type, external_id),
        ).fetchone()
        if row is None or row[0] != identity_id:
            raise ReadingHistoryStateError("identity_conflict")
        connection.execute(
            "UPDATE book_identities SET updated_at=? WHERE identity_id=?",
            (observed_at, identity_id),
        )
        return identity_id

    @staticmethod
    def _link_exact_identities(
        connection: sqlite3.Connection,
        *,
        sidecar_identity: str,
        book_identity: str,
        observed_at: str,
    ) -> None:
        existing_links = connection.execute(
            "SELECT right_identity_id FROM identity_links WHERE left_identity_id=?",
            (sidecar_identity,),
        ).fetchall()
        if any(row[0] != book_identity for row in existing_links):
            raise ReadingHistoryStateError("sidecar_identity_conflict")
        connection.execute(
            """
            INSERT INTO identity_links(
                left_identity_id, right_identity_id, relation, confidence, created_at
            ) VALUES (?, ?, 'same_document', 'exact', ?)
            ON CONFLICT(left_identity_id, right_identity_id) DO NOTHING
            """,
            (sidecar_identity, book_identity, observed_at),
        )
        row = connection.execute(
            "SELECT relation, confidence FROM identity_links "
            "WHERE left_identity_id=? AND right_identity_id=?",
            (sidecar_identity, book_identity),
        ).fetchone()
        if tuple(row) != ("same_document", "exact"):
            raise ReadingHistoryStateError("identity_conflict")

    def _store_page_event(
        self,
        connection: sqlite3.Connection,
        event: PageEvent,
        *,
        identity_id: str,
        import_id: str,
        observed_at: str,
    ) -> None:
        if (
            isinstance(event.page, bool)
            or not isinstance(event.page, int)
            or event.page < 1
            or isinstance(event.duration_seconds, bool)
            or not isinstance(event.duration_seconds, int)
            or event.duration_seconds < 0
            or isinstance(event.total_pages, bool)
            or not isinstance(event.total_pages, int)
            or event.total_pages < event.page
            or not _DIGEST.fullmatch(event.event_id)
        ):
            raise ReadingHistoryStateError("invalid_preview")
        occurred_at = _datetime_from_epoch(event.start_time)
        try:
            timestamp = int(event.start_time.timestamp())
            source_identity = f"{event.koreader_book_id}\x00{event.page}\x00{timestamp}".encode(
                "ascii"
            )
        except OverflowError, OSError, ValueError, UnicodeEncodeError:
            raise ReadingHistoryStateError("invalid_preview") from None
        if _sha256(source_identity) != event.event_id:
            raise ReadingHistoryStateError("invalid_preview")
        payload: dict[str, object] = {
            "duration_seconds": event.duration_seconds,
            "koreader_book_id": event.koreader_book_id,
            "page": event.page,
            "source_digest": event.source_digest,
            "start_time": occurred_at,
            "total_pages": event.total_pages,
        }
        self._insert_event(
            connection,
            event_id=_sha256(f"page\x00{event.event_id}".encode("ascii")),
            identity_id=identity_id,
            source_event_id=event.event_id,
            event_type="page",
            occurred_at=occurred_at,
            observed_at=observed_at,
            source_digest=event.source_digest,
            payload=payload,
            import_id=import_id,
            compare_ignoring_source_digest=True,
        )

    def _store_book_snapshot(
        self,
        connection: sqlite3.Connection,
        book: ReadingBook,
        *,
        identity_id: str,
        import_id: str,
        source_digest: str,
        observed_at: str,
    ) -> None:
        if (
            isinstance(book.koreader_book_id, bool)
            or not isinstance(book.koreader_book_id, int)
            or book.koreader_book_id < 1
        ):
            raise ReadingHistoryStateError("invalid_preview")
        if not _DIGEST.fullmatch(source_digest):
            raise ReadingHistoryStateError("invalid_preview")
        last_opened_at = None if book.last_opened_at is None else _iso_utc(book.last_opened_at)
        payload: dict[str, object] = {
            "authors": book.authors,
            "highlights": book.highlights,
            "koreader_book_id": book.koreader_book_id,
            "language": book.language,
            "last_opened_at": last_opened_at,
            "notes": book.notes,
            "pages": book.pages,
            "partial_md5_checksum": book.partial_md5_checksum,
            "series": book.series,
            "sidecar_ids": list(book.sidecar_ids),
            "source_digest": source_digest,
            "title": book.title,
            "total_read_pages": book.total_read_pages,
            "total_read_time_seconds": book.total_read_time_seconds,
        }
        source_event_id = f"book:{book.koreader_book_id}:{source_digest}"
        self._insert_event(
            connection,
            event_id=_sha256(source_event_id.encode("ascii")),
            identity_id=identity_id,
            source_event_id=source_event_id,
            event_type="book_snapshot",
            occurred_at=last_opened_at,
            observed_at=observed_at,
            source_digest=source_digest,
            payload=payload,
            import_id=import_id,
            compare_ignoring_source_digest=False,
        )

    def _store_sidecar_snapshot(
        self,
        connection: sqlite3.Connection,
        sidecar: ReadingSidecar,
        *,
        identity_id: str,
        import_id: str,
        observed_at: str,
    ) -> None:
        if not _DIGEST.fullmatch(sidecar.sidecar_id) or not _DIGEST.fullmatch(
            sidecar.source_digest
        ):
            raise ReadingHistoryStateError("invalid_preview")
        if sidecar.koreader_book_id is not None and (
            isinstance(sidecar.koreader_book_id, bool)
            or not isinstance(sidecar.koreader_book_id, int)
            or sidecar.koreader_book_id < 1
        ):
            raise ReadingHistoryStateError("invalid_preview")
        if sidecar.progress is not None and (
            isinstance(sidecar.progress, bool)
            or not isinstance(sidecar.progress, (int, float))
            or not math.isfinite(sidecar.progress)
            or not 0 <= sidecar.progress <= 1
        ):
            raise ReadingHistoryStateError("invalid_preview")
        if sidecar.rating is not None and (
            isinstance(sidecar.rating, bool)
            or not isinstance(sidecar.rating, (int, float))
            or not math.isfinite(sidecar.rating)
            or not 0 <= sidecar.rating <= 5
        ):
            raise ReadingHistoryStateError("invalid_preview")
        if sidecar.status is not None and sidecar.status not in {
            "new",
            "reading",
            "complete",
            "abandoned",
        }:
            raise ReadingHistoryStateError("invalid_preview")
        if sidecar.note is not None and not isinstance(sidecar.note, str):
            raise ReadingHistoryStateError("invalid_preview")
        if sidecar.position is not None and not isinstance(sidecar.position, str):
            raise ReadingHistoryStateError("invalid_preview")
        payload: dict[str, object] = {
            "koreader_book_id": sidecar.koreader_book_id,
            "note": sidecar.note,
            "position": sidecar.position,
            "progress": sidecar.progress,
            "rating": sidecar.rating,
            "sidecar_id": sidecar.sidecar_id,
            "source_digest": sidecar.source_digest,
            "status": sidecar.status,
            "warnings": list(sidecar.warnings),
        }
        source_event_id = f"sidecar:{sidecar.sidecar_id}:{sidecar.source_digest}"
        occurred_at = None
        event_id = _sha256(source_event_id.encode("ascii"))
        self._insert_event(
            connection,
            event_id=event_id,
            identity_id=identity_id,
            source_event_id=source_event_id,
            event_type="sidecar_snapshot",
            occurred_at=occurred_at,
            observed_at=observed_at,
            source_digest=sidecar.source_digest,
            payload=payload,
            import_id=import_id,
            compare_ignoring_source_digest=False,
        )

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        *,
        event_id: str,
        identity_id: str,
        source_event_id: str,
        event_type: str,
        occurred_at: str | None,
        observed_at: str,
        source_digest: str,
        payload: dict[str, object],
        import_id: str,
        compare_ignoring_source_digest: bool,
    ) -> None:
        if not _DIGEST.fullmatch(source_digest):
            raise ReadingHistoryStateError("invalid_preview")
        payload_json = _canonical_json(payload)
        existing = connection.execute(
            """
            SELECT event_id, identity_id, event_type, occurred_at, source_digest, payload_json,
                   import_id
            FROM reading_events WHERE source=? AND source_event_id=?
            """,
            (_SOURCE, source_event_id),
        ).fetchone()
        if existing is not None:
            try:
                prior_payload = json.loads(existing[5])
            except TypeError, ValueError:
                raise ReadingHistoryStateError("invalid_stored_data") from None
            current_payload: object = payload
            if compare_ignoring_source_digest:
                if not isinstance(prior_payload, dict):
                    raise ReadingHistoryStateError("event_identity_conflict")
                prior_payload = {
                    key: value for key, value in prior_payload.items() if key != "source_digest"
                }
                current_payload = {
                    key: value for key, value in payload.items() if key != "source_digest"
                }
            if (
                existing[0] != event_id
                or existing[1] != identity_id
                or existing[2] != event_type
                or existing[3] != occurred_at
                or (not compare_ignoring_source_digest and existing[4] != source_digest)
                or _canonical_json(prior_payload) != _canonical_json(current_payload)
            ):
                raise ReadingHistoryStateError("event_identity_conflict")
            if existing[6] != import_id:
                batch = connection.execute(
                    "SELECT status FROM import_batches WHERE import_id=?", (existing[6],)
                ).fetchone()
                if batch is not None and batch[0] == "applied":
                    return
                if batch is not None and batch[0] == "preview":
                    raise ReadingHistoryStateError("pending_import_conflict")
                if batch is None or batch[0] != "failed":
                    raise ReadingHistoryStateError("invalid_stored_data")
                connection.execute(
                    "UPDATE reading_events SET import_id=?, source_digest=?, observed_at=?, "
                    "payload_json=? WHERE event_id=?",
                    (import_id, source_digest, observed_at, payload_json, event_id),
                )
            return
        connection.execute(
            """
            INSERT INTO reading_events(
                event_id, identity_id, source, source_event_id, event_type, occurred_at,
                observed_at, source_digest, payload_json, import_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                identity_id,
                _SOURCE,
                source_event_id,
                event_type,
                occurred_at,
                observed_at,
                source_digest,
                payload_json,
                import_id,
            ),
        )

    def _store_annotation(
        self,
        connection: sqlite3.Connection,
        sidecar: ReadingSidecar,
        annotation: dict[str, object],
        *,
        identity_id: str,
        import_id: str,
        source_digest: str,
        observed_at: str,
    ) -> None:
        payload_json = _canonical_json(annotation, reason_code="invalid_annotation")
        start = annotation.get("pos0")
        end = annotation.get("pos1")
        annotation_id_source = (
            f"position\x00{start}\x00{end}"
            if isinstance(start, str) and isinstance(end, str) and start and end
            else f"content\x00{payload_json}"
        )
        stable_id = _sha256(annotation_id_source.encode("utf-8"))
        source_annotation_id = f"{sidecar.sidecar_id}:{stable_id}"
        latest = connection.execute(
            """
            SELECT version, payload_json, import_id FROM annotation_versions
            WHERE source=? AND source_annotation_id=? ORDER BY version DESC LIMIT 1
            """,
            (_SOURCE, source_annotation_id),
        ).fetchone()
        if latest is not None and latest[1] == payload_json:
            if latest[2] == import_id:
                return
            prior_batch = connection.execute(
                "SELECT status FROM import_batches WHERE import_id=?", (latest[2],)
            ).fetchone()
            if prior_batch is not None and prior_batch[0] == "applied":
                return
            if prior_batch is not None and prior_batch[0] == "preview":
                raise ReadingHistoryStateError("pending_import_conflict")
        version = 1 if latest is None else int(latest[0]) + 1
        version_id = _sha256(
            f"{_SOURCE}\x00{source_annotation_id}\x00{version}\x00{source_digest}".encode()
        )
        connection.execute(
            """
            INSERT INTO annotation_versions(
                annotation_version_id, identity_id, source, source_annotation_id,
                version, source_digest, observed_at, payload_json, import_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                identity_id,
                _SOURCE,
                source_annotation_id,
                version,
                source_digest,
                observed_at,
                payload_json,
                import_id,
            ),
        )
