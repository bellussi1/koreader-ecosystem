from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from koreader_ecosystem.application_state import ApplicationState
from koreader_ecosystem.calibre_catalog import CalibreBook
from koreader_ecosystem.koreader_calibre import assert_preview_plan_unchanged
from koreader_ecosystem.koreader_reading_history import (
    PageEvent,
    ReadingBook,
    ReadingHistoryPreview,
    ReadingSidecar,
)
from koreader_ecosystem.reading_history_projection import (
    ReadingHistoryProjectionError,
    preview_calibre_reading_projection,
)
from koreader_ecosystem.reading_history_state import KoreaderReadingHistoryStore

_OBSERVED_AT = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
_CALIBRE_UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def _import(
    tmp_path: Path,
    *,
    status: str = "applied",
    sidecar_status: str = "reading",
    sidecar_progress: float = 0.25,
) -> tuple[ApplicationState, str]:
    state = ApplicationState(tmp_path / "application-state.sqlite3")
    store = KoreaderReadingHistoryStore(state)
    book = ReadingBook(
        koreader_book_id=42,
        title="Synthetic Fixture Book",
        authors="Fixture Author",
        notes=1,
        last_opened_at=_OBSERVED_AT - timedelta(minutes=3),
        highlights=1,
        pages=100,
        series="Synthetic Series",
        language="eng",
        partial_md5_checksum="fixture-partial-md5",
        total_read_time_seconds=60,
        total_read_pages=20,
        sidecar_ids=("d" * 64,),
    )
    start_time = _OBSERVED_AT - timedelta(minutes=3)
    event_id = hashlib.sha256(f"42\x001\x00{int(start_time.timestamp())}".encode()).hexdigest()
    event = PageEvent(event_id, 42, 1, start_time, 60, 100, "a" * 64)
    sidecar = ReadingSidecar(
        sidecar_id="d" * 64,
        source_digest="e" * 64,
        koreader_book_id=42,
        progress=sidecar_progress,
        status=sidecar_status,
        rating=4.5,
        note="Fixture note",
        position="epubcfi(/6/4)",
        annotations=({"pos0": "start", "pos1": "end", "text": "A highlight"},),
        warnings=(),
    )
    preview = ReadingHistoryPreview("a" * 64, (book,), (event,), (), (), (sidecar,), _OBSERVED_AT)
    batch = store.stage_preview(preview)
    if status == "applied":
        store.confirm_import(batch.import_id, batch.source_digest)
    return state, batch.import_id


def _link_calibre(state: ApplicationState, *, book_id: int = 42) -> None:
    created_at = _OBSERVED_AT.isoformat()
    calibre_identity_id = hashlib.sha256(
        f"calibre\x00book_uuid\x00{_CALIBRE_UUID}".encode()
    ).hexdigest()
    with state.connect() as connection:
        koreader_identity_id = connection.execute(
            "SELECT identity_id FROM book_identities "
            "WHERE provider='koreader' AND external_type='book_id' AND external_id=?",
            (str(book_id),),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO book_identities(
                identity_id, provider, external_type, external_id, created_at, updated_at
            ) VALUES (?, 'calibre', 'book_uuid', ?, ?, ?)
            """,
            (calibre_identity_id, _CALIBRE_UUID, created_at, created_at),
        )
        connection.execute(
            """
            INSERT INTO identity_links(
                left_identity_id, right_identity_id, relation, confidence, created_at
            ) VALUES (?, ?, 'same_edition', 'confirmed', ?)
            """,
            (koreader_identity_id, calibre_identity_id, created_at),
        )


def _calibre_book(custom_values: dict[str, object] | None = None) -> CalibreBook:
    return CalibreBook(
        7,
        _CALIBRE_UUID,
        "Synthetic Fixture Book",
        ("Fixture Author",),
        {},
        custom_values or {},
    )


def test_builds_persisted_reviewable_projection_from_confirmed_identity(
    tmp_path: Path,
) -> None:
    state, import_id = _import(tmp_path)
    _link_calibre(state)

    preview = preview_calibre_reading_projection(
        state,
        import_id,
        (_calibre_book(),),
    )

    assert preview.import_id == import_id
    assert preview.source_digest == "a" * 64
    assert len(preview.items) == 1
    assert preview.items[0].match_status == "manual"
    new_values = preview.items[0].new_values
    assert new_values == {
        "koreader_progress": 0.25,
        "koreader_status": "reading",
        "koreader_position": "epubcfi(/6/4)",
        "koreader_rating": 4.5,
        "koreader_note": "Fixture note",
        "koreader_annotations": new_values["koreader_annotations"],
    }
    annotations = json.loads(str(new_values["koreader_annotations"]))
    assert annotations[0]["annotation"]["text"] == "A highlight"
    assert not preview.issues
    assert_preview_plan_unchanged(state.database, preview.projection_id)


def test_does_not_match_a_calibre_book_by_title_alone(tmp_path: Path) -> None:
    state, import_id = _import(tmp_path)

    preview = preview_calibre_reading_projection(
        state,
        import_id,
        (_calibre_book(),),
    )

    assert preview.items == ()
    assert [issue.reason_code for issue in preview.issues] == ["identity_not_mapped"]


def test_only_applied_imports_can_be_projected(tmp_path: Path) -> None:
    state, import_id = _import(tmp_path, status="preview")

    with pytest.raises(ReadingHistoryProjectionError) as error:
        preview_calibre_reading_projection(state, import_id, (_calibre_book(),))

    assert error.value.reason_code == "import_not_applied"


def test_progress_regression_is_reported_for_review_without_proposed_write(
    tmp_path: Path,
) -> None:
    state, import_id = _import(tmp_path)
    _link_calibre(state)

    preview = preview_calibre_reading_projection(
        state,
        import_id,
        (_calibre_book({"koreader_progress": 0.8}),),
    )

    assert len(preview.items) == 1
    assert preview.items[0].new_values == {}
    assert "progress_regression" in preview.items[0].warnings


def test_complete_status_with_incomplete_progress_is_held_for_review(
    tmp_path: Path,
) -> None:
    state, import_id = _import(
        tmp_path,
        sidecar_progress=0.127,
        sidecar_status="complete",
    )
    _link_calibre(state)

    preview = preview_calibre_reading_projection(
        state,
        import_id,
        (_calibre_book(),),
    )

    assert len(preview.items) == 1
    assert preview.items[0].new_values == {}
    assert preview.items[0].reason_code == "status_progress_conflict"
    assert "status_progress_conflict" in preview.items[0].warnings
