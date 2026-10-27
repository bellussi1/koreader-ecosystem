from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from koreader_ecosystem.application_state import ApplicationState
from koreader_ecosystem.sync_persistence import (
    RemoteCacheStore,
    SyncOutboxStore,
    SyncPersistenceError,
)


def test_remote_cache_survives_reopening_application_state(tmp_path: Path) -> None:
    database = tmp_path / "application-state.sqlite3"
    first = RemoteCacheStore(ApplicationState(database))
    fetched_at = datetime(2026, 10, 25, 16, 0, tzinfo=UTC)
    first.put("library_page", "limit=20&offset=0", {"books": [{"id": 12}]}, fetched_at=fetched_at)

    reopened = RemoteCacheStore(ApplicationState(database))

    entry = reopened.get("library_page", "limit=20&offset=0")
    assert entry is not None
    assert entry.payload == {"books": [{"id": 12}]}
    assert entry.fetched_at == fetched_at


def test_remote_cache_validates_json_and_can_clear_provider(tmp_path: Path) -> None:
    state = ApplicationState(tmp_path / "application-state.sqlite3")
    cache = RemoteCacheStore(state)
    cache.put("current_reading", "me", {"book": "Example"})

    with pytest.raises(SyncPersistenceError):
        cache.put("current_reading", "bad", {"value": float("nan")})

    assert cache.clear_provider("hardcover") == 1
    assert cache.get("current_reading", "me") is None


def test_outbox_is_idempotent_and_rejects_conflicting_reuse(tmp_path: Path) -> None:
    store = SyncOutboxStore(ApplicationState(tmp_path / "application-state.sqlite3"))
    first = store.enqueue(
        provider="hardcover",
        operation="finish_read",
        idempotency_key="action-123",
        payload={"read_id": 7},
    )

    repeated = store.enqueue(
        provider="hardcover",
        operation="finish_read",
        idempotency_key="action-123",
        payload={"read_id": 7},
    )

    assert repeated == first
    with pytest.raises(SyncPersistenceError):
        store.enqueue(
            provider="hardcover",
            operation="finish_read",
            idempotency_key="action-123",
            payload={"read_id": 8},
        )


def test_outbox_persists_transitions_and_recovers_inflight_as_reconcile(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application-state.sqlite3"
    first = SyncOutboxStore(ApplicationState(database))
    queued = first.enqueue(
        provider="hardcover",
        operation="finish_read",
        idempotency_key="action-456",
        payload={"read_id": 9},
    )
    first.transition(queued.operation_id, "sending")

    reopened = SyncOutboxStore(ApplicationState(database))
    assert reopened.recover_interrupted() == 1

    recovered = reopened.list_pending()
    assert len(recovered) == 1
    assert recovered[0].status == "reconcile"
    assert recovered[0].reason_code == "interrupted_send"


def test_outbox_rejects_invalid_state_transition(tmp_path: Path) -> None:
    store = SyncOutboxStore(ApplicationState(tmp_path / "application-state.sqlite3"))
    queued = store.enqueue(
        provider="hardcover",
        operation="set_rating",
        idempotency_key="action-789",
        payload={"user_book_id": 2, "rating": 4.5},
    )

    with pytest.raises(SyncPersistenceError):
        store.transition(queued.operation_id, "complete")


def test_outbox_claims_reconciliation_atomically_before_retry(tmp_path: Path) -> None:
    store = SyncOutboxStore(ApplicationState(tmp_path / "application-state.sqlite3"))
    queued = store.enqueue(
        provider="hardcover",
        operation="update_rating",
        idempotency_key="rating-123",
        payload={"user_book_id": 17, "rating": 4.5},
    )
    store.transition(queued.operation_id, "sending")
    store.transition(queued.operation_id, "reconcile", reason_code="remote_unavailable")

    claimed = store.claim_for_retry(
        queued.operation_id,
        expected_status="reconcile",
        expected_attempts=1,
    )

    assert claimed.status == "sending"
    assert claimed.attempts == 2
    assert (
        store.claim_for_retry(
            queued.operation_id,
            expected_status="reconcile",
            expected_attempts=1,
        )
        is None
    )


def test_outbox_manual_review_is_audited_and_removed_from_unresolved_queue(
    tmp_path: Path,
) -> None:
    store = SyncOutboxStore(ApplicationState(tmp_path / "application-state.sqlite3"))
    queued = store.enqueue(
        provider="hardcover",
        operation="add_journal_entry",
        idempotency_key="note-1",
        payload={"book_id": 28, "entry": "Private journal note", "privacy_setting_id": 3},
    )
    store.transition(queued.operation_id, "sending")
    ambiguous = store.transition(queued.operation_id, "reconcile", reason_code="remote_unavailable")

    reviewed_at = datetime(2026, 10, 26, 16, 30, tzinfo=UTC)
    store.resolve_manual_review(
        ambiguous.operation_id,
        resolution="confirmed_remote",
        expected_status="reconcile",
        expected_attempts=1,
        reviewed_at=reviewed_at,
    )

    assert store.list_pending() == ()
    assert (
        store.claim_for_retry(
            ambiguous.operation_id,
            expected_status="reconcile",
            expected_attempts=ambiguous.attempts,
        )
        is None
    )
    with ApplicationState(store.state.database).connect() as connection:
        row = connection.execute(
            "SELECT resolution, reviewed_at FROM sync_outbox_reviews WHERE operation_id=?",
            (queued.operation_id,),
        ).fetchone()
    assert tuple(row) == ("confirmed_remote", reviewed_at.isoformat(timespec="microseconds"))


def test_outbox_manual_review_rejects_stale_or_sending_operations(tmp_path: Path) -> None:
    store = SyncOutboxStore(ApplicationState(tmp_path / "application-state.sqlite3"))
    queued = store.enqueue(
        provider="hardcover",
        operation="start_read",
        idempotency_key="start-read-inflight",
        payload={"user_book_id": 7, "edition_id": 9, "page": 1},
    )
    sending = store.transition(queued.operation_id, "sending")

    with pytest.raises(SyncPersistenceError, match="review state changed"):
        store.resolve_manual_review(
            queued.operation_id,
            resolution="closed_locally",
            expected_status="reconcile",
            expected_attempts=1,
        )

    with pytest.raises(SyncPersistenceError, match="not eligible"):
        store.resolve_manual_review(
            queued.operation_id,
            resolution="closed_locally",
            expected_status="sending",
            expected_attempts=sending.attempts,
        )
