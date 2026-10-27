from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from koreader_ecosystem.sync_state import SyncState, SyncStateError

SIDE_CAR_KEY = "a" * 64
PREVIEW_ID = "b" * 64
CALIBRE_UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def _item() -> dict[str, object]:
    return {
        "sidecar_key": SIDE_CAR_KEY,
        "relative_path": "Books/Example.sdr/metadata.epub.lua",
        "source_digest": "c" * 64,
        "match_status": "matched",
        "reason_code": "exact_calibre_uuid",
        "book_id": 7,
        "calibre_uuid": CALIBRE_UUID,
        "title": "Livro de teste",
        "old_values": {"koreader_progress": None},
        "new_values": {"koreader_progress": 0.5},
        "warnings": [],
        "sidecar_data": {"percent_finished": 0.5},
        "applied_values": {},
        "applied_at": None,
    }


def test_creates_versioned_private_schema_and_persists_preview_idempotently(
    tmp_path: Path,
) -> None:
    database = tmp_path / "private" / "sync.sqlite3"
    state = SyncState(database)
    item = _item()

    state.save_preview(PREVIEW_ID, PREVIEW_ID, "2026-10-23T12:00:00+00:00", [item])
    state.save_preview(PREVIEW_ID, PREVIEW_ID, "2026-10-23T12:00:01+00:00", [item])

    saved = state.load_preview(PREVIEW_ID)
    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        row_count = connection.execute("SELECT count(*) FROM preview_items").fetchone()[0]

    assert database.is_file()
    assert version == 6
    assert row_count == 1
    assert saved["created_at"] == "2026-10-23T12:00:00+00:00"
    assert saved["items"] == [item]


def test_persists_manual_mapping_and_verified_per_field_checkpoints(tmp_path: Path) -> None:
    state = SyncState(tmp_path / "sync.sqlite3")
    state.save_preview(PREVIEW_ID, PREVIEW_ID, "2026-10-23T12:00:00+00:00", [_item()])

    state.save_manual_mapping(SIDE_CAR_KEY, 7)
    state.record_applied_field(
        PREVIEW_ID,
        SIDE_CAR_KEY,
        CALIBRE_UUID,
        7,
        "c" * 64,
        "koreader_progress",
        0.5,
        "2026-10-23T12:05:00+00:00",
    )

    assert state.manual_mappings() == {SIDE_CAR_KEY: 7}
    checkpoint = state.load_checkpoint(CALIBRE_UUID)
    assert checkpoint == {
        "book_id": 7,
        "sidecar_key": SIDE_CAR_KEY,
        "source_digest": "c" * 64,
        "values": {"koreader_progress": 0.5},
        "applied_at": "2026-10-23T12:05:00+00:00",
    }
    saved = state.load_preview(PREVIEW_ID)
    assert saved["items"][0]["applied_values"] == {"koreader_progress": 0.5}


def test_rejects_a_preview_id_reused_for_a_different_plan(tmp_path: Path) -> None:
    state = SyncState(tmp_path / "sync.sqlite3")
    state.save_preview(PREVIEW_ID, PREVIEW_ID, "2026-10-23T12:00:00+00:00", [_item()])

    with pytest.raises(SyncStateError):
        state.save_preview(PREVIEW_ID, "d" * 64, "2026-10-23T12:00:00+00:00", [_item()])


def test_rejects_an_unknown_database_schema_version(tmp_path: Path) -> None:
    database = tmp_path / "sync.sqlite3"
    SyncState(database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(SyncStateError):
        SyncState(database)


def test_rejects_invalid_manual_mapping_keys_and_ids(tmp_path: Path) -> None:
    state = SyncState(tmp_path / "sync.sqlite3")

    with pytest.raises(SyncStateError):
        state.save_manual_mapping("not-a-digest", 7)
    with pytest.raises(SyncStateError):
        state.save_manual_mapping(SIDE_CAR_KEY, 0)
