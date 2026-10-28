from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Literal

from koreader_ecosystem.application_state import ApplicationState


class LocalLibraryError(ValueError):
    """Invalid or unavailable local reading record."""


@dataclass(frozen=True, slots=True)
class LocalBook:
    book_id: str
    title: str
    author: str
    status: str
    progress: float | None
    rating: float | None
    started_on: str | None
    finished_on: str | None
    notes: str


def _date(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LocalLibraryError("invalid_date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise LocalLibraryError("invalid_date") from None
    if parsed.isoformat() != value:
        raise LocalLibraryError("invalid_date")
    return value


def _number(value: float | None, maximum: int) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LocalLibraryError("invalid_number")
    if not math.isfinite(value) or not 0 <= value <= maximum:
        raise LocalLibraryError("invalid_number")
    return float(value)


class LocalLibrary:
    """Local reading records; no network calls or modifications to external libraries."""

    def __init__(self, state: ApplicationState) -> None:
        self.state = state

    def add_book(self, *, title: str, author: str) -> LocalBook:
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
            raise LocalLibraryError("invalid_title")
        if not isinstance(author, str) or len(author.strip()) > 200:
            raise LocalLibraryError("invalid_author")
        book_id = uuid.uuid4().hex
        with self.state.connect() as connection:
            connection.execute(
                "INSERT INTO local_books(book_id,title,author,status,created_at,updated_at) "
                "VALUES(?,?,?,'to_read',strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                "strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                (book_id, title.strip(), author.strip()),
            )
        return self.get_book(book_id)

    def get_book(self, book_id: str) -> LocalBook:
        with self.state.connect() as connection:
            row = connection.execute(
                "SELECT book_id,title,author,status,progress,rating,started_on,finished_on,notes "
                "FROM local_books WHERE book_id=?",
                (book_id,),
            ).fetchone()
        if row is None:
            raise LocalLibraryError("book_not_found")
        return LocalBook(**dict(row))

    def list_books(self, *, limit: int = 100, offset: int = 0) -> tuple[LocalBook, ...]:
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 100 or offset < 0:
            raise LocalLibraryError("invalid_page")
        with self.state.connect() as connection:
            rows = connection.execute(
                "SELECT book_id,title,author,status,progress,rating,started_on,"
                "finished_on,'' AS notes "
                "FROM local_books ORDER BY created_at DESC,book_id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return tuple(LocalBook(**dict(row)) for row in rows)

    def save_reading(
        self,
        book_id: str,
        *,
        status: Literal["to_read", "reading", "finished", "stopped"],
        progress: float | None,
        rating: float | None,
        started_on: str | None,
        finished_on: str | None,
        notes: str,
    ) -> LocalBook:
        if status not in {"to_read", "reading", "finished", "stopped"}:
            raise LocalLibraryError("invalid_status")
        progress = _number(progress, 100)
        rating = _number(rating, 5)
        started_on = _date(started_on)
        finished_on = _date(finished_on)
        if started_on and finished_on and finished_on < started_on:
            raise LocalLibraryError("invalid_dates")
        if not isinstance(notes, str) or len(notes) > 100_000 or len(json.dumps(notes)) > 500_000:
            raise LocalLibraryError("invalid_notes")
        with self.state.connect() as connection:
            cursor = connection.execute(
                "UPDATE local_books SET status=?,progress=?,rating=?,started_on=?,finished_on=?,"
                "notes=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE book_id=?",
                (status, progress, rating, started_on, finished_on, notes, book_id),
            )
            if cursor.rowcount != 1:
                raise LocalLibraryError("book_not_found")
        return self.get_book(book_id)
