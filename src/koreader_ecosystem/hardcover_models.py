from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class HardcoverRead:
    id: int
    started_at: date | None
    finished_at: date | None
    progress_pages: int
    edition_id: int | None


@dataclass(frozen=True, slots=True)
class HardcoverLibraryEntry:
    user_book_id: int
    book_id: int
    title: str
    authors: tuple[str, ...]
    status_id: int
    rating: float | None
    edition_id: int | None
    edition_pages: int | None
    reads: tuple[HardcoverRead, ...]
    cover_url: str | None = None
    edition_format: str | None = None


def parse_library_entry(value: Any) -> HardcoverLibraryEntry:
    if not isinstance(value, dict):
        raise ValueError("user book must be an object")

    book = value.get("book")
    edition = value.get("edition")
    if not isinstance(book, dict) or not isinstance(edition, dict | type(None)):
        raise ValueError("book or edition is malformed")

    title = book.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("book title is missing")

    raw_reads = value.get("user_book_reads")
    if raw_reads is None:
        raw_reads = []
    if not isinstance(raw_reads, list):
        raise ValueError("reading records must be a list")

    rating_value = value.get("rating")
    if rating_value is None:
        rating = None
    elif isinstance(rating_value, int | float) and not isinstance(rating_value, bool):
        rating = float(rating_value)
        if not 0 <= rating <= 5:
            raise ValueError("rating is outside the supported range")
    else:
        raise ValueError("rating is malformed")

    edition_id = _optional_integer(value.get("edition_id"), minimum=1)
    edition_pages = _optional_integer(
        edition.get("pages") if isinstance(edition, dict) else None,
        minimum=1,
    )
    edition_format = _optional_edition_format(
        edition.get("edition_format") if isinstance(edition, dict) else None
    )

    if edition_id is None and isinstance(edition, dict):
        edition_id = _optional_integer(edition.get("id"), minimum=1)

    reads = tuple(parse_read(record) for record in raw_reads)
    authors = _parse_authors(book.get("cached_contributors"))

    return HardcoverLibraryEntry(
        user_book_id=_required_integer(value.get("id"), minimum=1),
        book_id=_required_integer(value.get("book_id"), minimum=1),
        title=title.strip(),
        authors=authors,
        status_id=_required_integer(value.get("status_id"), minimum=1),
        rating=rating,
        edition_id=edition_id,
        edition_pages=edition_pages,
        reads=reads,
        cover_url=_optional_cover_url(book.get("cached_image")),
        edition_format=edition_format,
    )


def parse_read(value: Any) -> HardcoverRead:
    if not isinstance(value, dict):
        raise ValueError("reading record must be an object")
    return HardcoverRead(
        id=_required_integer(value.get("id"), minimum=1),
        started_at=_parse_date(value.get("started_at")),
        finished_at=_parse_date(value.get("finished_at")),
        progress_pages=_required_integer(value.get("progress_pages") or 0, minimum=0),
        edition_id=_optional_integer(value.get("edition_id"), minimum=1),
    )


def serialize_read(value: HardcoverRead) -> dict[str, object]:
    return {
        "id": value.id,
        "started_at": value.started_at.isoformat() if value.started_at is not None else None,
        "finished_at": value.finished_at.isoformat() if value.finished_at is not None else None,
        "progress_pages": value.progress_pages,
        "edition_id": value.edition_id,
    }


def deserialize_read(value: Any) -> HardcoverRead:
    if not isinstance(value, dict):
        raise ValueError("Cached reading record is malformed")
    return HardcoverRead(
        id=_required_integer(value.get("id"), minimum=1),
        started_at=_parse_date(value.get("started_at")),
        finished_at=_parse_date(value.get("finished_at")),
        progress_pages=_required_integer(value.get("progress_pages"), minimum=0),
        edition_id=_optional_integer(value.get("edition_id"), minimum=1),
    )


def serialize_library_entry(value: HardcoverLibraryEntry) -> dict[str, object]:
    return {
        "user_book_id": value.user_book_id,
        "book_id": value.book_id,
        "title": value.title,
        "authors": list(value.authors),
        "status_id": value.status_id,
        "rating": value.rating,
        "edition_id": value.edition_id,
        "edition_pages": value.edition_pages,
        "reads": [serialize_read(read) for read in value.reads],
        "cover_url": value.cover_url,
        "edition_format": value.edition_format,
    }


def deserialize_library_entry(value: Any) -> HardcoverLibraryEntry:
    if not isinstance(value, dict):
        raise ValueError("Cached Hardcover book is malformed")
    title = value.get("title")
    authors = value.get("authors")
    reads = value.get("reads")
    rating_value = value.get("rating")
    if (
        not isinstance(title, str)
        or not title.strip()
        or not isinstance(authors, list)
        or not all(isinstance(author, str) for author in authors)
        or not isinstance(reads, list)
    ):
        raise ValueError("Cached Hardcover book is malformed")
    if rating_value is None:
        rating = None
    elif isinstance(rating_value, int | float) and not isinstance(rating_value, bool):
        rating = float(rating_value)
        if not 0 <= rating <= 5:
            raise ValueError("Cached Hardcover rating is invalid")
    else:
        raise ValueError("Cached Hardcover rating is invalid")
    edition_format = value.get("edition_format")
    if edition_format is not None and not isinstance(edition_format, str):
        raise ValueError("Cached Hardcover edition format is invalid")
    cover_url = _optional_cover_url(value.get("cover_url"))
    return HardcoverLibraryEntry(
        user_book_id=_required_integer(value.get("user_book_id"), minimum=1),
        book_id=_required_integer(value.get("book_id"), minimum=1),
        title=title.strip(),
        authors=tuple(author.strip() for author in authors if author.strip()),
        status_id=_required_integer(value.get("status_id"), minimum=1),
        rating=rating,
        edition_id=_optional_integer(value.get("edition_id"), minimum=1),
        edition_pages=_optional_integer(value.get("edition_pages"), minimum=1),
        reads=tuple(deserialize_read(read) for read in reads),
        cover_url=cover_url,
        edition_format=_optional_edition_format(edition_format),
    )


def _required_integer(value: Any, *, minimum: int) -> int:
    parsed = _optional_integer(value, minimum=minimum)
    if parsed is None:
        raise ValueError("required integer is missing")
    return parsed


def _optional_integer(value: Any, *, minimum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer identifier")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        parsed = int(value)
    else:
        raise ValueError("integer value is malformed")
    if parsed < minimum:
        raise ValueError("integer value is outside the supported range")
    return parsed


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("date is malformed")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("date is malformed") from None


def _parse_authors(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("book contributors are malformed")

    authors: list[str] = []
    for contributor in value:
        name: Any
        if isinstance(contributor, str):
            name = contributor
        elif isinstance(contributor, dict):
            name = contributor.get("name")
            if not isinstance(name, str):
                author = contributor.get("author")
                name = author.get("name") if isinstance(author, dict) else author
        else:
            raise ValueError("book contributor is malformed")
        if not isinstance(name, str):
            raise ValueError("book contributor name is malformed")
        normalized = name.strip()
        if normalized and normalized not in authors:
            authors.append(normalized)
    return tuple(authors)


def _optional_cover_url(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("url")
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url or len(url) > 2048:
        return None
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or hostname is None
        or not (hostname == "hardcover.app" or hostname.endswith(".hardcover.app"))
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    return url


def _optional_edition_format(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("edition format is malformed")
    normalized = value.strip()
    if len(normalized) > 80:
        raise ValueError("edition format is too long")
    return normalized or None
