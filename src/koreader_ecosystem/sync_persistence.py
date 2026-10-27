from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from koreader_ecosystem.application_state import ApplicationState, ApplicationStateError


class SyncPersistenceError(RuntimeError):
    """Raised when cached or queued synchronization state is invalid or unavailable."""


OutboxStatus = Literal["pending", "sending", "reconcile", "complete", "failed"]
OutboxReviewResolution = Literal["confirmed_remote", "closed_locally"]
_PROVIDER = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_OPERATION = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_OUTBOX_STATUSES = frozenset({"pending", "sending", "reconcile", "complete", "failed"})
_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"sending", "failed"}),
    "sending": frozenset({"complete", "failed", "reconcile"}),
    "reconcile": frozenset({"pending", "complete", "failed"}),
    "failed": frozenset({"pending"}),
    "complete": frozenset(),
}
_MAX_CACHE_BYTES = 2 * 1024 * 1024
_MAX_OUTBOX_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class RemoteCacheEntry:
    payload: dict[str, object]
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class RemoteCacheResource:
    resource_id: str
    payload: dict[str, object]
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxOperation:
    operation_id: str
    provider: str
    operation: str
    identity_id: str | None
    idempotency_key: str
    payload: dict[str, object]
    status: OutboxStatus
    attempts: int
    created_at: datetime
    updated_at: datetime
    reason_code: str | None


def _timestamp(value: datetime | None) -> tuple[str, datetime]:
    parsed = value or datetime.now(UTC)
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SyncPersistenceError("Timestamp must include a timezone")
    normalized = parsed.astimezone(UTC)
    return normalized.isoformat(timespec="microseconds"), normalized


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise SyncPersistenceError("Stored timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise SyncPersistenceError("Stored timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SyncPersistenceError("Stored timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _json_payload(payload: object, *, maximum_bytes: int) -> str:
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise SyncPersistenceError("Stored payload must be a JSON object")
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except TypeError, ValueError, RecursionError:
        raise SyncPersistenceError("Payload is not valid JSON") from None
    if len(encoded.encode("utf-8")) > maximum_bytes:
        raise SyncPersistenceError("Payload exceeds the storage limit")
    return encoded


def _decode_payload(value: object, *, maximum_bytes: int) -> dict[str, object]:
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum_bytes:
        raise SyncPersistenceError("Stored payload is invalid")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError, RecursionError:
        raise SyncPersistenceError("Stored payload is invalid") from None
    if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
        raise SyncPersistenceError("Stored payload must be a JSON object")
    return decoded


def _validate_identifier(value: str, *, setting: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise SyncPersistenceError(f"{setting} is invalid")


class RemoteCacheStore:
    """Persist validated Hardcover response payloads in application SQLite state."""

    def __init__(self, state: ApplicationState) -> None:
        self.state = state

    def put(
        self,
        resource_type: str,
        resource_id: str,
        payload: dict[str, object],
        *,
        fetched_at: datetime | None = None,
    ) -> RemoteCacheEntry:
        if not isinstance(resource_type, str) or not _OPERATION.fullmatch(resource_type):
            raise SyncPersistenceError("Cache resource type is invalid")
        _validate_identifier(resource_id, setting="Cache resource ID", maximum=512)
        encoded = _json_payload(payload, maximum_bytes=_MAX_CACHE_BYTES)
        timestamp_text, timestamp = _timestamp(fetched_at)
        try:
            with self.state.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO remote_cache(
                        provider, resource_type, resource_id, payload_json, fetched_at
                    )
                    VALUES ('hardcover', ?, ?, ?, ?)
                    ON CONFLICT(provider, resource_type, resource_id) DO UPDATE SET
                        payload_json=excluded.payload_json, fetched_at=excluded.fetched_at
                    """,
                    (resource_type, resource_id, encoded, timestamp_text),
                )
        except ApplicationStateError:
            raise SyncPersistenceError("Remote cache is unavailable") from None
        except sqlite3.Error:
            raise SyncPersistenceError("Remote cache could not be written") from None
        return RemoteCacheEntry(payload=dict(payload), fetched_at=timestamp)

    def get(
        self,
        resource_type: str,
        resource_id: str,
        *,
        max_age: timedelta | None = None,
        now: datetime | None = None,
    ) -> RemoteCacheEntry | None:
        if not isinstance(resource_type, str) or not _OPERATION.fullmatch(resource_type):
            raise SyncPersistenceError("Cache resource type is invalid")
        _validate_identifier(resource_id, setting="Cache resource ID", maximum=512)
        if max_age is not None and (
            not isinstance(max_age, timedelta) or max_age.total_seconds() < 0
        ):
            raise SyncPersistenceError("Cache age limit is invalid")
        _, current_time = _timestamp(now)
        try:
            with self.state.connect() as connection:
                row = connection.execute(
                    "SELECT payload_json, fetched_at FROM remote_cache "
                    "WHERE provider='hardcover' AND resource_type=? AND resource_id=?",
                    (resource_type, resource_id),
                ).fetchone()
        except ApplicationStateError:
            raise SyncPersistenceError("Remote cache is unavailable") from None
        except sqlite3.Error:
            raise SyncPersistenceError("Remote cache could not be read") from None
        if row is None:
            return None
        payload = _decode_payload(row["payload_json"], maximum_bytes=_MAX_CACHE_BYTES)
        fetched_at = _parse_timestamp(row["fetched_at"])
        if max_age is not None and current_time - fetched_at > max_age:
            return None
        return RemoteCacheEntry(payload=payload, fetched_at=fetched_at)

    def list_resources(
        self, resource_type: str, *, limit: int = 1000
    ) -> tuple[RemoteCacheResource, ...]:
        if not isinstance(resource_type, str) or not _OPERATION.fullmatch(resource_type):
            raise SyncPersistenceError("Cache resource type is invalid")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10_000:
            raise SyncPersistenceError("Cache resource limit is invalid")
        try:
            with self.state.connect() as connection:
                rows = connection.execute(
                    "SELECT resource_id, payload_json, fetched_at FROM remote_cache "
                    "WHERE provider='hardcover' AND resource_type=? "
                    "ORDER BY fetched_at DESC, resource_id LIMIT ?",
                    (resource_type, limit),
                ).fetchall()
        except ApplicationStateError:
            raise SyncPersistenceError("Remote cache is unavailable") from None
        except sqlite3.Error:
            raise SyncPersistenceError("Remote cache could not be read") from None
        return tuple(
            RemoteCacheResource(
                resource_id=str(row["resource_id"]),
                payload=_decode_payload(row["payload_json"], maximum_bytes=_MAX_CACHE_BYTES),
                fetched_at=_parse_timestamp(row["fetched_at"]),
            )
            for row in rows
        )

    def clear_provider(self, provider: str) -> int:
        if provider != "hardcover":
            raise SyncPersistenceError("Cache provider is unsupported")
        try:
            with self.state.connect() as connection:
                result = connection.execute(
                    "DELETE FROM remote_cache WHERE provider=?", (provider,)
                )
                return result.rowcount
        except ApplicationStateError:
            raise SyncPersistenceError("Remote cache is unavailable") from None
        except sqlite3.Error:
            raise SyncPersistenceError("Remote cache could not be cleared") from None


class SyncOutboxStore:
    """Persist mutation intents and their delivery/reconciliation state."""

    def __init__(self, state: ApplicationState) -> None:
        self.state = state

    def enqueue(
        self,
        *,
        provider: str,
        operation: str,
        idempotency_key: str,
        payload: dict[str, object],
        identity_id: str | None = None,
        operation_id: str | None = None,
        created_at: datetime | None = None,
    ) -> OutboxOperation:
        if not isinstance(provider, str) or not _PROVIDER.fullmatch(provider):
            raise SyncPersistenceError("Outbox provider is invalid")
        if not isinstance(operation, str) or not _OPERATION.fullmatch(operation):
            raise SyncPersistenceError("Outbox operation is invalid")
        _validate_identifier(idempotency_key, setting="Idempotency key", maximum=512)
        if identity_id is not None:
            _validate_identifier(identity_id, setting="Identity ID", maximum=512)
        identifier = operation_id or str(uuid.uuid4())
        if not isinstance(identifier, str) or not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", identifier
        ):
            raise SyncPersistenceError("Outbox operation ID is invalid")
        encoded = _json_payload(payload, maximum_bytes=_MAX_OUTBOX_BYTES)
        created_text, created_value = _timestamp(created_at)
        try:
            with self.state.connect() as connection:
                existing = connection.execute(
                    "SELECT * FROM sync_outbox WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                if existing is not None:
                    if (
                        existing["provider"] != provider
                        or existing["operation"] != operation
                        or existing["identity_id"] != identity_id
                        or existing["payload_json"] != encoded
                    ):
                        raise SyncPersistenceError(
                            "Idempotency key conflicts with another operation"
                        )
                    return _outbox_record(existing)
                connection.execute(
                    """
                    INSERT INTO sync_outbox(
                        operation_id, provider, operation, identity_id, idempotency_key,
                        payload_json, status, attempts, created_at, updated_at, reason_code
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, NULL)
                    """,
                    (
                        identifier,
                        provider,
                        operation,
                        identity_id,
                        idempotency_key,
                        encoded,
                        created_text,
                        created_text,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM sync_outbox WHERE operation_id=?", (identifier,)
                ).fetchone()
                if row is None:
                    raise SyncPersistenceError("Outbox operation could not be read")
                return _outbox_record(row)
        except SyncPersistenceError:
            raise
        except ApplicationStateError:
            raise SyncPersistenceError("Sync outbox is unavailable") from None
        except sqlite3.Error:
            raise SyncPersistenceError("Sync operation could not be queued") from None

    def transition(
        self,
        operation_id: str,
        status: OutboxStatus,
        *,
        reason_code: str | None = None,
        updated_at: datetime | None = None,
    ) -> OutboxOperation:
        if status not in _OUTBOX_STATUSES:
            raise SyncPersistenceError("Outbox status is invalid")
        if reason_code is not None and (
            not isinstance(reason_code, str) or not _REASON_CODE.fullmatch(reason_code)
        ):
            raise SyncPersistenceError("Outbox reason code is invalid")
        if status in {"reconcile", "failed"} and reason_code is None:
            raise SyncPersistenceError("Outbox failure state requires a reason code")
        if status not in {"reconcile", "failed"} and reason_code is not None:
            raise SyncPersistenceError("Outbox reason is not valid for this state")
        timestamp_text, _ = _timestamp(updated_at)
        try:
            with self.state.connect() as connection:
                row = connection.execute(
                    "SELECT * FROM sync_outbox WHERE operation_id=?", (operation_id,)
                ).fetchone()
                if row is None:
                    raise SyncPersistenceError("Outbox operation was not found")
                current_status = row["status"]
                if status != current_status and status not in _TRANSITIONS.get(current_status, ()):
                    raise SyncPersistenceError("Outbox state transition is invalid")
                attempts = int(row["attempts"]) + (
                    1 if status == "sending" and status != current_status else 0
                )
                connection.execute(
                    "UPDATE sync_outbox SET status=?, attempts=?, updated_at=?, reason_code=? "
                    "WHERE operation_id=?",
                    (status, attempts, timestamp_text, reason_code, operation_id),
                )
                updated = connection.execute(
                    "SELECT * FROM sync_outbox WHERE operation_id=?", (operation_id,)
                ).fetchone()
                if updated is None:
                    raise SyncPersistenceError("Outbox operation could not be read")
                return _outbox_record(updated)
        except SyncPersistenceError:
            raise
        except ApplicationStateError:
            raise SyncPersistenceError("Sync outbox is unavailable") from None
        except sqlite3.Error:
            raise SyncPersistenceError("Outbox operation could not be updated") from None

    def claim_for_retry(
        self,
        operation_id: str,
        *,
        expected_status: Literal["pending", "reconcile"],
        expected_attempts: int,
        updated_at: datetime | None = None,
    ) -> OutboxOperation | None:
        if (
            expected_status not in {"pending", "reconcile"}
            or isinstance(expected_attempts, bool)
            or not isinstance(expected_attempts, int)
            or expected_attempts < 0
            or (expected_status == "pending" and expected_attempts != 0)
        ):
            raise SyncPersistenceError("Outbox retry claim is invalid")
        timestamp_text, _ = _timestamp(updated_at)
        try:
            with self.state.connect() as connection:
                result = connection.execute(
                    "UPDATE sync_outbox SET status='sending', attempts=attempts+1, "
                    "updated_at=?, reason_code=NULL WHERE operation_id=? AND status=? "
                    "AND attempts=? AND NOT EXISTS (SELECT 1 FROM sync_outbox_reviews "
                    "WHERE sync_outbox_reviews.operation_id=sync_outbox.operation_id)",
                    (timestamp_text, operation_id, expected_status, expected_attempts),
                )
                if result.rowcount != 1:
                    return None
                row = connection.execute(
                    "SELECT * FROM sync_outbox WHERE operation_id=?", (operation_id,)
                ).fetchone()
                if row is None:
                    raise SyncPersistenceError("Outbox operation could not be read")
                return _outbox_record(row)
        except SyncPersistenceError:
            raise
        except ApplicationStateError:
            raise SyncPersistenceError("Sync outbox is unavailable") from None
        except sqlite3.Error:
            raise SyncPersistenceError("Outbox operation could not be claimed") from None

    def resolve_manual_review(
        self,
        operation_id: str,
        *,
        resolution: OutboxReviewResolution,
        expected_status: OutboxStatus,
        expected_attempts: int,
        reviewed_at: datetime | None = None,
    ) -> None:
        _validate_identifier(operation_id, setting="Outbox operation ID", maximum=64)
        if resolution not in {"confirmed_remote", "closed_locally"}:
            raise SyncPersistenceError("Outbox review resolution is invalid")
        if (
            expected_status not in {"pending", "sending", "reconcile", "complete", "failed"}
            or isinstance(expected_attempts, bool)
            or not isinstance(expected_attempts, int)
            or expected_attempts < 0
        ):
            raise SyncPersistenceError("Outbox review state is invalid")
        timestamp_text, _ = _timestamp(reviewed_at)
        try:
            with self.state.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT status, attempts FROM sync_outbox WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if (
                    row is None
                    or row["status"] != expected_status
                    or int(row["attempts"]) != expected_attempts
                ):
                    raise SyncPersistenceError("Outbox review state changed; refresh and retry")
                if expected_status in {"sending", "complete"} or (
                    expected_status == "pending" and expected_attempts == 0
                ):
                    raise SyncPersistenceError("Outbox operation is not eligible for review")
                existing = connection.execute(
                    "SELECT 1 FROM sync_outbox_reviews WHERE operation_id=?", (operation_id,)
                ).fetchone()
                if existing is not None:
                    raise SyncPersistenceError("Outbox operation was already reviewed")
                connection.execute(
                    "INSERT INTO sync_outbox_reviews(operation_id, resolution, reviewed_at) "
                    "VALUES (?, ?, ?)",
                    (operation_id, resolution, timestamp_text),
                )
        except SyncPersistenceError:
            raise
        except ApplicationStateError:
            raise SyncPersistenceError("Sync outbox is unavailable") from None
        except sqlite3.Error:
            raise SyncPersistenceError("Outbox review could not be saved") from None

    def list_pending(self) -> tuple[OutboxOperation, ...]:
        try:
            with self.state.connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM sync_outbox WHERE status <> 'complete' "
                    "AND NOT EXISTS (SELECT 1 FROM sync_outbox_reviews "
                    "WHERE sync_outbox_reviews.operation_id=sync_outbox.operation_id) "
                    "ORDER BY created_at, operation_id"
                ).fetchall()
            return tuple(_outbox_record(row) for row in rows)
        except SyncPersistenceError:
            raise
        except ApplicationStateError:
            raise SyncPersistenceError("Sync outbox is unavailable") from None
        except sqlite3.Error:
            raise SyncPersistenceError("Sync outbox could not be read") from None

    def recover_interrupted(self, *, updated_at: datetime | None = None) -> int:
        timestamp_text, _ = _timestamp(updated_at)
        try:
            with self.state.connect() as connection:
                result = connection.execute(
                    "UPDATE sync_outbox SET status='reconcile', reason_code='interrupted_send', "
                    "updated_at=? WHERE status='sending'",
                    (timestamp_text,),
                )
                return result.rowcount
        except ApplicationStateError:
            raise SyncPersistenceError("Sync outbox is unavailable") from None
        except sqlite3.Error:
            raise SyncPersistenceError(
                "Interrupted sync operations could not be recovered"
            ) from None


def _outbox_record(row: sqlite3.Row) -> OutboxOperation:
    status = row["status"]
    if not isinstance(status, str) or status not in _OUTBOX_STATUSES:
        raise SyncPersistenceError("Stored outbox status is invalid")
    reason_code = row["reason_code"]
    if reason_code is not None and (
        not isinstance(reason_code, str) or not _REASON_CODE.fullmatch(reason_code)
    ):
        raise SyncPersistenceError("Stored outbox reason is invalid")
    return OutboxOperation(
        operation_id=str(row["operation_id"]),
        provider=str(row["provider"]),
        operation=str(row["operation"]),
        identity_id=str(row["identity_id"]) if row["identity_id"] is not None else None,
        idempotency_key=str(row["idempotency_key"]),
        payload=_decode_payload(row["payload_json"], maximum_bytes=_MAX_OUTBOX_BYTES),
        status=status,  # type: ignore[arg-type]
        attempts=int(row["attempts"]),
        created_at=_parse_timestamp(row["created_at"]),
        updated_at=_parse_timestamp(row["updated_at"]),
        reason_code=reason_code,
    )
