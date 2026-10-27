from __future__ import annotations

import json
import math
import sqlite3
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from koreader_ecosystem import __version__
from koreader_ecosystem.hardcover_models import (
    HardcoverLibraryEntry,
    HardcoverRead,
    parse_library_entry,
    parse_read,
)


class HardcoverError(RuntimeError):
    """Raised when a Hardcover operation cannot be completed safely."""

    def __init__(self, reason_code: str = "remote_unavailable") -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


Opener = Callable[..., Any]
_API_URL = "https://api.hardcover.app/v1/graphql"
_QUERY = "query { me { id } }"
_USER_AGENT = f"koreader-ecosystem/{__version__} (Hardcover authentication check)"
_MAX_RESPONSE_BYTES = 1_000_000
HARDCOVER_APP_DAILY_REQUEST_LIMIT = 1_000
HARDCOVER_APP_PER_MINUTE_LIMIT = 25
HARDCOVER_REQUEST_WINDOW_SECONDS = 24 * 60 * 60

_LIBRARY_QUERY = """
query UserBooks($userId: Int!, $limit: Int!, $offset: Int!) {
  user_books(
    where: { user_id: { _eq: $userId } }
    limit: $limit
    offset: $offset
    order_by: { updated_at: desc, id: desc }
  ) {
    id
    book_id
    status_id
    edition_id
    rating
    book {
      title
      cached_contributors
      cached_image(path: "url")
    }
    edition {
      id
      edition_format
      pages
    }
    user_book_reads(order_by: { id: asc }) {
      id
      started_at
      finished_at
      progress_pages
      edition_id
    }
  }
}
"""

_CURRENT_READING_QUERY = """
query CurrentReading($userId: Int!) {
  user_books(
    where: {
      user_id: { _eq: $userId }
      status_id: { _eq: 2 }
    }
    limit: 1
    order_by: { updated_at: desc, id: desc }
  ) {
    id
    book_id
    status_id
    edition_id
    rating
    book {
      title
      cached_contributors
      cached_image(path: "url")
    }
    edition {
      id
      edition_format
      pages
    }
    user_book_reads(order_by: { id: asc }) {
      id
      started_at
      finished_at
      progress_pages
      edition_id
    }
  }
}
"""

_USER_BOOK_BY_ID_QUERY = """
query UserBookById($id: Int!) {
  user_books(where: { id: { _eq: $id } }, limit: 1) {
    id
    book_id
    status_id
    edition_id
    rating
    book {
      title
      cached_contributors
      cached_image(path: "url")
    }
    edition {
      id
      edition_format
      pages
    }
    user_book_reads(order_by: { id: asc }) {
      id
      started_at
      finished_at
      progress_pages
      edition_id
    }
  }
}
"""

_READ_BY_ID_QUERY = """
query UserBookReadById($id: Int!) {
  user_book_reads(where: { id: { _eq: $id } }, limit: 1) {
    id
    started_at
    finished_at
    progress_pages
    edition_id
    user_book {
      id
      status_id
    }
  }
}
"""

_UPDATE_STATUS_MUTATION = """
mutation UpdateUserBookStatus($id: Int!, $statusId: Int!) {
  update_user_book(id: $id, object: { status_id: $statusId }) {
    error
    user_book { id status_id }
  }
}
"""

_UPDATE_RATING_MUTATION = """
mutation UpdateUserBookRating($id: Int!, $rating: numeric) {
  update_user_book(id: $id, object: { rating: $rating }) {
    error
    user_book { id rating }
  }
}
"""

_CREATE_READ_MUTATION = """
mutation InsertUserBookRead($id: Int!, $pages: Int, $editionId: Int, $startedAt: date) {
  insert_user_book_read(
    user_book_id: $id,
    user_book_read: {
      progress_pages: $pages,
      edition_id: $editionId,
      started_at: $startedAt
    }
  ) {
    error
    user_book_read { id }
  }
}
"""

_UPDATE_READ_MUTATION = """
mutation UpdateUserBookRead(
  $id: Int!,
  $pages: Int,
  $editionId: Int,
  $startedAt: date
) {
  update_user_book_read(
    id: $id,
    object: {
      progress_pages: $pages,
      edition_id: $editionId,
      started_at: $startedAt
    }
  ) {
    error
    user_book_read { id }
  }
}
"""

_FINISH_READ_MUTATION = """
mutation FinishUserBookRead($id: Int!, $finishedAt: date!) {
  update_user_book_read(
    id: $id,
    object: { finished_at: $finishedAt }
  ) {
    error
    user_book_read { id }
  }
}
"""

_CREATE_JOURNAL_MUTATION = """
mutation InsertReadingJournalEntry($object: ReadingJournalCreateType!) {
  insert_reading_journal(object: $object) {
    reading_journal { id book_id entry privacy_setting_id }
  }
}
"""

_ALLOWED_STATUS_IDS = frozenset({1, 2, 3, 5})
_ALLOWED_PRIVACY_IDS = frozenset({1, 2, 3})


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _open_without_redirects(request: urllib.request.Request, *, timeout: float) -> Any:
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(request, timeout=timeout)


class HardcoverRequestBudget:
    """Persistent, rolling request budget for the Windows app."""

    def __init__(
        self,
        database_path: Path,
        *,
        daily_limit: int = HARDCOVER_APP_DAILY_REQUEST_LIMIT,
        per_minute_limit: int = HARDCOVER_APP_PER_MINUTE_LIMIT,
    ) -> None:
        self.database_path = database_path
        self.daily_limit = daily_limit
        self.per_minute_limit = per_minute_limit

    def reserve(self) -> None:
        now = int(time.time())
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.database_path, timeout=5, isolation_level=None) as db:
                db.execute("PRAGMA busy_timeout = 5000")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS hardcover_requests (requested_at INTEGER NOT NULL)"
                )
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "DELETE FROM hardcover_requests WHERE requested_at <= ?",
                    (now - HARDCOVER_REQUEST_WINDOW_SECONDS,),
                )
                daily_count = db.execute(
                    "SELECT COUNT(*) FROM hardcover_requests WHERE requested_at > ?",
                    (now - HARDCOVER_REQUEST_WINDOW_SECONDS,),
                ).fetchone()[0]
                minute_count = db.execute(
                    "SELECT COUNT(*) FROM hardcover_requests WHERE requested_at > ?",
                    (now - 60,),
                ).fetchone()[0]
                if daily_count >= self.daily_limit:
                    db.commit()
                    raise HardcoverError("daily_request_budget_reached")
                if minute_count >= self.per_minute_limit:
                    db.commit()
                    raise HardcoverError("local_rate_limit")
                db.execute("INSERT INTO hardcover_requests (requested_at) VALUES (?)", (now,))
                db.commit()
        except HardcoverError:
            raise
        except OSError, sqlite3.Error:
            raise HardcoverError("request_budget_unavailable") from None

    def usage(self) -> dict[str, int]:
        now = int(time.time())
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.database_path, timeout=5) as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS hardcover_requests (requested_at INTEGER NOT NULL)"
                )
                count = db.execute(
                    "SELECT COUNT(*) FROM hardcover_requests WHERE requested_at > ?",
                    (now - HARDCOVER_REQUEST_WINDOW_SECONDS,),
                ).fetchone()[0]
        except OSError, sqlite3.Error:
            raise HardcoverError("request_budget_unavailable") from None
        return {
            "requests_used": count,
            "request_limit": self.daily_limit,
            "requests_remaining": max(0, self.daily_limit - count),
            "window_seconds": HARDCOVER_REQUEST_WINDOW_SECONDS,
        }


class HardcoverClient:
    """Small Hardcover GraphQL client with sanitized errors and responses."""

    def __init__(self, opener: Opener | None = None) -> None:
        self._opener = opener
        self._request_budget: HardcoverRequestBudget | None = None

    def set_request_budget(self, budget: HardcoverRequestBudget) -> None:
        self._request_budget = budget

    def request_usage(self) -> dict[str, int]:
        if self._request_budget is None:
            return {
                "requests_used": 0,
                "request_limit": HARDCOVER_APP_DAILY_REQUEST_LIMIT,
                "requests_remaining": HARDCOVER_APP_DAILY_REQUEST_LIMIT,
                "window_seconds": HARDCOVER_REQUEST_WINDOW_SECONDS,
            }
        return self._request_budget.usage()

    def verify_token(self, token: str) -> None:
        data = self._graphql(
            token,
            _QUERY,
            graphql_error_code="authentication_failed",
        )
        users = data.get("me")
        if (
            not isinstance(users, list)
            or not users
            or not isinstance(users[0], dict)
            or not isinstance(users[0].get("id"), int | str)
            or not str(users[0]["id"]).strip()
        ):
            raise HardcoverError("authentication_failed")

    def list_user_books(
        self, token: str, *, limit: int = 50, offset: int = 0
    ) -> tuple[HardcoverLibraryEntry, ...]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
            or isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
        ):
            raise HardcoverError("invalid_request")

        profile = self._graphql(
            token,
            _QUERY,
            graphql_error_code="authentication_failed",
        )
        users = profile.get("me")
        if (
            not isinstance(users, list)
            or not users
            or not isinstance(users[0], dict)
            or not isinstance(users[0].get("id"), int | str)
        ):
            raise HardcoverError("authentication_failed")
        user_id = users[0]["id"]
        if isinstance(user_id, bool) or not str(user_id).isdecimal():
            raise HardcoverError("authentication_failed")

        data = self._graphql(
            token,
            _LIBRARY_QUERY,
            {
                "userId": int(user_id),
                "limit": limit,
                "offset": offset,
            },
        )
        entries = data.get("user_books")
        if not isinstance(entries, list):
            raise HardcoverError("invalid_response")
        try:
            return tuple(parse_library_entry(entry) for entry in entries)
        except TypeError, ValueError:
            raise HardcoverError("invalid_response") from None

    def current_reading(self, token: str) -> HardcoverLibraryEntry | None:
        """Return the most recently updated book marked as currently reading."""
        profile = self._graphql(
            token,
            _QUERY,
            graphql_error_code="authentication_failed",
        )
        users = profile.get("me")
        if (
            not isinstance(users, list)
            or not users
            or not isinstance(users[0], dict)
            or not isinstance(users[0].get("id"), int | str)
        ):
            raise HardcoverError("authentication_failed")
        user_id = users[0]["id"]
        if isinstance(user_id, bool) or not str(user_id).isdecimal():
            raise HardcoverError("authentication_failed")

        data = self._graphql(
            token,
            _CURRENT_READING_QUERY,
            {"userId": int(user_id)},
        )
        entries = data.get("user_books")
        if not isinstance(entries, list) or len(entries) > 1:
            raise HardcoverError("invalid_response")
        if not entries:
            return None
        try:
            return parse_library_entry(entries[0])
        except TypeError, ValueError:
            raise HardcoverError("invalid_response") from None

    def start_read(
        self,
        token: str,
        *,
        user_book_id: int,
        edition_id: int,
        page: int,
        started_at: date,
    ) -> HardcoverRead:
        _validate_positive_integer(user_book_id)
        _validate_positive_integer(edition_id)
        _validate_page(page)
        _validate_date(started_at)

        entry = self._get_user_book_entry(token, user_book_id)
        active_read = next(
            (read for read in reversed(entry.reads) if read.finished_at is None), None
        )
        if entry.status_id != 2:
            entry = self.update_status(token, user_book_id=user_book_id, status_id=2)
        if active_read is not None:
            return active_read

        data = self._graphql(
            token,
            _CREATE_READ_MUTATION,
            {
                "id": user_book_id,
                "pages": page,
                "editionId": edition_id,
                "startedAt": started_at.isoformat(),
            },
        )
        mutation = data.get("insert_user_book_read")
        created = mutation.get("user_book_read") if isinstance(mutation, dict) else None
        if not isinstance(mutation, dict) or mutation.get("error") or not isinstance(created, dict):
            raise HardcoverError("mutation_failed")
        read_id = _required_integer(created.get("id"), minimum=1)
        read, actual_user_book_id, _ = self._get_read_context(token, read_id)
        if (
            actual_user_book_id != user_book_id
            or read.started_at != started_at
            or read.finished_at is not None
            or read.progress_pages != page
            or read.edition_id != edition_id
        ):
            raise HardcoverError("write_unconfirmed")
        return read

    def update_read(
        self,
        token: str,
        *,
        read_id: int,
        edition_id: int,
        page: int,
        started_at: date,
    ) -> HardcoverRead:
        _validate_positive_integer(read_id)
        _validate_positive_integer(edition_id)
        _validate_page(page)
        _validate_date(started_at)

        current, _, _ = self._get_read_context(token, read_id)
        if current.finished_at is not None:
            raise HardcoverError("read_finished")
        data = self._graphql(
            token,
            _UPDATE_READ_MUTATION,
            {
                "id": read_id,
                "pages": page,
                "editionId": edition_id,
                "startedAt": started_at.isoformat(),
            },
        )
        self._require_mutation_object(data, "update_user_book_read", "user_book_read")
        updated, _, _ = self._get_read_context(token, read_id)
        if (
            updated.started_at != started_at
            or updated.finished_at is not None
            or updated.progress_pages != page
            or updated.edition_id != edition_id
        ):
            raise HardcoverError("write_unconfirmed")
        return updated

    def finish_read(self, token: str, *, read_id: int, finished_at: date) -> HardcoverRead:
        _validate_positive_integer(read_id)
        _validate_date(finished_at)

        current, user_book_id, status_id = self._get_read_context(token, read_id)
        completed = current
        if current.finished_at is None:
            data = self._graphql(
                token,
                _FINISH_READ_MUTATION,
                {
                    "id": read_id,
                    "finishedAt": finished_at.isoformat(),
                },
            )
            self._require_mutation_object(data, "update_user_book_read", "user_book_read")
            completed, actual_user_book_id, status_id = self._get_read_context(token, read_id)
            if actual_user_book_id != user_book_id or completed.finished_at != finished_at:
                raise HardcoverError("write_unconfirmed")

        if status_id != 3:
            self.update_status(token, user_book_id=user_book_id, status_id=3)
        return completed

    def update_status(
        self, token: str, *, user_book_id: int, status_id: int
    ) -> HardcoverLibraryEntry:
        _validate_positive_integer(user_book_id)
        if isinstance(status_id, bool) or status_id not in _ALLOWED_STATUS_IDS:
            raise HardcoverError("invalid_request")
        data = self._graphql(
            token,
            _UPDATE_STATUS_MUTATION,
            {"id": user_book_id, "statusId": status_id},
        )
        mutation = self._require_mutation_object(data, "update_user_book", "user_book")
        if (
            _optional_integer(mutation.get("id"), minimum=1) != user_book_id
            or mutation.get("status_id") != status_id
        ):
            raise HardcoverError("mutation_failed")
        updated = self._get_user_book_entry(token, user_book_id)
        if updated.status_id != status_id:
            raise HardcoverError("write_unconfirmed")
        return updated

    def update_rating(
        self, token: str, *, user_book_id: int, rating: float | None
    ) -> HardcoverLibraryEntry:
        _validate_positive_integer(user_book_id)
        if rating is not None and (
            isinstance(rating, bool)
            or not isinstance(rating, int | float)
            or not math.isfinite(rating)
            or not 0 < rating <= 5
            or not math.isclose(rating * 2, round(rating * 2))
        ):
            raise HardcoverError("invalid_request")
        normalized_rating = float(rating) if rating is not None else None
        data = self._graphql(
            token,
            _UPDATE_RATING_MUTATION,
            {"id": user_book_id, "rating": normalized_rating},
        )
        mutation = self._require_mutation_object(data, "update_user_book", "user_book")
        if _optional_integer(mutation.get("id"), minimum=1) != user_book_id:
            raise HardcoverError("mutation_failed")
        updated = self._get_user_book_entry(token, user_book_id)
        if updated.rating != normalized_rating:
            raise HardcoverError("write_unconfirmed")
        return updated

    def add_journal_entry(
        self,
        token: str,
        *,
        book_id: int,
        entry: str,
        privacy_setting_id: int,
    ) -> int:
        _validate_positive_integer(book_id)
        if (
            not isinstance(entry, str)
            or not entry.strip()
            or len(entry) > 20_000
            or isinstance(privacy_setting_id, bool)
            or privacy_setting_id not in _ALLOWED_PRIVACY_IDS
        ):
            raise HardcoverError("invalid_request")
        journal = {
            "book_id": book_id,
            "event": "note",
            "entry": entry.strip(),
            "privacy_setting_id": privacy_setting_id,
            "tags": [],
        }
        data = self._graphql(token, _CREATE_JOURNAL_MUTATION, {"object": journal})
        mutation = data.get("insert_reading_journal")
        created = mutation.get("reading_journal") if isinstance(mutation, dict) else None
        if (
            not isinstance(mutation, dict)
            or mutation.get("error")
            or not isinstance(created, dict)
            or _optional_integer(created.get("id"), minimum=1) is None
            or _optional_integer(created.get("book_id"), minimum=1) != book_id
            or created.get("entry") != journal["entry"]
            or created.get("privacy_setting_id") != privacy_setting_id
        ):
            raise HardcoverError("write_unconfirmed")
        return int(created["id"])

    def _get_user_book_entry(self, token: str, user_book_id: int) -> HardcoverLibraryEntry:
        data = self._graphql(token, _USER_BOOK_BY_ID_QUERY, {"id": user_book_id})
        entries = data.get("user_books")
        if not isinstance(entries, list) or len(entries) != 1:
            raise HardcoverError("not_found")
        try:
            return parse_library_entry(entries[0])
        except TypeError, ValueError:
            raise HardcoverError("invalid_response") from None

    def _get_read_context(self, token: str, read_id: int) -> tuple[HardcoverRead, int, int]:
        data = self._graphql(token, _READ_BY_ID_QUERY, {"id": read_id})
        reads = data.get("user_book_reads")
        if not isinstance(reads, list) or len(reads) != 1 or not isinstance(reads[0], dict):
            raise HardcoverError("not_found")
        parent = reads[0].get("user_book")
        if not isinstance(parent, dict):
            raise HardcoverError("invalid_response")
        try:
            read = parse_read(reads[0])
            user_book_id = _required_integer(parent.get("id"), minimum=1)
            status_id = _required_integer(parent.get("status_id"), minimum=1)
        except TypeError, ValueError:
            raise HardcoverError("invalid_response") from None
        return read, user_book_id, status_id

    @staticmethod
    def _require_mutation_object(
        data: dict[str, Any], operation_key: str, object_key: str
    ) -> dict[str, Any]:
        operation = data.get(operation_key)
        result = operation.get(object_key) if isinstance(operation, dict) else None
        if (
            not isinstance(operation, dict)
            or operation.get("error")
            or not isinstance(result, dict)
        ):
            raise HardcoverError("mutation_failed")
        return result

    def _graphql(
        self,
        token: str,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        graphql_error_code: str = "remote_unavailable",
    ) -> dict[str, Any]:
        if (
            not isinstance(token, str)
            or not token.isascii()
            or not token.strip()
            or any(character.isspace() for character in token)
        ):
            raise HardcoverError("authentication_failed")

        body: dict[str, Any] = {"query": query}
        if variables is not None:
            body["variables"] = variables
        request = urllib.request.Request(
            _API_URL,
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
            },
            method="POST",
        )

        if self._request_budget is not None:
            self._request_budget.reserve()

        try:
            opener = self._opener or _open_without_redirects
            with opener(request, timeout=15) as response:
                encoded = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(encoded) > _MAX_RESPONSE_BYTES:
                raise HardcoverError("invalid_response")
            payload = json.loads(encoded.decode("utf-8"))
        except HardcoverError:
            raise
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                raise HardcoverError("authentication_failed") from None
            if error.code == 429:
                raise HardcoverError("rate_limited") from None
            raise HardcoverError("remote_unavailable") from None
        except urllib.error.URLError, TimeoutError, OSError:
            raise HardcoverError("remote_unavailable") from None
        except UnicodeError, json.JSONDecodeError:
            raise HardcoverError("invalid_response") from None
        except Exception:
            raise HardcoverError("remote_unavailable") from None

        if not isinstance(payload, dict):
            raise HardcoverError("invalid_response")
        if payload.get("errors"):
            raise HardcoverError(graphql_error_code)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise HardcoverError("invalid_response")
        return data


def _validate_positive_integer(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise HardcoverError("invalid_request")


def _validate_page(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HardcoverError("invalid_request")


def _validate_date(value: object) -> None:
    if type(value) is not date:
        raise HardcoverError("invalid_request")


def _optional_integer(value: Any, *, minimum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        parsed = int(value)
    else:
        return None
    return parsed if parsed >= minimum else None


def _required_integer(value: Any, *, minimum: int) -> int:
    parsed = _optional_integer(value, minimum=minimum)
    if parsed is None:
        raise ValueError("required integer is malformed")
    return parsed
