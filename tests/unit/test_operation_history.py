from __future__ import annotations

import json
from pathlib import Path

import pytest

from koreader_ecosystem.operation_history import OperationHistory, OperationHistoryError


def test_operation_history_round_trips_only_sanitized_status(tmp_path: Path) -> None:
    history = OperationHistory(tmp_path / "operation-history.json")
    history.record(
        key="hardcover_library_refresh",
        status="success",
        updated_at="2026-10-24T12:30:00+00:00",
    )

    loaded = OperationHistory(history.path).get("hardcover_library_refresh")

    assert loaded is not None
    assert loaded.status == "success"
    assert loaded.updated_at == "2026-10-24T12:30:00+00:00"
    assert "book title" not in history.path.read_text(encoding="utf-8")


def test_sqlite_operation_history_migrates_legacy_json_and_keeps_new_state(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "operation-history.json"
    legacy_history = OperationHistory(legacy)
    legacy_history.record(
        key="hardcover_library_refresh",
        status="success",
        updated_at="2026-10-24T12:30:00+00:00",
    )
    original = legacy.read_bytes()
    database = tmp_path / "application-state.sqlite3"

    history = OperationHistory(database, legacy_path=legacy)

    assert history.get("hardcover_library_refresh") == legacy_history.get(
        "hardcover_library_refresh"
    )
    history.record(
        key="hardcover_library_refresh",
        status="failed",
        updated_at="2026-10-25T12:30:00+00:00",
        reason_code="remote_unavailable",
    )
    reopened = OperationHistory(database, legacy_path=legacy)
    record = reopened.get("hardcover_library_refresh")

    assert record is not None
    assert record.status == "failed"
    assert record.reason_code == "remote_unavailable"
    assert record.last_success_at == "2026-10-24T12:30:00+00:00"
    assert legacy.read_bytes() == original


def test_operation_history_rejects_arbitrary_keys_and_reasons(tmp_path: Path) -> None:
    history = OperationHistory(tmp_path / "operation-history.json")

    with pytest.raises(OperationHistoryError):
        history.record(
            key="book title contains private data",
            status="success",
            updated_at="2026-10-24T12:30:00+00:00",
        )
    with pytest.raises(OperationHistoryError):
        history.record(
            key="hardcover_library_refresh",
            status="failed",
            updated_at="2026-10-24T12:30:00+00:00",
            reason_code="private book title",
        )


def test_operation_history_marks_interrupted_local_backup_after_lock_recovery(
    tmp_path: Path,
) -> None:
    history = OperationHistory(tmp_path / "operation-history.json")
    history.record(
        key="local_backup",
        status="success",
        updated_at="2026-10-24T12:30:00+00:00",
    )
    history.record(
        key="local_backup",
        status="running",
        updated_at="2026-10-25T12:30:00+00:00",
    )

    recovered = history.recover_interrupted_backup(updated_at="2026-10-26T12:30:00+00:00")

    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.reason_code == "interrupted"
    assert recovered.last_success_at == "2026-10-24T12:30:00+00:00"


def test_operation_history_rejects_linked_path(tmp_path: Path) -> None:
    actual = tmp_path / "actual.json"
    actual.write_text("{}", encoding="utf-8")
    linked = tmp_path / "linked.json"
    try:
        linked.symlink_to(actual)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(OperationHistoryError):
        OperationHistory(linked)


def test_operation_history_rejects_invalid_stored_json(tmp_path: Path) -> None:
    path = tmp_path / "operation-history.json"
    path.write_text(json.dumps({"books": ["private"]}), encoding="utf-8")

    with pytest.raises(OperationHistoryError):
        OperationHistory(path).get("hardcover_library_refresh")
