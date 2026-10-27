from __future__ import annotations

import hashlib
import math
import re
import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Literal

from koreader_ecosystem.lua_sidecar import LuaSidecarError, parse_sidecar
from koreader_ecosystem.manifest import ManifestEntry, ManifestError, build_manifest


class ReadingHistoryError(ValueError):
    """Raised when a staged KOReader history snapshot is unsafe or unsupported."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code if reason_code in _REASON_CODES else "invalid_data"
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class ReadingBook:
    koreader_book_id: int
    title: str | None
    authors: str | None
    notes: int | None
    last_opened_at: datetime | None
    highlights: int | None
    pages: int | None
    series: str | None
    language: str | None
    partial_md5_checksum: str | None
    total_read_time_seconds: int | None
    total_read_pages: int | None
    sidecar_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PageEvent:
    event_id: str
    koreader_book_id: int
    page: int
    start_time: datetime
    duration_seconds: int
    total_pages: int
    source_digest: str


@dataclass(frozen=True, slots=True)
class EstimatedSession:
    session_id: str
    koreader_book_id: int
    started_at: datetime
    ended_at: datetime
    page_event_count: int
    duration_seconds: int
    first_page: int
    last_page: int
    estimated: Literal[True] = True


@dataclass(frozen=True, slots=True)
class ReadingSidecar:
    sidecar_id: str
    source_digest: str
    koreader_book_id: int | None
    progress: float | None
    status: str | None
    rating: float | None
    note: str | None
    position: str | None
    annotations: tuple[dict[str, object], ...]
    warnings: tuple[str, ...]
    title: str | None = None
    authors: str | None = None
    title_source: Literal["metadata", "folder"] | None = None


@dataclass(frozen=True, slots=True)
class ReadingHistoryPreview:
    source_digest: str
    books: tuple[ReadingBook, ...]
    events: tuple[PageEvent, ...]
    sessions: tuple[EstimatedSession, ...]
    warnings: tuple[str, ...]
    sidecars: tuple[ReadingSidecar, ...]
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class _StageSources:
    database: Path | None
    sidecars: tuple[Path, ...]
    entries_by_path: dict[str, ManifestEntry]


_SUPPORTED_USER_VERSION = 20221111
_INACTIVITY_GAP = timedelta(minutes=30)
_MAX_SIDECAR_BYTES = 16_777_216
_SIDECAR_NAME = re.compile(r"metadata\..+\.lua", re.IGNORECASE)
_VALID_STATUSES = frozenset({"new", "reading", "complete", "abandoned"})
_REASON_CODES = frozenset(
    {
        "stage_unavailable",
        "invalid_path",
        "unexpected_file",
        "multiple_databases",
        "database_missing",
        "wal_incomplete",
        "database_integrity",
        "schema_unsupported",
        "schema_invalid",
        "invalid_data",
        "invalid_sidecar",
        "source_changed",
    }
)
_BOOK_COLUMNS = frozenset(
    {
        "id",
        "title",
        "authors",
        "notes",
        "last_open",
        "highlights",
        "pages",
        "series",
        "language",
        "md5",
        "total_read_time",
        "total_read_pages",
    }
)
_PAGE_COLUMNS = frozenset({"id_book", "page", "start_time", "duration", "total_pages"})


def _valid_observed_at(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ReadingHistoryError("invalid_data")
    return value.astimezone(UTC)


def _relative_path(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ReadingHistoryError("invalid_path") from None
    if any(part in {"", ".", ".."} or ":" in part or "\x00" in part for part in relative.parts):
        raise ReadingHistoryError("invalid_path")
    normalized = PurePosixPath(relative.as_posix())
    if normalized.is_absolute() or "\\" in normalized.as_posix():
        raise ReadingHistoryError("invalid_path")
    return normalized.as_posix()


def _is_sidecar_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    return _SIDECAR_NAME.fullmatch(path.name) is not None and any(
        part.casefold().endswith(".sdr") for part in path.parts[:-1]
    )


def _scan_stage(stage_root: Path) -> tuple[Path, tuple[ManifestEntry, ...], _StageSources]:
    root = Path(stage_root).expanduser()
    if not root.is_dir() or root.is_symlink() or root.is_junction():
        raise ReadingHistoryError("stage_unavailable")
    root = root.resolve(strict=True)

    try:
        paths = tuple(root.rglob("*"))
        if any(path.is_symlink() or path.is_junction() for path in paths):
            raise ReadingHistoryError("invalid_path")
        manifest = build_manifest(root)
    except ReadingHistoryError:
        raise
    except ManifestError, OSError, RuntimeError:
        raise ReadingHistoryError("invalid_path") from None

    file_entries = tuple(entry for entry in manifest if entry.kind == "file")
    entries_by_path = {entry.path: entry for entry in file_entries}
    if len(entries_by_path) != len(file_entries):
        raise ReadingHistoryError("invalid_path")

    databases: list[Path] = []
    companions: list[tuple[str, Path]] = []
    sidecars: list[Path] = []
    for entry in file_entries:
        path = root.joinpath(*PurePosixPath(entry.path).parts)
        relative = _relative_path(path, root)
        name = PurePosixPath(relative).name.casefold()
        if name == "statistics.sqlite3":
            databases.append(path)
        elif name in {"statistics.sqlite3-wal", "statistics.sqlite3-shm"}:
            companions.append((relative, path))
        elif _SIDECAR_NAME.fullmatch(PurePosixPath(relative).name):
            if not _is_sidecar_path(relative):
                raise ReadingHistoryError("unexpected_file")
            if entry.size > _MAX_SIDECAR_BYTES:
                raise ReadingHistoryError("invalid_sidecar")
            sidecars.append(path)
        else:
            raise ReadingHistoryError("unexpected_file")

    if len(databases) > 1:
        raise ReadingHistoryError("multiple_databases")
    if not databases and companions:
        raise ReadingHistoryError("wal_incomplete")
    if databases:
        database_relative = _relative_path(databases[0], root).casefold()
        expected = {f"{database_relative}-wal", f"{database_relative}-shm"}
        actual = {relative.casefold() for relative, _ in companions}
        if actual - expected or len(actual) not in {0, 2}:
            raise ReadingHistoryError("wal_incomplete")

    sorted_sidecars = tuple(
        sorted(sidecars, key=lambda path: _relative_path(path, root).casefold())
    )
    return (
        root,
        manifest,
        _StageSources(
            database=databases[0] if databases else None,
            sidecars=sorted_sidecars,
            entries_by_path=entries_by_path,
        ),
    )


def _source_digest(manifest: Sequence[ManifestEntry]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(
        (item for item in manifest if item.kind == "file"), key=lambda item: item.path.casefold()
    ):
        path = entry.path.encode("utf-8")
        digest.update(len(path).to_bytes(8, "big"))
        digest.update(path)
        digest.update(bytes.fromhex(entry.sha256 or ""))
    return digest.hexdigest()


def _column_names(connection: sqlite3.Connection, table: str) -> frozenset[str]:
    return frozenset(
        str(row[1]).casefold() for row in connection.execute(f'PRAGMA table_info("{table}")')
    )


def _as_int(value: object, *, nullable: bool = False, minimum: int | None = None) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReadingHistoryError("invalid_data")
    if minimum is not None and value < minimum:
        raise ReadingHistoryError("invalid_data")
    return value


def _as_text(value: object, *, nullable: bool = True) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ReadingHistoryError("invalid_data")
    return value


def _epoch_datetime(value: object, *, nullable: bool = False) -> datetime | None:
    timestamp = _as_int(value, nullable=nullable)
    if timestamp is None or timestamp == 0:
        return None
    if timestamp < 0:
        raise ReadingHistoryError("invalid_data")
    try:
        return datetime.fromtimestamp(timestamp, tz=UTC)
    except OverflowError, OSError, ValueError:
        raise ReadingHistoryError("invalid_data") from None


def _read_database(
    database: Path, source_digest: str
) -> tuple[tuple[ReadingBook, ...], tuple[PageEvent, ...]]:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=10)
        connection.execute("PRAGMA query_only=ON")
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        if integrity != [("ok",)]:
            raise ReadingHistoryError("database_integrity")

        user_version = connection.execute("PRAGMA user_version").fetchone()
        if user_version is None or user_version[0] != _SUPPORTED_USER_VERSION:
            raise ReadingHistoryError("schema_unsupported")
        tables = {
            str(row[0]).casefold()
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not {"book", "page_stat_data"}.issubset(tables):
            raise ReadingHistoryError("schema_invalid")
        if not _BOOK_COLUMNS.issubset(_column_names(connection, "book")):
            raise ReadingHistoryError("schema_invalid")
        if not _PAGE_COLUMNS.issubset(_column_names(connection, "page_stat_data")):
            raise ReadingHistoryError("schema_invalid")

        book_rows = connection.execute(
            """
            SELECT id, title, authors, notes, last_open, highlights, pages, series,
                   language, md5, total_read_time, total_read_pages
            FROM book ORDER BY id
            """
        ).fetchall()
        books: list[ReadingBook] = []
        for row in book_rows:
            book_id = _as_int(row[0], minimum=1)
            assert isinstance(book_id, int)
            books.append(
                ReadingBook(
                    koreader_book_id=book_id,
                    title=_as_text(row[1]),
                    authors=_as_text(row[2]),
                    notes=_as_int(row[3], nullable=True, minimum=0),
                    last_opened_at=_epoch_datetime(row[4], nullable=True),
                    highlights=_as_int(row[5], nullable=True, minimum=0),
                    pages=_as_int(row[6], nullable=True, minimum=0),
                    series=_as_text(row[7]),
                    language=_as_text(row[8]),
                    partial_md5_checksum=_as_text(row[9]),
                    total_read_time_seconds=_as_int(row[10], nullable=True, minimum=0),
                    total_read_pages=_as_int(row[11], nullable=True, minimum=0),
                )
            )

        book_ids = {book.koreader_book_id for book in books}
        event_rows = connection.execute(
            """
            SELECT id_book, page, start_time, duration, total_pages
            FROM page_stat_data ORDER BY start_time, id_book, page
            """
        ).fetchall()
        events: list[PageEvent] = []
        for row in event_rows:
            book_id = _as_int(row[0], minimum=1)
            page = _as_int(row[1], minimum=1)
            timestamp = _as_int(row[2], minimum=1)
            duration = _as_int(row[3], minimum=0)
            total_pages = _as_int(row[4], minimum=1)
            assert isinstance(book_id, int)
            assert isinstance(page, int)
            assert isinstance(timestamp, int)
            assert isinstance(duration, int)
            assert isinstance(total_pages, int)
            if book_id not in book_ids or page > total_pages:
                raise ReadingHistoryError("invalid_data")
            start_time = _epoch_datetime(timestamp)
            assert isinstance(start_time, datetime)
            try:
                start_time + timedelta(seconds=duration)
            except OverflowError:
                raise ReadingHistoryError("invalid_data") from None
            event_identity = f"{book_id}\x00{page}\x00{timestamp}".encode("ascii")
            event_id = hashlib.sha256(event_identity).hexdigest()
            events.append(
                PageEvent(
                    event_id=event_id,
                    koreader_book_id=book_id,
                    page=page,
                    start_time=start_time,
                    duration_seconds=duration,
                    total_pages=total_pages,
                    source_digest=source_digest,
                )
            )
        return tuple(books), tuple(events)
    except ReadingHistoryError:
        raise
    except sqlite3.DatabaseError, OSError, OverflowError, ValueError:
        raise ReadingHistoryError("database_integrity") from None
    finally:
        if connection is not None:
            connection.close()


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _sidecar_identity(data: dict[str, object]) -> tuple[str, str, str] | None:
    properties = data.get("doc_props")
    md5 = data.get("partial_md5_checksum")
    if not isinstance(properties, dict):
        return None
    title = properties.get("title")
    authors = properties.get("authors")
    if isinstance(title, str) and isinstance(authors, str) and isinstance(md5, str):
        return title, authors, md5
    return None


def _sidecar_display_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:240] if text else None


def _sidecar_folder_title(relative: str) -> str | None:
    parts = PurePosixPath(relative).parts
    sdr_index = next(
        (
            index
            for index in range(len(parts) - 2, -1, -1)
            if parts[index].casefold().endswith(".sdr")
        ),
        None,
    )
    if sdr_index is None:
        return None

    title = parts[sdr_index][:-4]
    suffix = PurePosixPath(title).suffix
    if suffix.casefold() in {
        ".epub",
        ".pdf",
        ".mobi",
        ".azw",
        ".azw3",
        ".kfx",
        ".txt",
        ".fb2",
        ".djvu",
        ".cbz",
        ".cbr",
        ".rtf",
        ".docx",
        ".html",
        ".htm",
    }:
        title = title[: -len(suffix)]
    return _sidecar_display_text(title)


def _parse_sidecar(
    path: Path,
    root: Path,
    entry: ManifestEntry,
    book_ids_by_identity: dict[tuple[str, str, str], tuple[int, ...]],
) -> ReadingSidecar:
    relative = _relative_path(path, root)
    try:
        raw = path.read_bytes()
    except OSError:
        raise ReadingHistoryError("invalid_sidecar") from None
    if len(raw) > _MAX_SIDECAR_BYTES:
        raise ReadingHistoryError("invalid_sidecar")
    if hashlib.sha256(raw).hexdigest() != entry.sha256:
        raise ReadingHistoryError("source_changed")
    try:
        source = raw.decode("utf-8-sig")
        data = parse_sidecar(source, max_bytes=_MAX_SIDECAR_BYTES)
    except UnicodeDecodeError, LuaSidecarError:
        raise ReadingHistoryError("invalid_sidecar") from None

    warnings: list[str] = []
    progress = _finite_number(data.get("percent_finished"))
    if progress is not None and not 0 <= progress <= 1:
        progress = None
        warnings.append("invalid_progress")
    elif "percent_finished" in data and progress is None:
        warnings.append("invalid_progress")

    status: str | None = None
    rating: float | None = None
    note: str | None = None
    summary = data.get("summary")
    if summary is not None and not isinstance(summary, dict):
        warnings.append("invalid_summary")
    elif isinstance(summary, dict):
        candidate_status = summary.get("status")
        if candidate_status is not None:
            if isinstance(candidate_status, str) and candidate_status in _VALID_STATUSES:
                status = candidate_status
            else:
                warnings.append("unknown_status")
        candidate_rating = _finite_number(summary.get("rating"))
        if candidate_rating is not None and 0 <= candidate_rating <= 5:
            rating = candidate_rating
        elif "rating" in summary:
            warnings.append("invalid_rating")
        candidate_note = summary.get("note")
        if isinstance(candidate_note, str) or candidate_note is None:
            note = candidate_note
        else:
            warnings.append("invalid_note")

    position_value = data.get("last_xpointer")
    position = position_value if isinstance(position_value, str) else None
    if position_value is not None and position is None:
        warnings.append("invalid_position")

    annotations: tuple[dict[str, object], ...] = ()
    annotation_value = data.get("annotations")
    if annotation_value is not None:
        if isinstance(annotation_value, list) and all(
            isinstance(item, dict) and all(isinstance(key, str) for key in item)
            for item in annotation_value
        ):
            annotations = tuple(dict(item) for item in annotation_value)
        else:
            warnings.append("invalid_annotations")

    identity = _sidecar_identity(data)
    matching_ids = book_ids_by_identity.get(identity, ()) if identity is not None else ()
    book_id = matching_ids[0] if len(matching_ids) == 1 else None
    if book_id is None:
        warnings.append("sidecar_unmatched")

    properties = data.get("doc_props")
    title = _sidecar_display_text(properties.get("title")) if isinstance(properties, dict) else None
    authors = (
        _sidecar_display_text(properties.get("authors")) if isinstance(properties, dict) else None
    )
    title_source: Literal["metadata", "folder"] | None = "metadata" if title else None
    if title is None:
        title = _sidecar_folder_title(relative)
        if title is not None:
            title_source = "folder"

    return ReadingSidecar(
        sidecar_id=hashlib.sha256(relative.encode("utf-8")).hexdigest(),
        source_digest=hashlib.sha256(raw).hexdigest(),
        koreader_book_id=book_id,
        progress=progress,
        status=status,
        rating=rating,
        note=note,
        position=position,
        annotations=annotations,
        warnings=tuple(sorted(set(warnings))),
        title=title,
        authors=authors,
        title_source=title_source,
    )


def estimate_reading_sessions(
    events: Sequence[PageEvent], *, inactivity_gap: timedelta = _INACTIVITY_GAP
) -> tuple[EstimatedSession, ...]:
    """Group page events into estimated sessions; KOReader stores no session IDs."""
    if not isinstance(inactivity_gap, timedelta) or inactivity_gap < timedelta(0):
        raise ValueError("inactivity_gap must be a non-negative timedelta")
    by_book: defaultdict[int, list[PageEvent]] = defaultdict(list)
    for event in events:
        if event.start_time.tzinfo is None or event.start_time.utcoffset() is None:
            raise ReadingHistoryError("invalid_data")
        if event.duration_seconds < 0 or event.page < 1 or event.total_pages < event.page:
            raise ReadingHistoryError("invalid_data")
        by_book[event.koreader_book_id].append(event)

    sessions: list[EstimatedSession] = []
    for book_id in sorted(by_book):
        ordered = sorted(by_book[book_id], key=lambda event: (event.start_time, event.event_id))
        current: list[PageEvent] = []
        for event in ordered:
            if current:
                prior = current[-1]
                prior_end = prior.start_time + timedelta(seconds=prior.duration_seconds)
                if event.start_time - prior_end >= inactivity_gap:
                    sessions.append(_estimated_session(book_id, current))
                    current = []
            current.append(event)
        if current:
            sessions.append(_estimated_session(book_id, current))
    return tuple(
        sorted(sessions, key=lambda session: (session.started_at, session.koreader_book_id))
    )


def _estimated_session(book_id: int, events: list[PageEvent]) -> EstimatedSession:
    start = min(event.start_time for event in events)
    end = max(event.start_time + timedelta(seconds=event.duration_seconds) for event in events)
    identity = f"{book_id}\x00{events[0].event_id}\x00{events[-1].event_id}".encode("ascii")
    return EstimatedSession(
        session_id=hashlib.sha256(identity).hexdigest(),
        koreader_book_id=book_id,
        started_at=start,
        ended_at=end,
        page_event_count=len(events),
        duration_seconds=sum(event.duration_seconds for event in events),
        first_page=min(event.page for event in events),
        last_page=max(event.page for event in events),
    )


def preview_reading_history(
    stage_root: Path,
    *,
    observed_at: datetime,
) -> ReadingHistoryPreview:
    """Read a verified KOReader staging directory without modifying its contents."""
    observed = _valid_observed_at(observed_at)
    root, manifest, sources = _scan_stage(Path(stage_root))
    source_digest = _source_digest(manifest)
    database = sources.database
    warnings: list[str] = []
    if database is None:
        books: tuple[ReadingBook, ...] = ()
        events: tuple[PageEvent, ...] = ()
        warnings.append("statistics_database_missing")
    else:
        books, events = _read_database(database, source_digest)

    books_by_identity: defaultdict[tuple[str, str, str], list[int]] = defaultdict(list)
    for book in books:
        if (
            isinstance(book.title, str)
            and isinstance(book.authors, str)
            and isinstance(book.partial_md5_checksum, str)
        ):
            books_by_identity[(book.title, book.authors, book.partial_md5_checksum)].append(
                book.koreader_book_id
            )
    exact_identities = {
        identity: tuple(book_ids) for identity, book_ids in books_by_identity.items()
    }

    sidecars = tuple(
        _parse_sidecar(
            path,
            root,
            sources.entries_by_path[_relative_path(path, root)],
            exact_identities,
        )
        for path in sources.sidecars
    )
    linked_ids: defaultdict[int, list[str]] = defaultdict(list)
    sidecars_per_book: defaultdict[int, int] = defaultdict(int)
    for sidecar in sidecars:
        warnings.extend(sidecar.warnings)
        if sidecar.koreader_book_id is not None:
            linked_ids[sidecar.koreader_book_id].append(sidecar.sidecar_id)
            sidecars_per_book[sidecar.koreader_book_id] += 1
            linked_book = next(
                book for book in books if book.koreader_book_id == sidecar.koreader_book_id
            )
            if (
                sidecar.progress is not None
                and linked_book.pages is not None
                and linked_book.pages > 0
                and linked_book.total_read_pages is not None
                and abs(sidecar.progress - linked_book.total_read_pages / linked_book.pages) > 0.01
            ):
                warnings.append("progress_conflict")
    if any(count > 1 for count in sidecars_per_book.values()):
        warnings.append("multiple_sidecars_for_book")

    books = tuple(
        ReadingBook(
            koreader_book_id=book.koreader_book_id,
            title=book.title,
            authors=book.authors,
            notes=book.notes,
            last_opened_at=book.last_opened_at,
            highlights=book.highlights,
            pages=book.pages,
            series=book.series,
            language=book.language,
            partial_md5_checksum=book.partial_md5_checksum,
            total_read_time_seconds=book.total_read_time_seconds,
            total_read_pages=book.total_read_pages,
            sidecar_ids=tuple(sorted(linked_ids[book.koreader_book_id])),
        )
        for book in books
    )
    sessions = estimate_reading_sessions(events)
    return ReadingHistoryPreview(
        source_digest=source_digest,
        books=books,
        events=events,
        sessions=sessions,
        warnings=tuple(sorted(set(warnings))),
        sidecars=sidecars,
        observed_at=observed,
    )
