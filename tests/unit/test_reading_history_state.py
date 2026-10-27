from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from koreader_ecosystem.application_state import ApplicationState
from koreader_ecosystem.koreader_reading_history import (
    PageEvent,
    ReadingBook,
    ReadingHistoryPreview,
    ReadingSidecar,
)
from koreader_ecosystem.reading_history_state import (
    KoreaderReadingHistoryStore,
    ReadingHistoryStateError,
)

OBSERVED_AT = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _preview(
    *,
    source_digest: str = "a" * 64,
    event_id: str | None = None,
    duration_seconds: int = 60,
    annotation_text: str = "Fixture highlight",
    sidecar_book_id: int | None = 42,
) -> ReadingHistoryPreview:
    book = ReadingBook(
        koreader_book_id=42,
        title="Synthetic Fixture Book",
        authors="Fixture Author",
        notes=1,
        last_opened_at=OBSERVED_AT - timedelta(minutes=3),
        highlights=1,
        pages=100,
        series="Synthetic Series",
        language="eng",
        partial_md5_checksum="fixture-partial-md5",
        total_read_time_seconds=60,
        total_read_pages=1,
        sidecar_ids=("d" * 64,),
    )
    start_time = OBSERVED_AT - timedelta(minutes=3)
    stable_id = hashlib.sha256(f"42\x001\x00{int(start_time.timestamp())}".encode()).hexdigest()
    event = PageEvent(
        event_id=stable_id if event_id is None else event_id,
        koreader_book_id=42,
        page=1,
        start_time=start_time,
        duration_seconds=duration_seconds,
        total_pages=100,
        source_digest=source_digest,
    )
    books = (book,)
    if sidecar_book_id == 43:
        books += (
            ReadingBook(
                koreader_book_id=43,
                title="Other Fixture Book",
                authors="Other Fixture Author",
                notes=None,
                last_opened_at=None,
                highlights=None,
                pages=None,
                series=None,
                language=None,
                partial_md5_checksum=None,
                total_read_time_seconds=None,
                total_read_pages=None,
            ),
        )
    sidecar = ReadingSidecar(
        sidecar_id="d" * 64,
        source_digest="e" * 64,
        koreader_book_id=sidecar_book_id,
        progress=0.25,
        status="reading",
        rating=4.5,
        note="Fixture note",
        position="epubcfi(/6/4)",
        annotations=({"pos0": "anchor-start", "pos1": "anchor-end", "text": annotation_text},),
        warnings=(),
    )
    return ReadingHistoryPreview(
        source_digest=source_digest,
        books=books,
        events=(event,),
        sessions=(),
        warnings=(),
        sidecars=(sidecar,),
        observed_at=OBSERVED_AT,
    )


def _store(tmp_path: Path) -> KoreaderReadingHistoryStore:
    return KoreaderReadingHistoryStore(ApplicationState(tmp_path / "application-state.sqlite3"))


def test_preview_events_stay_inactive_until_digest_confirmed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    preview = _preview()

    batch = store.stage_preview(preview)

    assert batch.status == "preview"
    assert batch.source_digest == preview.source_digest
    assert store.get_events() == ()
    with pytest.raises(ReadingHistoryStateError) as stale:
        store.confirm_import(batch.import_id, "c" * 64)
    assert stale.value.reason_code == "source_digest_mismatch"
    assert store.get_import(batch.import_id).status == "preview"

    applied = store.confirm_import(batch.import_id, preview.source_digest)

    assert applied.status == "applied"
    assert applied.completed_at is not None
    events = store.get_events()
    assert len(events) == 1
    final_page = store.get_events_page(limit=1, offset=0)
    assert final_page.events == events
    assert final_page.has_more is False
    assert store.get_events_page(limit=1, offset=1).events == ()
    assert events[0].payload == {
        "duration_seconds": 60,
        "koreader_book_id": 42,
        "page": 1,
        "source_digest": preview.source_digest,
        "start_time": "2026-10-25T11:57:00Z",
        "total_pages": 100,
    }
    with sqlite3.connect(store.state.database) as connection:
        snapshot_row = connection.execute(
            "SELECT payload_json FROM reading_events WHERE event_type='sidecar_snapshot'"
        ).fetchone()
        book_row = connection.execute(
            "SELECT payload_json FROM reading_events WHERE event_type='book_snapshot'"
        ).fetchone()
        link = connection.execute("SELECT relation, confidence FROM identity_links").fetchone()
    assert json.loads(snapshot_row[0]) == {
        "koreader_book_id": 42,
        "note": "Fixture note",
        "position": "epubcfi(/6/4)",
        "progress": 0.25,
        "rating": 4.5,
        "sidecar_id": "d" * 64,
        "source_digest": "e" * 64,
        "status": "reading",
        "warnings": [],
    }
    assert json.loads(book_row[0])["partial_md5_checksum"] == "fixture-partial-md5"
    assert link == ("same_document", "exact")


def test_failed_preview_is_inactive_and_keeps_a_safe_reason_code(tmp_path: Path) -> None:
    store = _store(tmp_path)
    batch = store.stage_preview(_preview())

    failed = store.fail_import(batch.import_id, "invalid_sidecar")

    assert failed.status == "failed"
    assert failed.reason_code == "invalid_sidecar"
    assert failed.completed_at is not None
    assert store.get_events() == ()
    with pytest.raises(ReadingHistoryStateError):
        store.fail_import(batch.import_id, "invalid_sidecar")
    retried = store.stage_preview(_preview())
    assert retried.import_id == batch.import_id
    assert retried.status == "preview"
    assert store.confirm_import(retried.import_id, retried.source_digest).status == "applied"
    assert len(store.get_events()) == 1


def test_identical_source_digest_and_duplicate_event_identity_do_not_duplicate_rows(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = store.stage_preview(_preview())
    repeated = store.stage_preview(_preview())

    assert repeated.import_id == first.import_id
    assert repeated.status == "preview"
    store.confirm_import(first.import_id, first.source_digest)
    second_preview = _preview(source_digest="f" * 64)
    second = store.stage_preview(second_preview)

    with sqlite3.connect(store.state.database) as connection:
        event_count = connection.execute(
            "SELECT count(*) FROM reading_events WHERE event_type='page'"
        ).fetchone()[0]
        snapshot_count = connection.execute(
            "SELECT count(*) FROM reading_events WHERE event_type='sidecar_snapshot'"
        ).fetchone()[0]
        batch_count = connection.execute("SELECT count(*) FROM import_batches").fetchone()[0]

    assert second.import_id != first.import_id
    assert event_count == 1
    assert snapshot_count == 1
    assert batch_count == 2
    assert len(store.get_events()) == 1


def test_changed_payload_with_the_same_source_event_id_fails_transactionally(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = store.stage_preview(_preview())

    with pytest.raises(ReadingHistoryStateError) as error:
        store.stage_preview(_preview(source_digest="f" * 64, duration_seconds=61))

    assert error.value.reason_code == "event_identity_conflict"
    with sqlite3.connect(store.state.database) as connection:
        assert connection.execute("SELECT count(*) FROM import_batches").fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM reading_events WHERE event_type='page'"
        ).fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM annotation_versions").fetchone() == (1,)
    assert store.get_import(first.import_id).status == "preview"


def test_sidecar_identity_cannot_be_relinked_to_another_book(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.stage_preview(_preview())
    store.confirm_import(first.import_id, first.source_digest)

    with pytest.raises(ReadingHistoryStateError) as error:
        store.stage_preview(_preview(source_digest="f" * 64, sidecar_book_id=43))

    assert error.value.reason_code == "sidecar_identity_conflict"
    with sqlite3.connect(store.state.database) as connection:
        assert connection.execute("SELECT count(*) FROM import_batches").fetchone() == (1,)


def test_annotation_edits_create_immutable_versions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first_preview = _preview()
    first = store.stage_preview(first_preview)
    store.confirm_import(first.import_id, first_preview.source_digest)
    second_preview = _preview(source_digest="f" * 64, annotation_text="Edited highlight")
    second = store.stage_preview(second_preview)
    store.confirm_import(second.import_id, second_preview.source_digest)

    with sqlite3.connect(store.state.database) as connection:
        rows = connection.execute(
            """
            SELECT version, payload_json, import_id
            FROM annotation_versions
            WHERE source='koreader' AND source_annotation_id LIKE ?
            ORDER BY version
            """,
            ("d" * 64 + ":%",),
        ).fetchall()

    assert [row[0] for row in rows] == [1, 2]
    assert json.loads(rows[0][1])["text"] == "Fixture highlight"
    assert json.loads(rows[1][1])["text"] == "Edited highlight"
    assert rows[0][2] == first.import_id
    assert rows[1][2] == second.import_id


def test_store_rolls_back_batch_and_events_when_annotation_payload_is_invalid(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    invalid = _preview()
    sidecar = invalid.sidecars[0]
    invalid_sidecar = ReadingSidecar(
        sidecar_id=sidecar.sidecar_id,
        source_digest=sidecar.source_digest,
        koreader_book_id=sidecar.koreader_book_id,
        progress=sidecar.progress,
        status=sidecar.status,
        rating=sidecar.rating,
        note=sidecar.note,
        position=sidecar.position,
        annotations=({"text": object()},),
        warnings=(),
    )
    invalid = ReadingHistoryPreview(
        source_digest=invalid.source_digest,
        books=invalid.books,
        events=invalid.events,
        sessions=invalid.sessions,
        warnings=invalid.warnings,
        sidecars=(invalid_sidecar,),
        observed_at=invalid.observed_at,
    )

    with pytest.raises(ReadingHistoryStateError):
        store.stage_preview(invalid)

    with sqlite3.connect(store.state.database) as connection:
        for table in ("import_batches", "reading_events", "annotation_versions", "book_identities"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
