from __future__ import annotations

import ast
import json
import math
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from koreader_ecosystem.processes import run_hidden


class CalibreCatalogError(RuntimeError):
    """Raised when Calibre metadata cannot be read and validated safely."""


@dataclass(frozen=True, slots=True)
class CalibreCustomColumn:
    label: str
    name: str
    datatype: str
    editable: bool
    is_multiple: bool
    enum_values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CalibreBook:
    book_id: int
    uuid: str
    title: str
    authors: tuple[str, ...]
    identifiers: dict[str, str]
    custom_values: dict[str, object]
    custom_column_definitions: tuple[CalibreCustomColumn, ...] = ()
    cover_path: str | None = None
    series: str | None = None
    series_index: float | None = None
    publisher: str | None = None
    pubdate: str | None = None
    languages: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    comments: str = ""
    rating: float | None = None
    formats: tuple[str, ...] = ()
    size: int = 0


@dataclass(frozen=True, slots=True)
class MatchResult:
    status: Literal["matched", "unmatched", "ambiguous", "manual"]
    book: CalibreBook | None
    reason_code: str


type Runner = Callable[..., subprocess.CompletedProcess[str]]

_SUPPORTED_CUSTOM_TYPES = frozenset(
    {
        "bool",
        "comments",
        "composite",
        "datetime",
        "enumeration",
        "float",
        "int",
        "rating",
        "series",
        "text",
    }
)
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9_-]+$")
_SIDE_CAR_KEY = re.compile(r"^[0-9a-f]{64}$")
_ISBN_LABEL = re.compile(r"^isbn(?:-(?:10|13))?:(.+)$", re.IGNORECASE)
_MAX_COLUMN_OUTPUT_BYTES = 2_097_152
_MAX_COLUMN_COUNT = 1_024
_SUPPORTED_COVER_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})


def _calibredb_executable() -> str:
    executable = shutil.which("calibredb")
    if executable is None:
        raise CalibreCatalogError("Calibre command-line tool is unavailable")
    return executable


def _run_calibredb(
    executable: str,
    library: Path,
    command: str,
    command_args: Sequence[str],
    runner: Runner,
) -> str:
    args = [executable, "--with-library", str(library), command, *command_args]
    try:
        result = runner(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except OSError, subprocess.SubprocessError:
        raise CalibreCatalogError("Calibre catalog command could not complete") from None
    if result.returncode != 0:
        raise CalibreCatalogError("Calibre catalog command failed")
    if not isinstance(result.stdout, str):
        raise CalibreCatalogError("Calibre returned an invalid catalog response")
    return result.stdout


def _consume_python_mapping(lines: Sequence[str], start: int) -> tuple[str, int]:
    opening_to_closing = {"{": "}", "[": "]", "(": ")"}
    stack: list[str] = []
    quote: str | None = None
    escaped = False
    literal: list[str] = []
    started = False

    for line_index in range(start, len(lines)):
        line = lines[line_index]
        if line_index > start:
            literal.append("\n")
        for character in line:
            if not started and character.isspace():
                continue
            started = True
            literal.append(character)
            if quote is not None:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
                continue
            if character in ("'", '"'):
                quote = character
            elif character in opening_to_closing:
                stack.append(opening_to_closing[character])
            elif character in "}])":
                if not stack or stack.pop() != character:
                    raise CalibreCatalogError("Calibre custom-column details are malformed")
                if not stack:
                    return "".join(literal), line_index + 1

    raise CalibreCatalogError("Calibre custom-column details are incomplete")


def _load_custom_columns(output: str) -> tuple[CalibreCustomColumn, ...]:
    try:
        if len(output.encode("utf-8")) > _MAX_COLUMN_OUTPUT_BYTES:
            raise CalibreCatalogError("Calibre custom-column response exceeds the size limit")
    except UnicodeEncodeError:
        raise CalibreCatalogError("Calibre custom-column response is invalid") from None

    lines = output.replace("\r\n", "\n").split("\n")
    columns: list[CalibreCustomColumn] = []
    seen_labels: set[str] = set()
    index = 0
    while index < len(lines):
        while index < len(lines) and not lines[index].strip():
            index += 1
        if index == len(lines):
            break

        heading = lines[index].strip()
        index += 1
        label = heading.removeprefix("#")
        if not _SAFE_LABEL.fullmatch(label):
            raise CalibreCatalogError("Calibre returned an unsupported custom-column label")
        if label.casefold() in seen_labels:
            raise CalibreCatalogError("Calibre returned duplicate custom-column labels")
        seen_labels.add(label.casefold())

        while index < len(lines) and not lines[index].strip():
            index += 1
        literal, index = _consume_python_mapping(lines, index)
        try:
            details = ast.literal_eval(literal)
        except SyntaxError, ValueError, RecursionError:
            raise CalibreCatalogError("Calibre custom-column details are invalid") from None
        if not isinstance(details, dict):
            raise CalibreCatalogError("Calibre custom-column details are invalid")

        detail_label = details.get("label")
        datatype = details.get("datatype")
        number = details.get("num")
        name = details.get("name", label)
        editable = details.get("editable", False)
        is_multiple = details.get("is_multiple", False)
        display = details.get("display", {})
        enum_values = display.get("enum_values", ()) if isinstance(display, dict) else None
        if (
            not isinstance(detail_label, str)
            or detail_label.removeprefix("#") != label
            or not isinstance(datatype, str)
            or datatype not in _SUPPORTED_CUSTOM_TYPES
            or not isinstance(name, str)
            or len(name) > 4096
            or type(editable) is not bool
            or type(is_multiple) is not bool
            or not isinstance(display, dict)
            or not isinstance(enum_values, list | tuple)
            or not all(isinstance(value, str) and len(value) <= 4096 for value in enum_values)
            or len(enum_values) > 1024
            or not isinstance(number, int)
            or isinstance(number, bool)
            or number < 1
        ):
            raise CalibreCatalogError("Calibre custom-column type metadata is invalid")
        columns.append(
            CalibreCustomColumn(
                label=label,
                name=name.strip() or label,
                datatype=datatype,
                editable=editable,
                is_multiple=is_multiple,
                enum_values=tuple(enum_values),
            )
        )
        if len(columns) > _MAX_COLUMN_COUNT:
            raise CalibreCatalogError("Calibre has too many custom columns")

    return tuple(columns)


def _normalize_authors(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        if not all(isinstance(author, str) for author in value):
            raise CalibreCatalogError("Calibre returned invalid author metadata")
        return tuple(author for author in value if author)
    if isinstance(value, str):
        if not value:
            return ()
        return tuple(author for author in value.split(" & ") if author)
    raise CalibreCatalogError("Calibre returned invalid author metadata")


def _normalize_string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        items = value
    else:
        raise CalibreCatalogError(f"Calibre returned invalid {field_name}")
    normalized = tuple(item.strip() for item in items if item.strip())
    if len(normalized) > 1024 or any(len(item) > 4096 for item in normalized):
        raise CalibreCatalogError(f"Calibre returned oversized {field_name}")
    return normalized


def _normalize_format_tuple(value: object) -> tuple[str, ...]:
    """Normalize Calibre's format names or file paths to safe extensions."""
    raw_formats = _normalize_string_tuple(value, "formats")
    formats: list[str] = []
    for raw_format in raw_formats:
        normalized_path = raw_format.replace("\\", "/")
        suffix = Path(normalized_path).suffix
        if suffix:
            format_name = suffix[1:]
        elif "/" in normalized_path:
            raise CalibreCatalogError("Calibre returned an invalid book format")
        else:
            format_name = raw_format
        normalized = format_name.upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,31}", normalized):
            raise CalibreCatalogError("Calibre returned an invalid book format")
        if normalized not in formats:
            formats.append(normalized)
    return tuple(formats)


def _optional_text(value: object, field_name: str, *, maximum: int = 4096) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise CalibreCatalogError(f"Calibre returned invalid {field_name}")
    normalized = value.strip()
    return normalized or None


def _optional_number(value: object, field_name: str) -> float | None:
    if value is None or value == "":
        return None
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise CalibreCatalogError(f"Calibre returned invalid {field_name}")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise CalibreCatalogError(f"Calibre returned invalid {field_name}")
    return normalized


def _decode_books(
    output: str,
    custom_columns: Sequence[CalibreCustomColumn],
    library: Path,
) -> tuple[CalibreBook, ...]:
    try:
        decoded = json.loads(output)
    except json.JSONDecodeError, TypeError:
        raise CalibreCatalogError("Calibre returned invalid catalog JSON") from None
    if not isinstance(decoded, list):
        raise CalibreCatalogError("Calibre catalog must be a JSON list")

    books: list[CalibreBook] = []
    seen_ids: set[int] = set()
    for record in decoded:
        if not isinstance(record, dict):
            raise CalibreCatalogError("Calibre catalog contains an invalid book record")
        book_id = record.get("id")
        book_uuid = record.get("uuid")
        title = record.get("title")
        raw_identifiers = record.get("identifiers")
        if (
            not isinstance(book_id, int)
            or isinstance(book_id, bool)
            or book_id < 1
            or book_id in seen_ids
            or not isinstance(book_uuid, str)
            or not book_uuid.strip()
            or not isinstance(title, str)
            or not isinstance(raw_identifiers, dict)
            or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in raw_identifiers.items()
            )
        ):
            raise CalibreCatalogError("Calibre catalog contains invalid book metadata")
        seen_ids.add(book_id)

        custom_values: dict[str, object] = {}
        for column in custom_columns:
            value = record.get(f"*{column.label}")
            if isinstance(value, dict) and not all(isinstance(key, str) for key in value):
                raise CalibreCatalogError("Calibre returned invalid custom-column data")
            custom_values[column.label] = value

        series_index = _optional_number(record.get("series_index"), "series index")
        rating = _optional_number(record.get("rating"), "rating")
        raw_size = record.get("size", 0)
        if raw_size is None:
            raw_size = 0
        if not isinstance(raw_size, int) or isinstance(raw_size, bool) or raw_size < 0:
            raise CalibreCatalogError("Calibre returned invalid format size")
        comments = record.get("comments", "")
        if comments is None:
            comments = ""
        if not isinstance(comments, str) or len(comments) > 131_072:
            raise CalibreCatalogError("Calibre returned invalid description")

        raw_cover = record.get("cover")
        cover_path: str | None = None
        if isinstance(raw_cover, str) and raw_cover.strip():
            try:
                resolved_library = library.resolve(strict=True)
                resolved_cover = Path(raw_cover).expanduser().resolve(strict=True)
                resolved_cover.relative_to(resolved_library)
                if (
                    resolved_cover.is_file()
                    and resolved_cover.suffix.casefold() in _SUPPORTED_COVER_SUFFIXES
                ):
                    cover_path = str(resolved_cover)
            except OSError, RuntimeError, ValueError:
                # A missing or out-of-library cover does not invalidate book metadata.
                pass

        books.append(
            CalibreBook(
                book_id=book_id,
                uuid=book_uuid.strip(),
                title=title,
                authors=_normalize_authors(record.get("authors")),
                identifiers=dict(raw_identifiers),
                custom_values=custom_values,
                custom_column_definitions=tuple(custom_columns),
                cover_path=cover_path,
                series=_optional_text(record.get("series"), "series"),
                series_index=series_index,
                publisher=_optional_text(record.get("publisher"), "publisher"),
                pubdate=_optional_text(record.get("pubdate"), "publication date"),
                languages=_normalize_string_tuple(record.get("languages"), "languages"),
                tags=_normalize_string_tuple(record.get("tags"), "tags"),
                comments=comments,
                rating=rating,
                formats=_normalize_format_tuple(record.get("formats")),
                size=raw_size,
            )
        )
    return tuple(books)


def load_calibre_catalog(
    library: Path,
    runner: Runner = run_hidden,
) -> tuple[CalibreBook, ...]:
    """Read the Calibre book catalog and custom-column values through its CLI."""
    library = Path(library)
    if not library.is_dir() or not (library / "metadata.db").is_file():
        raise CalibreCatalogError("Calibre library is unavailable")

    custom_columns = load_calibre_custom_column_definitions(library, runner)
    executable = _calibredb_executable()
    fields = [
        "uuid",
        "title",
        "authors",
        "identifiers",
        "cover",
        "series",
        "series_index",
        "publisher",
        "pubdate",
        "languages",
        "tags",
        "comments",
        "rating",
        "formats",
        "size",
    ]
    fields.extend(f"*{column.label}" for column in custom_columns)
    listing = _run_calibredb(
        executable,
        library,
        "list",
        ("--for-machine", "--fields", ",".join(fields)),
        runner,
    )
    return _decode_books(listing, custom_columns, library)


def load_calibre_custom_columns(
    library: Path,
    runner: Runner = run_hidden,
) -> dict[str, str]:
    """Return validated Calibre custom-column labels and their data types."""
    library = Path(library)
    if not library.is_dir() or not (library / "metadata.db").is_file():
        raise CalibreCatalogError("Calibre library is unavailable")
    return {
        column.label: column.datatype
        for column in load_calibre_custom_column_definitions(library, runner)
    }


def load_calibre_custom_column_definitions(
    library: Path,
    runner: Runner = run_hidden,
) -> tuple[CalibreCustomColumn, ...]:
    """Return validated Calibre custom-column definitions for safe editing."""
    library = Path(library)
    if not library.is_dir() or not (library / "metadata.db").is_file():
        raise CalibreCatalogError("Calibre library is unavailable")
    output = _run_calibredb(
        _calibredb_executable(),
        library,
        "custom_columns",
        ("--details",),
        runner,
    )
    return _load_custom_columns(output)


def _identifier_tokens(sidecar: Mapping[str, object]) -> tuple[str, ...]:
    values: list[str] = []
    for section_name in ("stats", "doc_props"):
        section = sidecar.get(section_name)
        if not isinstance(section, dict):
            continue
        identifiers = section.get("identifiers")
        if isinstance(identifiers, str):
            values.extend(item for item in re.split(r"[\s\\]+", identifiers) if item)
    return tuple(values)


def _calibre_uuids(sidecar: Mapping[str, object]) -> tuple[str, ...]:
    values: list[str] = []
    for item in _identifier_tokens(sidecar):
        if item.startswith("calibre:"):
            value = item.removeprefix("calibre:").strip()
            if value and value.casefold() not in {existing.casefold() for existing in values}:
                values.append(value)
    return tuple(values)


def _normalize_isbn(value: str) -> str | None:
    normalized = re.sub(r"[\s-]", "", value).upper()
    if len(normalized) == 10 and re.fullmatch(r"\d{9}[\dX]", normalized):
        total = sum((10 - index) * int(digit) for index, digit in enumerate(normalized[:9]))
        check = 10 if normalized[-1] == "X" else int(normalized[-1])
        if (total + check) % 11 != 0:
            return None
        isbn13_prefix = "978" + normalized[:9]
        checksum = sum(
            int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(isbn13_prefix)
        )
        return isbn13_prefix + str((-checksum) % 10)
    if len(normalized) == 13 and normalized.startswith(("978", "979")) and normalized.isdigit():
        checksum = sum(
            int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(normalized[:12])
        )
        if (-checksum) % 10 == int(normalized[-1]):
            return normalized
    return None


def _sidecar_isbns(sidecar: Mapping[str, object]) -> tuple[str, ...]:
    values: list[str] = []
    for item in _identifier_tokens(sidecar):
        match = _ISBN_LABEL.fullmatch(item)
        if match is None:
            continue
        normalized = _normalize_isbn(match.group(1))
        if normalized is not None and normalized not in values:
            values.append(normalized)
    return tuple(values)


def _book_isbns(book: CalibreBook) -> tuple[str, ...]:
    values: list[str] = []
    for key, value in book.identifiers.items():
        if key.casefold() not in {"isbn", "isbn-10", "isbn-13"}:
            continue
        normalized = _normalize_isbn(value)
        if normalized is not None and normalized not in values:
            values.append(normalized)
    return tuple(values)


def match_sidecar(
    sidecar_key: str,
    sidecar: dict[str, object],
    catalog: tuple[CalibreBook, ...],
    manual_map: dict[str, int] | None = None,
) -> MatchResult:
    """Match by exact Calibre UUID, unique ISBN, or explicit sidecar-key mapping."""
    if not _SIDE_CAR_KEY.fullmatch(sidecar_key):
        raise CalibreCatalogError("Sidecar key must be a SHA-256 digest")

    if manual_map is not None and sidecar_key in manual_map:
        target_id = manual_map[sidecar_key]
        if not isinstance(target_id, int) or isinstance(target_id, bool) or target_id < 1:
            return MatchResult("unmatched", None, "manual_target_invalid")
        targets = tuple(book for book in catalog if book.book_id == target_id)
        if not targets:
            return MatchResult("unmatched", None, "manual_target_missing")
        if len(targets) != 1:
            return MatchResult("ambiguous", None, "manual_target_ambiguous")
        return MatchResult("manual", targets[0], "manual_mapping")

    identifiers = _calibre_uuids(sidecar)
    if len(identifiers) > 1:
        return MatchResult("ambiguous", None, "multiple_calibre_uuids")
    if identifiers:
        target_uuid = identifiers[0].casefold()
        matches = tuple(book for book in catalog if book.uuid.casefold() == target_uuid)
        if not matches:
            return MatchResult("unmatched", None, "calibre_uuid_not_found")
        if len(matches) != 1:
            return MatchResult("ambiguous", None, "duplicate_calibre_uuid")
        return MatchResult("matched", matches[0], "exact_calibre_uuid")

    sidecar_isbns = _sidecar_isbns(sidecar)
    if not sidecar_isbns:
        return MatchResult("unmatched", None, "calibre_identifier_missing")
    target_isbns = set(sidecar_isbns)
    matches = tuple(book for book in catalog if target_isbns.intersection(_book_isbns(book)))
    if not matches:
        return MatchResult("unmatched", None, "isbn_not_found")
    if len(matches) != 1:
        return MatchResult("ambiguous", None, "duplicate_isbn")
    return MatchResult("matched", matches[0], "exact_isbn")
