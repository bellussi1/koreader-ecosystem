from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from koreader_ecosystem.application_state import ApplicationState
from koreader_ecosystem.calibre_catalog import CalibreBook
from koreader_ecosystem.koreader_calibre import (
    PreviewItem,
    _canonical_json,
    _item_dict,
    _plan_row,
    _prepare_values,
)
from koreader_ecosystem.sync_state import SyncState, SyncStateError


class ReadingHistoryProjectionError(RuntimeError):
    """Raised when local KOReader history cannot be projected safely to Calibre."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class ReadingHistoryProjectionIssue:
    koreader_book_id: int | None
    title: str | None
    reason_code: str


@dataclass(frozen=True, slots=True)
class CalibreReadingProjectionPreview:
    projection_id: str
    import_id: str
    source_digest: str
    items: tuple[PreviewItem, ...]
    issues: tuple[ReadingHistoryProjectionIssue, ...]


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SOURCE = "koreader"
_SUMMARY_FIELDS = {
    "progress": "koreader_progress",
    "status": "koreader_status",
    "position": "koreader_position",
    "rating": "koreader_rating",
    "note": "koreader_note",
}


def _read_json_object(value: object) -> dict[str, object]:
    try:
        decoded = json.loads(value) if isinstance(value, str) else None
    except json.JSONDecodeError, RecursionError:
        decoded = None
    if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
        raise ReadingHistoryProjectionError("stored_history_invalid")
    return decoded


def _read_batch(connection: sqlite3.Connection, import_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM import_batches WHERE import_id=? AND source=?",
        (import_id, _SOURCE),
    ).fetchone()
    if row is None:
        raise ReadingHistoryProjectionError("import_not_found")
    if row["status"] != "applied":
        raise ReadingHistoryProjectionError("import_not_applied")
    return cast(sqlite3.Row, row)


def _calibre_link(
    connection: sqlite3.Connection, koreader_identity_id: str
) -> tuple[str, str] | None:
    rows = connection.execute(
        """
        SELECT target.identity_id, target.external_id
        FROM identity_links AS link
        JOIN book_identities AS target ON target.identity_id=link.right_identity_id
        WHERE link.left_identity_id=? AND link.relation='same_edition'
          AND link.confidence='confirmed' AND target.provider='calibre'
          AND target.external_type='book_uuid'
        ORDER BY target.external_id
        """,
        (koreader_identity_id,),
    ).fetchall()
    if len(rows) > 1:
        raise ReadingHistoryProjectionError("identity_mapping_ambiguous")
    if not rows:
        return None
    return str(rows[0][0]), str(rows[0][1])


def _read_sidecar(
    connection: sqlite3.Connection,
    *,
    sidecar_id: str,
    koreader_identity_id: str,
) -> tuple[str, dict[str, object], tuple[dict[str, object], ...]] | None:
    identity = connection.execute(
        """
        SELECT identity_id FROM book_identities
        WHERE provider='koreader' AND external_type='sidecar_id' AND external_id=?
        """,
        (sidecar_id,),
    ).fetchone()
    if identity is None:
        raise ReadingHistoryProjectionError("sidecar_identity_missing")
    sidecar_identity_id = str(identity[0])
    links = connection.execute(
        """
        SELECT right_identity_id FROM identity_links
        WHERE left_identity_id=? AND relation='same_document' AND confidence='exact'
        """,
        (sidecar_identity_id,),
    ).fetchall()
    if len(links) != 1 or str(links[0][0]) != koreader_identity_id:
        raise ReadingHistoryProjectionError("sidecar_identity_conflict")
    snapshot = connection.execute(
        """
        SELECT e.payload_json, e.source_digest
        FROM reading_events AS e
        JOIN import_batches AS batch ON batch.import_id=e.import_id
        WHERE e.source=? AND e.identity_id=? AND e.event_type='sidecar_snapshot'
          AND batch.status='applied'
        ORDER BY e.observed_at DESC, e.event_id DESC LIMIT 1
        """,
        (_SOURCE, sidecar_identity_id),
    ).fetchone()
    if snapshot is None:
        return None
    payload = _read_json_object(snapshot[0])
    if payload.get("sidecar_id") != sidecar_id:
        raise ReadingHistoryProjectionError("sidecar_identity_conflict")

    rows = connection.execute(
        """
        SELECT version.source_annotation_id, version.version, version.payload_json
        FROM annotation_versions AS version
        JOIN import_batches AS batch ON batch.import_id=version.import_id
        WHERE version.source=? AND version.identity_id=? AND batch.status='applied'
        ORDER BY version.version DESC
        """,
        (_SOURCE, sidecar_identity_id),
    ).fetchall()
    latest: dict[str, tuple[int, dict[str, object]]] = {}
    for row in rows:
        annotation_id = str(row[0])
        if annotation_id in latest:
            continue
        annotation = _read_json_object(row[2])
        stable_id = annotation_id.rsplit(":", 1)[-1]
        latest[annotation_id] = (int(row[1]), {"id": stable_id, "annotation": annotation})
    annotations = tuple(value[1] for _, value in sorted(latest.items(), key=lambda item: item[0]))
    return str(snapshot[1]), payload, annotations


def _collect_candidates(
    connection: sqlite3.Connection,
    *,
    sidecar_ids: tuple[str, ...],
    koreader_identity_id: str,
) -> tuple[dict[str, object], tuple[str, ...], tuple[str, ...]]:
    values_by_field: dict[str, list[object]] = {field: [] for field in _SUMMARY_FIELDS.values()}
    warning_codes: set[str] = set()
    observed_sidecars: list[str] = []
    annotations_by_id: dict[str, dict[str, object]] = {}
    for sidecar_id in sidecar_ids:
        result = _read_sidecar(
            connection,
            sidecar_id=sidecar_id,
            koreader_identity_id=koreader_identity_id,
        )
        if result is None:
            continue
        _source_digest, payload, annotations = result
        observed_sidecars.append(sidecar_id)
        raw_warnings = payload.get("warnings", [])
        if isinstance(raw_warnings, list):
            warning_codes.update(warning for warning in raw_warnings if isinstance(warning, str))
        for source_field, target_field in _SUMMARY_FIELDS.items():
            value = payload.get(source_field)
            if value is not None:
                values_by_field[target_field].append(value)
        for annotation in annotations:
            annotation_id = annotation.get("id")
            if isinstance(annotation_id, str):
                annotations_by_id[annotation_id] = annotation

    candidates: dict[str, object] = {}
    for field, values in values_by_field.items():
        unique_values = {_canonical_json(value) for value in values}
        if len(unique_values) > 1:
            warning_codes.add("multiple_sidecars_conflict")
            continue
        if values and not (field == "koreader_progress" and "progress_conflict" in warning_codes):
            candidates[field] = values[0]
    if annotations_by_id:
        candidates["koreader_annotations"] = _canonical_json(
            [annotations_by_id[key] for key in sorted(annotations_by_id)]
        )
    if "progress_conflict" in warning_codes:
        warning_codes.add("progress_conflict")
    return candidates, tuple(sorted(warning_codes)), tuple(sorted(observed_sidecars))


def _save_projection_preview(
    state: SyncState,
    source_digest: str,
    items: tuple[PreviewItem, ...],
) -> str:
    plan_material = [_plan_row(item) for item in sorted(items, key=lambda item: item.sidecar_key)]
    digest = hashlib.sha256(_canonical_json(plan_material).encode("utf-8")).hexdigest()
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        state.save_preview(digest, digest, created_at, [_item_dict(item) for item in items])
    except SyncStateError:
        raise ReadingHistoryProjectionError("projection_preview_unavailable") from None
    return digest


def preview_calibre_reading_projection(
    state: ApplicationState,
    import_id: str,
    catalog: tuple[CalibreBook, ...],
) -> CalibreReadingProjectionPreview:
    """Build and persist a review-only Calibre projection from applied local history."""
    if not isinstance(import_id, str) or not _DIGEST.fullmatch(import_id):
        raise ReadingHistoryProjectionError("invalid_import")
    try:
        with state.connect() as connection:
            batch = _read_batch(connection, import_id)
            source_digest = str(batch["source_digest"])
            snapshots = connection.execute(
                """
                SELECT event.identity_id, event.payload_json
                FROM reading_events AS event
                WHERE event.import_id=? AND event.source=? AND event.event_type='book_snapshot'
                ORDER BY event.source_event_id
                """,
                (import_id, _SOURCE),
            ).fetchall()
            sync_state = SyncState(state.database)
            calibre_by_uuid: dict[str, list[CalibreBook]] = {}
            for book in catalog:
                calibre_by_uuid.setdefault(book.uuid.casefold(), []).append(book)

            items: list[PreviewItem] = []
            issues: list[ReadingHistoryProjectionIssue] = []
            for snapshot in snapshots:
                koreader_identity_id = str(snapshot[0])
                book_data = _read_json_object(snapshot[1])
                book_id_value = book_data.get("koreader_book_id")
                title_value = book_data.get("title")
                if (
                    isinstance(book_id_value, bool)
                    or not isinstance(book_id_value, int)
                    or book_id_value < 1
                    or not isinstance(title_value, str)
                ):
                    raise ReadingHistoryProjectionError("stored_history_invalid")
                book_id = book_id_value
                title = title_value
                link = _calibre_link(connection, koreader_identity_id)
                if link is None:
                    issues.append(
                        ReadingHistoryProjectionIssue(book_id, title, "identity_not_mapped")
                    )
                    continue
                calibre_uuid = link[1]
                matching_books = calibre_by_uuid.get(calibre_uuid.casefold(), [])
                if not matching_books:
                    issues.append(
                        ReadingHistoryProjectionIssue(book_id, title, "calibre_book_missing")
                    )
                    continue
                if len(matching_books) != 1:
                    issues.append(
                        ReadingHistoryProjectionIssue(book_id, title, "calibre_book_ambiguous")
                    )
                    continue
                sidecar_ids_value = book_data.get("sidecar_ids", [])
                if not isinstance(sidecar_ids_value, list) or not all(
                    isinstance(value, str) and _DIGEST.fullmatch(value)
                    for value in sidecar_ids_value
                ):
                    raise ReadingHistoryProjectionError("stored_history_invalid")
                sidecar_ids = tuple(sidecar_ids_value)
                if not sidecar_ids:
                    issues.append(ReadingHistoryProjectionIssue(book_id, title, "sidecar_missing"))
                    continue
                candidates, source_warnings, observed_sidecars = _collect_candidates(
                    connection,
                    sidecar_ids=sidecar_ids,
                    koreader_identity_id=koreader_identity_id,
                )
                if not observed_sidecars:
                    issues.append(
                        ReadingHistoryProjectionIssue(book_id, title, "sidecar_snapshot_missing")
                    )
                    continue
                book = matching_books[0]
                try:
                    checkpoint = sync_state.load_checkpoint(book.uuid)
                except SyncStateError:
                    raise ReadingHistoryProjectionError("projection_state_unavailable") from None
                old_values, new_values, review_warnings, conflict = _prepare_values(
                    book,
                    candidates,
                    checkpoint,
                )
                warnings = tuple(sorted(set((*source_warnings, *review_warnings))))
                reason_code = conflict or (
                    "already_current" if not new_values else "confirmed_identity"
                )
                if conflict is not None:
                    old_values = {}
                    new_values = {}
                sidecar_key = hashlib.sha256(
                    "\x00".join(observed_sidecars).encode("ascii")
                ).hexdigest()
                items.append(
                    PreviewItem(
                        sidecar_key=sidecar_key,
                        relative_path="KOReader reading history",
                        source_digest=source_digest,
                        match_status="manual",
                        reason_code=reason_code,
                        book_id=book.book_id,
                        calibre_uuid=book.uuid,
                        title=book.title,
                        old_values=old_values,
                        new_values=new_values,
                        warnings=warnings,
                        authors=book.authors,
                    )
                )
            item_tuple = tuple(sorted(items, key=lambda item: item.sidecar_key))
        projection_id = _save_projection_preview(
            sync_state,
            source_digest,
            item_tuple,
        )
        return CalibreReadingProjectionPreview(
            projection_id=projection_id,
            import_id=import_id,
            source_digest=source_digest,
            items=item_tuple,
            issues=tuple(issues),
        )
    except ReadingHistoryProjectionError:
        raise
    except sqlite3.Error, OSError:
        raise ReadingHistoryProjectionError("projection_state_unavailable") from None
