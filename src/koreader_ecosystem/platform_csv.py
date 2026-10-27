from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast

MAX_CSV_BYTES = 50 * 1024 * 1024
MAX_CSV_ROWS = 50_000
MAX_CSV_FIELD_CHARS = 1_000_000
MAX_CSV_COLUMNS = 256
MAX_CSV_HEADER_CHARS = 256
_SUPPORTED_SOURCES = frozenset({"goodreads", "storygraph", "maratona"})
_STATUS_MAP = {
    "to-read": "to_read",
    "currently-reading": "currently_reading",
    "read": "read",
    "did-not-finish": "did_not_finish",
}
_STATUS_EXPORT_MAP = {value: key for key, value in _STATUS_MAP.items()}
GOODREADS_HEADERS = (
    "Book Id",
    "Title",
    "Author",
    "Author l-f",
    "Additional Authors",
    "ISBN",
    "ISBN13",
    "My Rating",
    "Average Rating",
    "Publisher",
    "Binding",
    "Number of Pages",
    "Year Published",
    "Original Publication Year",
    "Date Read",
    "Date Added",
    "Bookshelves",
    "Bookshelves with positions",
    "Exclusive Shelf",
    "My Review",
    "Spoiler",
    "Private Notes",
    "Read Count",
    "Owned Copies",
)
CSV_MAPPING_FIELDS = (
    "external_id",
    "title",
    "author",
    "isbn_10",
    "isbn_13",
    "rating",
    "status",
    "shelves",
    "read_count",
    "date_added",
    "date_read",
    "review",
)
_COLUMN_ALIASES = {
    "external_id": ("book id", "bookid", "id", "goodreads id", "storygraph id", "id do livro"),
    "title": ("title", "book title", "name", "titulo", "título", "nome", "nome do livro"),
    "author": ("author", "authors", "author(s)", "writer", "autor", "autores"),
    "isbn_10": ("isbn", "isbn 10", "isbn-10", "isbn10"),
    "isbn_13": ("isbn13", "isbn 13", "isbn-13"),
    "rating": (
        "my rating",
        "rating",
        "my rating (1-5)",
        "nota",
        "minha nota",
        "minha avaliacao",
        "minha avaliação",
    ),
    "status": (
        "exclusive shelf",
        "reading status",
        "status",
        "status de leitura",
        "estado",
        "situacao",
        "situação",
    ),
    "shelves": ("bookshelves", "shelves", "tags", "categories", "categorias", "prateleiras"),
    "read_count": (
        "read count",
        "times read",
        "number of reads",
        "contagem de leituras",
        "vezes lido",
    ),
    "date_added": (
        "date added",
        "added on",
        "added date",
        "adicionado em",
        "data de adicao",
        "data de adição",
    ),
    "date_read": (
        "date read",
        "date finished",
        "finished date",
        "finished at",
        "data lida",
        "data de leitura",
        "data de conclusao",
        "data de conclusão",
    ),
    "review": ("my review", "review", "resenha", "avaliação escrita", "avaliacao escrita"),
}


class ReadingCsvError(ValueError):
    """A safe, user-displayable reason why a reading CSV cannot be previewed."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class CsvRowIssue:
    row_number: int
    reason_code: str


@dataclass(frozen=True, slots=True)
class CsvReadingRecord:
    row_number: int
    external_id: str | None
    title: str
    authors: tuple[str, ...]
    isbn_10: str | None
    isbn_13: str | None
    rating: float | None
    exclusive_shelf: str | None
    reading_status: str | None
    shelves: tuple[str, ...]
    read_count: int | None
    date_added: str | None
    date_read: str | None
    review: str | None
    source_fields: dict[str, str]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReadingCsvPreview:
    source: str
    source_digest: str
    mapping_digest: str
    records: tuple[CsvReadingRecord, ...]
    issues: tuple[CsvRowIssue, ...]


@dataclass(frozen=True, slots=True)
class CsvColumnMappingDraft:
    source: str
    source_digest: str
    headers: tuple[str, ...]
    suggested_mapping: dict[str, str | None]


@dataclass(frozen=True, slots=True)
class GoodreadsCsvExport:
    content: bytes
    rows_exported: int
    formula_escaped_cells: int
    reviews_included: bool
    unexported_fields: tuple[str, ...]
    warnings: tuple[str, ...]


def inspect_reading_csv(content: bytes, *, source: str) -> CsvColumnMappingDraft:
    """Read column names and safe mapping suggestions without returning row values."""
    text = _decode_csv_content(content, source=source)
    previous_field_limit = csv.field_size_limit()
    csv.field_size_limit(MAX_CSV_FIELD_CHARS)
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        headers = _validate_headers(reader.fieldnames)
        return CsvColumnMappingDraft(
            source=source,
            source_digest=hashlib.sha256(content).hexdigest(),
            headers=headers,
            suggested_mapping=_suggest_column_mapping(headers),
        )
    except csv.Error as error:
        reason_code = (
            "field_too_large" if "field larger than field limit" in str(error) else "malformed_csv"
        )
        raise ReadingCsvError(reason_code) from None
    finally:
        csv.field_size_limit(previous_field_limit)


def parse_reading_csv(
    content: bytes,
    *,
    source: str,
    column_mapping: Mapping[str, str | None] | None = None,
) -> ReadingCsvPreview:
    """Parse a Goodreads-compatible CSV without persisting or changing its source."""
    text = _decode_csv_content(content, source=source)

    previous_field_limit = csv.field_size_limit()
    csv.field_size_limit(MAX_CSV_FIELD_CHARS)
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        headers = _validate_headers(reader.fieldnames)
        mapping = (
            _suggest_column_mapping(headers)
            if column_mapping is None
            else _validate_column_mapping(headers, column_mapping)
        )
        if column_mapping is None and (mapping["title"] is None or mapping["author"] is None):
            raise ReadingCsvError("missing_required_columns")
        header_map: dict[str, str] = {}
        for field, logical_name in (
            ("title", "title"),
            ("author", "author"),
            ("external_id", "book id"),
            ("isbn_10", "isbn"),
            ("isbn_13", "isbn13"),
            ("rating", "my rating"),
            ("status", "exclusive shelf"),
            ("shelves", "bookshelves"),
            ("read_count", "read count"),
            ("date_added", "date added"),
            ("date_read", "date read"),
            ("review", "my review"),
        ):
            header = mapping[field]
            if header is not None:
                header_map[logical_name] = header
        mapped_headers = set(header_map.values())
        records: list[CsvReadingRecord] = []
        issues: list[CsvRowIssue] = []
        for row_number, row in enumerate(reader, start=2):
            if row_number - 1 > MAX_CSV_ROWS:
                raise ReadingCsvError("too_many_rows")
            if row is None or all(value in (None, "") for value in row.values()):
                continue

            if None in row:
                issues.append(CsvRowIssue(row_number, "extra_columns"))
                continue

            fields = {header: (value or "").strip() for header, value in row.items() if header}
            title = _value(fields, header_map, "title")
            author = _value(fields, header_map, "author")
            if not title or not author:
                issues.append(CsvRowIssue(row_number, "missing_title_or_author"))
                continue

            warnings: list[str] = []
            source_shelf = _value(fields, header_map, "exclusive shelf") or None
            reading_status = _normalize_status(source_shelf)
            if source_shelf and reading_status is None:
                warnings.append("unmapped_status")

            rating, rating_warning = _parse_rating(_value(fields, header_map, "my rating"))
            if rating_warning:
                warnings.append(rating_warning)

            date_added, date_added_warning = _parse_date(_value(fields, header_map, "date added"))
            if date_added_warning:
                warnings.append(date_added_warning)
            date_read, date_read_warning = _parse_date(_value(fields, header_map, "date read"))
            if date_read_warning:
                warnings.append(date_read_warning)

            read_count, read_count_warning = _parse_read_count(
                _value(fields, header_map, "read count")
            )
            if read_count_warning:
                warnings.append(read_count_warning)

            isbn_10, isbn_10_warning = _parse_isbn(_value(fields, header_map, "isbn"), 10)
            if isbn_10_warning:
                warnings.append(isbn_10_warning)
            isbn_13, isbn_13_warning = _parse_isbn(_value(fields, header_map, "isbn13"), 13)
            if isbn_13_warning:
                warnings.append(isbn_13_warning)

            records.append(
                CsvReadingRecord(
                    row_number=row_number,
                    external_id=_optional_value(fields, header_map, "book id"),
                    title=title,
                    authors=(author,),
                    isbn_10=isbn_10,
                    isbn_13=isbn_13,
                    rating=rating,
                    exclusive_shelf=source_shelf,
                    reading_status=reading_status,
                    shelves=_split_shelves(_value(fields, header_map, "bookshelves")),
                    read_count=read_count,
                    date_added=date_added,
                    date_read=date_read,
                    review=_optional_value(fields, header_map, "my review"),
                    source_fields={
                        header: "" for header in headers if header not in mapped_headers
                    },
                    warnings=tuple(dict.fromkeys(warnings)),
                )
            )

        return ReadingCsvPreview(
            source=source,
            source_digest=hashlib.sha256(content).hexdigest(),
            mapping_digest=_mapping_digest(mapping),
            records=tuple(records),
            issues=tuple(issues),
        )
    except csv.Error as error:
        reason_code = (
            "field_too_large" if "field larger than field limit" in str(error) else "malformed_csv"
        )
        raise ReadingCsvError(reason_code) from None
    finally:
        csv.field_size_limit(previous_field_limit)


def _decode_csv_content(content: bytes, *, source: str) -> str:
    if source not in _SUPPORTED_SOURCES:
        raise ReadingCsvError("unsupported_source")
    if not isinstance(content, bytes):
        raise ReadingCsvError("invalid_content")
    if not content:
        raise ReadingCsvError("empty_file")
    if len(content) > MAX_CSV_BYTES:
        raise ReadingCsvError("file_too_large")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ReadingCsvError("invalid_encoding") from None
    if not text.strip():
        raise ReadingCsvError("empty_file")
    return text


def _validate_headers(raw_headers: Sequence[str | None] | None) -> tuple[str, ...]:
    if not raw_headers:
        raise ReadingCsvError("invalid_header")
    if len(raw_headers) > MAX_CSV_COLUMNS:
        raise ReadingCsvError("invalid_header")
    headers: list[str] = []
    for header in raw_headers:
        if not isinstance(header, str) or not header.strip() or len(header) > MAX_CSV_HEADER_CHARS:
            raise ReadingCsvError("invalid_header")
        headers.append(header)
    header_tuple = tuple(headers)
    normalized = tuple(_normalized_header(header) for header in header_tuple)
    if len(normalized) != len(set(normalized)):
        raise ReadingCsvError("duplicate_headers")
    return header_tuple


def _suggest_column_mapping(headers: tuple[str, ...]) -> dict[str, str | None]:
    header_by_name = {_normalized_header(header): header for header in headers}
    mapping: dict[str, str | None] = {}
    used: set[str] = set()
    for field in CSV_MAPPING_FIELDS:
        if field == "review":
            mapping[field] = None
            continue
        match = next(
            (
                header_by_name[_normalized_header(alias)]
                for alias in _COLUMN_ALIASES[field]
                if _normalized_header(alias) in header_by_name
                and header_by_name[_normalized_header(alias)] not in used
            ),
            None,
        )
        mapping[field] = match
        if match is not None:
            used.add(match)
    return mapping


def _validate_column_mapping(
    headers: tuple[str, ...], mapping: Mapping[str, str | None]
) -> dict[str, str | None]:
    if not isinstance(mapping, Mapping) or set(mapping) != set(CSV_MAPPING_FIELDS):
        raise ReadingCsvError("invalid_column_mapping")
    if any(value is not None and not isinstance(value, str) for value in mapping.values()):
        raise ReadingCsvError("invalid_column_mapping")
    selected = [value for value in mapping.values() if value is not None]
    if (
        mapping.get("title") not in headers
        or mapping.get("author") not in headers
        or len(selected) != len(set(selected))
        or any(value not in headers for value in selected)
    ):
        raise ReadingCsvError("invalid_column_mapping")
    return {field: mapping[field] for field in CSV_MAPPING_FIELDS}


def export_goodreads_csv(
    records: Iterable[CsvReadingRecord], *, include_reviews: bool = False
) -> GoodreadsCsvExport:
    """Serialize local records to a spreadsheet-safe Goodreads-compatible CSV."""
    if not isinstance(include_reviews, bool):
        raise ReadingCsvError("invalid_export")
    try:
        iterator = iter(records)
    except TypeError:
        raise ReadingCsvError("invalid_export") from None

    output = bytearray(b"\xef\xbb\xbf")
    header_buffer = io.StringIO(newline="")
    csv.writer(header_buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL).writerow(
        GOODREADS_HEADERS
    )
    output.extend(header_buffer.getvalue().encode("utf-8"))
    if len(output) > MAX_CSV_BYTES:
        raise ReadingCsvError("file_too_large")
    unexported: set[str] = set()
    warnings: set[str] = set()
    formula_escaped_cells = 0
    exported_count = 0

    for record in iterator:
        if exported_count >= MAX_CSV_ROWS:
            raise ReadingCsvError("too_many_rows")
        if (
            not isinstance(record, CsvReadingRecord)
            or not isinstance(record.title, str)
            or not record.title
            or not isinstance(record.authors, tuple)
            or not record.authors
            or any(not isinstance(author, str) or not author for author in record.authors)
            or not isinstance(record.source_fields, dict)
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in record.source_fields.items()
            )
        ):
            raise ReadingCsvError("invalid_export")

        source_fields = {
            _normalized_header(key): value for key, value in record.source_fields.items()
        }
        unexported.update(record.source_fields)
        if record.review and not include_reviews:
            unexported.add("My Review")
        if source_fields.get("spoiler") and not include_reviews:
            unexported.add("Spoiler")
        if source_fields.get("private notes"):
            unexported.add("Private Notes")
        warnings.update(record.warnings)

        values = {
            "Book Id": record.external_id or source_fields.get("book id", ""),
            "Title": record.title,
            "Author": record.authors[0],
            "Author l-f": source_fields.get("author l-f", ""),
            "Additional Authors": ", ".join(record.authors[1:])
            or source_fields.get("additional authors", ""),
            "ISBN": record.isbn_10 or "",
            "ISBN13": record.isbn_13 or "",
            "My Rating": _format_rating(record.rating),
            "Average Rating": source_fields.get("average rating", ""),
            "Publisher": source_fields.get("publisher", ""),
            "Binding": source_fields.get("binding", ""),
            "Number of Pages": source_fields.get("number of pages", ""),
            "Year Published": source_fields.get("year published", ""),
            "Original Publication Year": source_fields.get("original publication year", ""),
            "Date Read": _goodreads_date(record.date_read),
            "Date Added": _goodreads_date(record.date_added),
            "Bookshelves": ", ".join(record.shelves) or source_fields.get("bookshelves", ""),
            "Bookshelves with positions": source_fields.get("bookshelves with positions", ""),
            "Exclusive Shelf": record.exclusive_shelf
            or _STATUS_EXPORT_MAP.get(record.reading_status or "", ""),
            "My Review": record.review or "" if include_reviews else "",
            "Spoiler": source_fields.get("spoiler", "") if include_reviews else "",
            "Private Notes": "",
            "Read Count": str(record.read_count) if record.read_count is not None else "",
            "Owned Copies": source_fields.get("owned copies", ""),
        }
        row: list[str] = []
        for header in GOODREADS_HEADERS:
            value, escaped = _spreadsheet_safe(values[header])
            if len(value) > MAX_CSV_FIELD_CHARS:
                raise ReadingCsvError("field_too_large")
            row.append(value)
            formula_escaped_cells += int(escaped)
        row_buffer = io.StringIO(newline="")
        csv.writer(row_buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL).writerow(row)
        encoded_row = row_buffer.getvalue().encode("utf-8")
        if len(output) + len(encoded_row) > MAX_CSV_BYTES:
            raise ReadingCsvError("file_too_large")
        output.extend(encoded_row)
        exported_count += 1

    return GoodreadsCsvExport(
        content=bytes(output),
        rows_exported=exported_count,
        formula_escaped_cells=formula_escaped_cells,
        reviews_included=include_reviews,
        unexported_fields=tuple(sorted(unexported, key=str.casefold)),
        warnings=tuple(sorted(warnings)),
    )


def _format_rating(value: float | None) -> str:
    if value is None:
        return "0"
    return str(int(value)) if value.is_integer() else f"{value:.1f}"


def _goodreads_date(value: str | None) -> str:
    if value is None:
        return ""
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y/%m/%d")
    except ValueError:
        return ""


def _spreadsheet_safe(value: str) -> tuple[str, bool]:
    formula_prefixes = "=+-@\t\r\n＝＋－＠"
    trimmed = value.lstrip(" \t\r\n\x00")
    if value and (value[0] in formula_prefixes or (trimmed and trimmed[0] in formula_prefixes)):
        # OWASP recommends a leading tab for spreadsheet viewing; our parser trims it on import.
        return f"\t{value}", True
    return value, False


def _normalized_header(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _mapping_digest(mapping: Mapping[str, str | None]) -> str:
    canonical = json.dumps(
        mapping,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def csv_reading_record_from_mapping(value: object) -> CsvReadingRecord:
    """Rebuild an import row from the private SQLite JSON representation."""
    required = {
        "row_number",
        "external_id",
        "title",
        "authors",
        "isbn_10",
        "isbn_13",
        "rating",
        "exclusive_shelf",
        "reading_status",
        "shelves",
        "read_count",
        "date_added",
        "date_read",
        "review",
        "source_fields",
        "warnings",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ReadingCsvError("invalid_stored_record")

    optional_strings = (
        "external_id",
        "isbn_10",
        "isbn_13",
        "exclusive_shelf",
        "reading_status",
        "date_added",
        "date_read",
        "review",
    )
    row_number = value["row_number"]
    title = value["title"]
    authors = value["authors"]
    shelves = value["shelves"]
    warnings = value["warnings"]
    source_fields = value["source_fields"]
    rating = value["rating"]
    read_count = value["read_count"]
    if (
        not isinstance(row_number, int)
        or isinstance(row_number, bool)
        or not 2 <= row_number <= MAX_CSV_ROWS + 1
        or not isinstance(title, str)
        or not title
        or not isinstance(authors, (list, tuple))
        or not authors
        or not all(isinstance(author, str) and author for author in authors)
        or not isinstance(shelves, (list, tuple))
        or not all(isinstance(shelf, str) for shelf in shelves)
        or not isinstance(warnings, (list, tuple))
        or not all(
            isinstance(item, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item)
            for item in warnings
        )
        or not isinstance(source_fields, dict)
        or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in source_fields.items()
        )
        or any(
            value[name] is not None and not isinstance(value[name], str)
            for name in optional_strings
        )
        or (
            rating is not None
            and (
                isinstance(rating, bool)
                or not isinstance(rating, (float, int))
                or not math.isfinite(float(rating))
                or not 0 <= rating <= 5
                or float(rating) * 2 != int(float(rating) * 2)
            )
        )
        or (
            read_count is not None
            and (isinstance(read_count, bool) or not isinstance(read_count, int) or read_count < 0)
        )
    ):
        raise ReadingCsvError("invalid_stored_record")

    return CsvReadingRecord(
        row_number=row_number,
        external_id=cast(str | None, value["external_id"]),
        title=title,
        authors=tuple(authors),
        isbn_10=cast(str | None, value["isbn_10"]),
        isbn_13=cast(str | None, value["isbn_13"]),
        rating=float(rating) if rating is not None else None,
        exclusive_shelf=cast(str | None, value["exclusive_shelf"]),
        reading_status=cast(str | None, value["reading_status"]),
        shelves=tuple(shelves),
        read_count=cast(int | None, read_count),
        date_added=cast(str | None, value["date_added"]),
        date_read=cast(str | None, value["date_read"]),
        review=cast(str | None, value["review"]),
        source_fields=dict(source_fields),
        warnings=tuple(warnings),
    )


def _value(fields: dict[str, str], header_map: dict[str, str], normalized_name: str) -> str:
    original_name = header_map.get(normalized_name)
    return fields.get(original_name, "") if original_name is not None else ""


def _optional_value(
    fields: dict[str, str], header_map: dict[str, str], normalized_name: str
) -> str | None:
    value = _value(fields, header_map, normalized_name)
    return value or None


def _normalize_status(value: str | None) -> str | None:
    if value is None:
        return None
    return _STATUS_MAP.get(value.strip().casefold())


def _parse_rating(value: str) -> tuple[float | None, str | None]:
    if not value:
        return None, None
    try:
        rating = float(value)
    except ValueError:
        return None, "invalid_rating"
    if not 0 <= rating <= 5 or rating * 2 != int(rating * 2):
        return None, "invalid_rating"
    return (None if rating == 0 else rating), None


def _parse_date(value: str) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    for format_string in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, format_string).date().isoformat(), None
        except ValueError:
            continue
    return None, "invalid_date"


def _parse_read_count(value: str) -> tuple[int | None, str | None]:
    if not value:
        return None, None
    try:
        count = int(value)
    except ValueError:
        return None, "invalid_read_count"
    if count < 0:
        return None, "invalid_read_count"
    return count, None


def _parse_isbn(value: str, length: int) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    candidate = value.strip()
    if candidate.startswith('="') and candidate.endswith('"'):
        candidate = candidate[2:-1]
    elif candidate.startswith("="):
        candidate = candidate[1:].strip('"')
    normalized = candidate.replace("-", "").replace(" ", "").upper()
    if length == 10:
        if not re.fullmatch(r"[0-9]{9}[0-9X]", normalized):
            return None, "invalid_isbn10"
        checksum = sum(
            int(digit) * weight
            for digit, weight in zip(normalized[:9], range(10, 1, -1), strict=True)
        )
        check_digit = 10 if normalized[-1] == "X" else int(normalized[-1])
        if (checksum + check_digit) % 11 != 0:
            return None, "invalid_isbn10"
        return normalized, None

    if not re.fullmatch(r"[0-9]{13}", normalized):
        return None, "invalid_isbn13"
    checksum = sum(
        int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(normalized[:12])
    )
    if (10 - checksum % 10) % 10 != int(normalized[-1]):
        return None, "invalid_isbn13"
    return normalized, None


def _split_shelves(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(shelf.strip() for shelf in value.split(",") if shelf.strip()))
