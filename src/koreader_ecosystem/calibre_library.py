from __future__ import annotations

import math
import re
import shutil
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from koreader_ecosystem.backup import _running_process_names
from koreader_ecosystem.calibre_catalog import CalibreBook, load_calibre_catalog
from koreader_ecosystem.processes import run_hidden
from koreader_ecosystem.safety import SafetyViolation, require_calibre_closed


class CalibreLibraryError(RuntimeError):
    """A safe, stable error from an explicit Calibre library action."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class ImportCandidate:
    book_id: int
    title: str
    authors: tuple[str, ...]
    formats: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class CalibreImportPreview:
    name: str
    title: str
    authors: tuple[str, ...]
    isbn: str | None
    format: str
    candidates: tuple[ImportCandidate, ...]


type Runner = Callable[..., subprocess.CompletedProcess[str]]

_SUPPORTED_FORMATS = frozenset(
    {
        "azw",
        "azw1",
        "azw3",
        "azw4",
        "cb7",
        "cbc",
        "cbr",
        "cbz",
        "chm",
        "docx",
        "epub",
        "fb2",
        "fbz",
        "html",
        "htmlz",
        "imp",
        "kepub",
        "lit",
        "lrf",
        "lrx",
        "mobi",
        "odt",
        "oebzip",
        "pdb",
        "pdf",
        "pml",
        "pmlz",
        "pobi",
        "prc",
        "rar",
        "rb",
        "rtf",
        "snb",
        "tpz",
        "txt",
        "txtz",
        "updb",
        "zip",
    }
)
_DC_NS = "http://purl.org/dc/elements/1.1/"
_OPF_NS = "http://www.idpf.org/2007/opf"


def _calibredb_executable() -> str:
    executable = shutil.which("calibredb")
    if executable is None:
        raise CalibreLibraryError("calibredb_unavailable")
    return executable


def _run_calibredb(
    library: Path,
    command: str,
    command_args: Sequence[str],
    runner: Runner,
    *,
    timeout: int = 120,
) -> str:
    try:
        result = runner(
            [_calibredb_executable(), "--with-library", str(library), command, *command_args],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except OSError, subprocess.SubprocessError:
        raise CalibreLibraryError("calibredb_command_failed") from None
    if result.returncode != 0:
        raise CalibreLibraryError("calibredb_command_failed")
    if not isinstance(result.stdout, str):
        raise CalibreLibraryError("calibredb_invalid_response")
    return result.stdout


def export_calibre_book_format(
    library: Path,
    book_id: int,
    format_name: str,
    destination: Path,
    runner: Runner = run_hidden,
) -> Path:
    """Export exactly one selected Calibre ebook format to a new private directory."""
    resolved_library = _validate_library(library)
    if isinstance(book_id, bool) or not isinstance(book_id, int) or book_id < 1:
        raise CalibreLibraryError("invalid_book_id")
    normalized_format = format_name.upper() if isinstance(format_name, str) else ""
    if normalized_format not in {"AZW", "AZW3", "CBZ", "EPUB", "FB2", "MOBI", "PDF", "TXT"}:
        raise CalibreLibraryError("invalid_export_format")
    resolved_destination = Path(destination).expanduser().resolve(strict=False)
    if not resolved_destination.is_absolute() or resolved_destination.exists():
        raise CalibreLibraryError("invalid_export_destination")
    _ensure_calibre_closed()

    try:
        books = load_calibre_catalog(resolved_library, runner)
    except Exception:
        raise CalibreLibraryError("calibre_catalog_unavailable") from None
    book = next((candidate for candidate in books if candidate.book_id == book_id), None)
    if book is None:
        raise CalibreLibraryError("book_not_found")
    if normalized_format not in book.formats:
        raise CalibreLibraryError("format_unavailable")

    try:
        resolved_destination.mkdir(parents=True, exist_ok=False)
    except OSError:
        raise CalibreLibraryError("invalid_export_destination") from None
    _run_calibredb(
        resolved_library,
        "export",
        (
            f"--to-dir={resolved_destination}",
            "--single-dir",
            "--dont-save-cover",
            "--dont-write-opf",
            "--dont-save-extra-files",
            "--dont-update-metadata",
            f"--formats={normalized_format}",
            str(book_id),
        ),
        runner,
        timeout=600,
    )
    exported = tuple(resolved_destination.rglob(f"*.{normalized_format.lower()}"))
    if len(exported) != 1:
        raise CalibreLibraryError("calibre_export_unconfirmed")
    target = exported[0]
    try:
        resolved_target = target.resolve(strict=True)
        resolved_target.relative_to(resolved_destination)
        details = resolved_target.stat()
    except OSError, RuntimeError, ValueError:
        raise CalibreLibraryError("calibre_export_unconfirmed") from None
    if (
        target.is_symlink()
        or target.is_junction()
        or not resolved_target.is_file()
        or details.st_size <= 0
    ):
        raise CalibreLibraryError("calibre_export_unconfirmed")
    return resolved_target


def _validate_library(library: Path) -> Path:
    resolved = Path(library).expanduser().resolve(strict=False)
    if not resolved.is_dir() or not (resolved / "metadata.db").is_file():
        raise CalibreLibraryError("calibre_unavailable")
    return resolved


def _ensure_calibre_closed() -> None:
    try:
        require_calibre_closed(_running_process_names())
    except SafetyViolation:
        raise CalibreLibraryError("calibre_open") from None


def _validate_import_file(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise CalibreLibraryError("invalid_import_file")
    try:
        resolved = path.resolve(strict=True)
    except OSError, RuntimeError:
        raise CalibreLibraryError("import_file_unavailable") from None
    if (
        path.is_symlink()
        or path.is_junction()
        or not resolved.is_file()
        or resolved.suffix.casefold().lstrip(".") not in _SUPPORTED_FORMATS
        or resolved.stat().st_size <= 0
    ):
        raise CalibreLibraryError("invalid_import_file")
    return resolved


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return (
        "".join(character for character in normalized if not unicodedata.combining(character))
        .casefold()
        .strip()
    )


def _normalize_isbn(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[^0-9Xx]", "", value).upper()
    return normalized if len(normalized) in {10, 13} else None


def _book_isbns(book: CalibreBook) -> set[str]:
    return {
        isbn
        for key, value in book.identifiers.items()
        if key.casefold().startswith("isbn")
        if (isbn := _normalize_isbn(value)) is not None
    }


def _read_file_metadata(path: Path, runner: Runner) -> tuple[str, tuple[str, ...], str | None]:
    executable = shutil.which("ebook-meta")
    if executable is None:
        raise CalibreLibraryError("ebook_meta_unavailable")
    try:
        with tempfile.TemporaryDirectory(prefix="kre-import-") as temporary:
            opf_path = Path(temporary) / "metadata.opf"
            result = runner(
                [executable, str(path), "--to-opf", str(opf_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            if result.returncode != 0 or not opf_path.is_file():
                raise CalibreLibraryError("ebook_metadata_unavailable")
            try:
                root = ET.parse(opf_path).getroot()
            except ET.ParseError, OSError:
                raise CalibreLibraryError("ebook_metadata_invalid") from None
    except CalibreLibraryError:
        raise
    except OSError, subprocess.SubprocessError:
        raise CalibreLibraryError("ebook_metadata_unavailable") from None

    title_element = root.find(f".//{{{_DC_NS}}}title")
    title = (
        title_element.text.strip()
        if title_element is not None and title_element.text
        else path.stem
    )
    if not title or len(title) > 2048:
        raise CalibreLibraryError("ebook_metadata_invalid")
    authors = tuple(
        element.text.strip()
        for element in root.findall(f".//{{{_DC_NS}}}creator")
        if element.text and element.text.strip()
    )
    isbn: str | None = None
    for identifier in root.findall(f".//{{{_DC_NS}}}identifier"):
        scheme = identifier.attrib.get(f"{{{_OPF_NS}}}scheme", "").casefold()
        candidate = identifier.text.strip() if identifier.text else ""
        if scheme in {"isbn", "isbn-10", "isbn-13"} or _normalize_isbn(candidate):
            isbn = _normalize_isbn(candidate)
            if isbn:
                break
    return title, authors[:32], isbn


def _candidate_isbn(book: CalibreBook, isbn: str | None) -> bool:
    return isbn is not None and isbn in _book_isbns(book)


def _matching_candidates(
    title: str,
    authors: tuple[str, ...],
    isbn: str | None,
    books: Sequence[CalibreBook],
) -> tuple[ImportCandidate, ...]:
    normalized_title = _normalize_text(title)
    normalized_authors = {_normalize_text(author) for author in authors}
    candidates: list[ImportCandidate] = []
    for book in books:
        same_isbn = _candidate_isbn(book, isbn)
        same_title = _normalize_text(book.title) == normalized_title
        same_author = bool(
            normalized_authors.intersection(_normalize_text(author) for author in book.authors)
        )
        if same_isbn:
            reason = "ISBN igual"
        elif same_title and same_author:
            reason = "Título e autor iguais"
        else:
            continue
        candidates.append(
            ImportCandidate(
                book_id=book.book_id,
                title=book.title,
                authors=book.authors,
                formats=book.formats,
                reason=reason,
            )
        )
    return tuple(candidates[:20])


def preview_calibre_import(
    library: Path,
    files: Sequence[str | Path],
    runner: Runner = run_hidden,
) -> tuple[CalibreImportPreview, ...]:
    """Read file metadata and exact duplicate candidates without changing Calibre."""
    resolved_library = _validate_library(library)
    if not files or len(files) > 20:
        raise CalibreLibraryError("invalid_import_request")
    safe_files = tuple(_validate_import_file(path) for path in files)
    books = load_calibre_catalog(resolved_library, runner)
    previews: list[CalibreImportPreview] = []
    for path in safe_files:
        title, authors, isbn = _read_file_metadata(path, runner)
        previews.append(
            CalibreImportPreview(
                name=path.name,
                title=title,
                authors=authors,
                isbn=isbn,
                format=path.suffix.casefold().lstrip(".").upper(),
                candidates=_matching_candidates(title, authors, isbn, books),
            )
        )
    return tuple(previews)


def add_calibre_file(
    library: Path,
    file: str | Path,
    *,
    mode: str,
    target_book_id: int | None = None,
    runner: Runner = run_hidden,
) -> CalibreBook:
    """Add a new logical book or a format to a reviewed, unambiguous Calibre record."""
    resolved_library = _validate_library(library)
    source = _validate_import_file(file)
    if mode not in {"new", "attach"}:
        raise CalibreLibraryError("invalid_import_decision")
    _ensure_calibre_closed()

    try:
        before = load_calibre_catalog(resolved_library, runner)
    except Exception:
        raise CalibreLibraryError("calibre_catalog_unavailable") from None
    title, authors, isbn = _read_file_metadata(source, runner)
    candidates = _matching_candidates(title, authors, isbn, before)

    if mode == "attach":
        if (
            isinstance(target_book_id, bool)
            or not isinstance(target_book_id, int)
            or target_book_id < 1
            or not any(candidate.book_id == target_book_id for candidate in candidates)
        ):
            raise CalibreLibraryError("duplicate_review_required")
        target = next(book for book in before if book.book_id == target_book_id)
        file_format = source.suffix.casefold().lstrip(".").upper()
        if file_format in target.formats:
            raise CalibreLibraryError("format_already_exists")
        _run_calibredb(
            resolved_library,
            "add_format",
            ("--dont-replace", str(target_book_id), str(source)),
            runner,
        )
        try:
            updated = next(
                book
                for book in load_calibre_catalog(resolved_library, runner)
                if book.book_id == target_book_id
            )
        except Exception:
            raise CalibreLibraryError("write_unconfirmed") from None
        if file_format not in updated.formats:
            raise CalibreLibraryError("write_unconfirmed")
        return updated

    before_uuids = {book.uuid for book in before}
    _run_calibredb(
        resolved_library,
        "add",
        ("--duplicates", str(source)),
        runner,
    )
    try:
        after = load_calibre_catalog(resolved_library, runner)
    except Exception:
        raise CalibreLibraryError("write_unconfirmed") from None
    created = [book for book in after if book.uuid not in before_uuids]
    if len(created) != 1 or source.suffix.casefold().lstrip(".").upper() not in created[0].formats:
        raise CalibreLibraryError("write_unconfirmed")
    return created[0]


_EDITABLE_FIELDS = frozenset(
    {
        "title",
        "authors",
        "series",
        "series_index",
        "publisher",
        "pubdate",
        "languages",
        "tags",
        "comments",
        "rating",
        "identifiers",
        "custom_values",
    }
)

_MAX_COVER_BYTES = 20 * 1024 * 1024
_SUPPORTED_COVER_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})


def _normalize_edit_value(field: str, value: object) -> str:
    if field == "title":
        if not isinstance(value, str) or not value.strip() or len(value) > 2048:
            raise CalibreLibraryError("invalid_metadata")
        return value.strip()
    if field in {"authors", "languages", "tags"}:
        if (
            not isinstance(value, list)
            or len(value) > 128
            or not all(isinstance(item, str) for item in value)
        ):
            raise CalibreLibraryError("invalid_metadata")
        normalized = [item.strip() for item in value if item.strip()]
        if field == "authors" and not normalized:
            raise CalibreLibraryError("invalid_metadata")
        if any("," in item for item in normalized) or (
            field == "authors" and any(" & " in item for item in normalized)
        ):
            raise CalibreLibraryError("invalid_metadata")
        separator = " & " if field == "authors" else ","
        return separator.join(normalized)
    if field == "identifiers":
        if not isinstance(value, dict) or len(value) > 64:
            raise CalibreLibraryError("invalid_metadata")
        pieces: list[str] = []
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", key)
                or not isinstance(item, str)
                or len(item) > 256
                or "," in item
            ):
                raise CalibreLibraryError("invalid_metadata")
            pieces.append(f"{key}:{item.strip()}")
        return ",".join(pieces)
    if field == "series_index":
        if value is None or value == "":
            return ""
        if (
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value < 0
        ):
            raise CalibreLibraryError("invalid_metadata")
        return str(value)
    if field == "rating":
        if value is None or value == "":
            return ""
        if (
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or not math.isfinite(value)
            or not 0 <= value <= 10
        ):
            raise CalibreLibraryError("invalid_metadata")
        return str(value)
    if field == "pubdate":
        if value is None or value == "":
            return ""
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
            raise CalibreLibraryError("invalid_metadata")
        return value
    maximum = 131_072 if field == "comments" else 4096
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise CalibreLibraryError("invalid_metadata")
    return value.strip()


def _normalize_custom_value(datatype: str, enum_values: tuple[str, ...], value: object) -> str:
    if value is None or value == "":
        return ""
    if datatype == "bool":
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str) and value.casefold() in {"true", "false"}:
            return value.casefold()
        raise CalibreLibraryError("invalid_metadata")
    if datatype == "int":
        if isinstance(value, bool):
            raise CalibreLibraryError("invalid_metadata")
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str) and re.fullmatch(r"-?\d{1,18}", value.strip()):
            return str(int(value.strip()))
        raise CalibreLibraryError("invalid_metadata")
    if datatype in {"float", "rating"}:
        if isinstance(value, bool) or not isinstance(value, int | float | str):
            raise CalibreLibraryError("invalid_metadata")
        try:
            number = float(value)
        except ValueError, OverflowError:
            raise CalibreLibraryError("invalid_metadata") from None
        if not math.isfinite(number) or (datatype == "rating" and not 0 <= number <= 10):
            raise CalibreLibraryError("invalid_metadata")
        return str(number)
    if datatype == "datetime":
        if not isinstance(value, str) or len(value) > 64:
            raise CalibreLibraryError("invalid_metadata")
        try:
            date.fromisoformat(value) if len(value) == 10 else datetime.fromisoformat(value)
        except ValueError:
            raise CalibreLibraryError("invalid_metadata") from None
        return value
    if datatype == "enumeration":
        if not isinstance(value, str) or len(value) > 4096 or value not in enum_values:
            raise CalibreLibraryError("invalid_metadata")
        return value
    if datatype in {"text", "comments", "series"}:
        maximum = 131_072 if datatype == "comments" else 4096
        if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
            raise CalibreLibraryError("invalid_metadata")
        return value.strip()
    raise CalibreLibraryError("invalid_metadata")


def _validate_cover_file(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise CalibreLibraryError("invalid_metadata")
    try:
        resolved = path.resolve(strict=True)
        details = resolved.stat()
    except OSError, RuntimeError:
        raise CalibreLibraryError("invalid_metadata") from None
    if (
        path.is_symlink()
        or path.is_junction()
        or not resolved.is_file()
        or resolved.suffix.casefold() not in _SUPPORTED_COVER_SUFFIXES
        or details.st_size <= 0
        or details.st_size > _MAX_COVER_BYTES
    ):
        raise CalibreLibraryError("invalid_metadata")
    return resolved


def edit_calibre_metadata(
    library: Path,
    book_id: int,
    changes: Mapping[str, object],
    runner: Runner = run_hidden,
    *,
    cover_path: str | Path | None = None,
) -> CalibreBook:
    """Edit supported fields through calibredb and return the reread catalogue record."""
    resolved_library = _validate_library(library)
    if isinstance(book_id, bool) or not isinstance(book_id, int) or book_id < 1:
        raise CalibreLibraryError("invalid_book_id")
    if not isinstance(changes, Mapping) or not set(changes).issubset(_EDITABLE_FIELDS):
        raise CalibreLibraryError("invalid_metadata")
    if not changes and cover_path is None:
        raise CalibreLibraryError("invalid_metadata")
    _ensure_calibre_closed()

    try:
        before = load_calibre_catalog(resolved_library, runner)
    except Exception:
        raise CalibreLibraryError("calibre_catalog_unavailable") from None
    current = next((book for book in before if book.book_id == book_id), None)
    if current is None:
        raise CalibreLibraryError("book_not_found")

    custom_changes = changes.get("custom_values", {})
    if not isinstance(custom_changes, dict) or len(custom_changes) > 128:
        raise CalibreLibraryError("invalid_metadata")
    custom_columns = {column.label: column for column in current.custom_column_definitions}
    normalized_custom: dict[str, str] = {}
    for label, value in custom_changes.items():
        column = custom_columns.get(label) if isinstance(label, str) else None
        if (
            column is None
            or not column.editable
            or column.is_multiple
            or column.datatype == "composite"
        ):
            raise CalibreLibraryError("invalid_metadata")
        normalized_custom[label] = _normalize_custom_value(
            column.datatype,
            column.enum_values,
            value,
        )

    normalized_changes = {
        field: _normalize_edit_value(field, value)
        for field, value in changes.items()
        if field != "custom_values"
    }
    resolved_cover = _validate_cover_file(cover_path) if cover_path is not None else None
    args = [f"--field={field}:{value}" for field, value in normalized_changes.items()]
    if resolved_cover is not None:
        args.append(f"--field=cover:{resolved_cover}")
    if args:
        _run_calibredb(resolved_library, "set_metadata", (str(book_id), *args), runner)
    for label, value in normalized_custom.items():
        _run_calibredb(
            resolved_library,
            "set_custom",
            (label, str(book_id), value),
            runner,
        )

    try:
        updated = next(
            book
            for book in load_calibre_catalog(resolved_library, runner)
            if book.book_id == book_id
        )
    except Exception:
        raise CalibreLibraryError("write_unconfirmed") from None
    if resolved_cover is not None and updated.cover_path is None:
        raise CalibreLibraryError("write_unconfirmed")
    for label, expected in normalized_custom.items():
        actual = updated.custom_values.get(label)
        if actual is None and expected == "":
            continue
        if isinstance(actual, int | float) and not isinstance(actual, bool):
            try:
                matches = math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-9)
            except ValueError:
                matches = False
        else:
            matches = str(actual).casefold() == expected.casefold()
        if not matches:
            raise CalibreLibraryError("write_unconfirmed")
    return updated


def edit_calibre_tags_batch(
    library: Path,
    book_ids: Sequence[int],
    *,
    add_tags: Sequence[str],
    remove_tags: Sequence[str],
    runner: Runner = run_hidden,
) -> tuple[CalibreBook, ...]:
    """Apply tag additions/removals to reviewed books and reread every result."""
    resolved_library = _validate_library(library)
    if (
        not book_ids
        or len(book_ids) > 100
        or any(
            isinstance(book_id, bool) or not isinstance(book_id, int) or book_id < 1
            for book_id in book_ids
        )
    ):
        raise CalibreLibraryError("invalid_book_id")
    if len(set(book_ids)) != len(book_ids):
        raise CalibreLibraryError("invalid_book_id")
    normalized_add = (
        [tag for tag in _normalize_edit_value("tags", list(add_tags)).split(",") if tag]
        if add_tags
        else []
    )
    normalized_remove = (
        [tag for tag in _normalize_edit_value("tags", list(remove_tags)).split(",") if tag]
        if remove_tags
        else []
    )
    if not normalized_add and not normalized_remove:
        raise CalibreLibraryError("invalid_metadata")
    add_keys = {tag.casefold() for tag in normalized_add}
    remove_keys = {tag.casefold() for tag in normalized_remove}
    if add_keys & remove_keys:
        raise CalibreLibraryError("invalid_metadata")

    _ensure_calibre_closed()
    try:
        before = load_calibre_catalog(resolved_library, runner)
    except Exception:
        raise CalibreLibraryError("calibre_catalog_unavailable") from None
    by_id = {book.book_id: book for book in before}
    if any(book_id not in by_id for book_id in book_ids):
        raise CalibreLibraryError("book_not_found")

    expected: dict[int, tuple[str, ...]] = {}
    for book_id in book_ids:
        remaining = [tag for tag in by_id[book_id].tags if tag.casefold() not in remove_keys]
        present = {tag.casefold() for tag in remaining}
        for tag in normalized_add:
            if tag.casefold() not in present:
                remaining.append(tag)
                present.add(tag.casefold())
        expected[book_id] = tuple(remaining)

    for book_id in book_ids:
        tags = _normalize_edit_value("tags", list(expected[book_id]))
        _run_calibredb(
            resolved_library,
            "set_metadata",
            (str(book_id), f"--field=tags:{tags}"),
            runner,
        )
    try:
        after = load_calibre_catalog(resolved_library, runner)
    except Exception:
        raise CalibreLibraryError("write_unconfirmed") from None
    after_by_id = {book.book_id: book for book in after}
    updated: list[CalibreBook] = []
    for book_id in book_ids:
        book = after_by_id.get(book_id)
        if book is None or book.tags != expected[book_id]:
            raise CalibreLibraryError("write_unconfirmed")
        updated.append(book)
    return tuple(updated)


def remove_calibre_book(
    library: Path,
    book_id: int,
    runner: Runner = run_hidden,
) -> None:
    """Move one Calibre book to its Recycle Bin and verify it left the catalog."""
    resolved_library = _validate_library(library)
    if isinstance(book_id, bool) or not isinstance(book_id, int) or book_id < 1:
        raise CalibreLibraryError("invalid_book_id")
    _ensure_calibre_closed()

    try:
        before = load_calibre_catalog(resolved_library, runner)
    except Exception:
        raise CalibreLibraryError("calibre_catalog_unavailable") from None
    if not any(book.book_id == book_id for book in before):
        raise CalibreLibraryError("book_not_found")

    # Omitting calibredb's --permanent option leaves Calibre's Recycle Bin enabled.
    _run_calibredb(resolved_library, "remove", (str(book_id),), runner)
    try:
        remains = any(
            book.book_id == book_id for book in load_calibre_catalog(resolved_library, runner)
        )
    except Exception:
        raise CalibreLibraryError("write_unconfirmed") from None
    if remains:
        raise CalibreLibraryError("write_unconfirmed")
