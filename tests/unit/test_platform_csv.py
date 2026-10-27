from __future__ import annotations

import csv
from dataclasses import asdict

import pytest

from koreader_ecosystem.platform_csv import (
    GOODREADS_HEADERS,
    ReadingCsvError,
    csv_reading_record_from_mapping,
    export_goodreads_csv,
    inspect_reading_csv,
    parse_reading_csv,
)


def test_parses_mapped_goodreads_fields_and_discards_unmapped_values() -> None:
    content = (
        "\ufeffBook Id,Title,Author,Author l-f,ISBN,ISBN13,My Rating,Date Read,Date Added,"
        "Exclusive Shelf,Bookshelves,Read Count,My Review,Personal Field\r\n"
        '42,"O Hobbit","J. R. R. Tolkien","Tolkien, J. R. R.","=0345339681",'
        '"9780345339683",4.5,2026/10/02,2026/09/01,read,"fantasia, releitura",2,'
        '"Nota, com vírgula\r\ne segunda linha",custom-value\r\n'
    ).encode("utf-8")

    preview = parse_reading_csv(content, source="goodreads")

    assert preview.source == "goodreads"
    assert len(preview.source_digest) == 64
    assert len(preview.records) == 1
    record = preview.records[0]
    assert record.row_number == 2
    assert record.external_id == "42"
    assert record.title == "O Hobbit"
    assert record.authors == ("J. R. R. Tolkien",)
    assert record.isbn_10 == "0345339681"
    assert record.isbn_13 == "9780345339683"
    assert record.rating == 4.5
    assert record.reading_status == "read"
    assert record.exclusive_shelf == "read"
    assert record.shelves == ("fantasia", "releitura")
    assert record.read_count == 2
    assert record.date_added == "2026-09-01"
    assert record.date_read == "2026-10-02"
    assert record.review is None
    assert record.source_fields["My Review"] == ""
    assert record.source_fields["Personal Field"] == ""
    assert "custom-value" not in repr(asdict(record))
    assert preview.issues == ()


def test_inspects_alternate_portuguese_headers_and_suggests_only_header_names() -> None:
    content = (
        "ID,Título,Autores,ISBN-13,Nota,Estado,Tags,Adicionado em,Data lida\r\n"
        '42,"Livro privado","Autora Exemplo",9780345339683,4.5,read,"fantasia",'
        "2026-09-01,2026-10-02\r\n"
    ).encode()

    draft = inspect_reading_csv(content, source="storygraph")

    assert draft.headers == (
        "ID",
        "Título",
        "Autores",
        "ISBN-13",
        "Nota",
        "Estado",
        "Tags",
        "Adicionado em",
        "Data lida",
    )
    assert draft.suggested_mapping == {
        "external_id": "ID",
        "title": "Título",
        "author": "Autores",
        "isbn_10": None,
        "isbn_13": "ISBN-13",
        "rating": "Nota",
        "status": "Estado",
        "shelves": "Tags",
        "read_count": None,
        "date_added": "Adicionado em",
        "date_read": "Data lida",
        "review": None,
    }
    assert len(draft.source_digest) == 64
    assert "Livro privado" not in repr(draft)


@pytest.mark.parametrize(
    "header_row",
    [
        ",".join(f"field-{index}" for index in range(257)),
        f"{'x' * 257},Title,Author",
    ],
)
def test_rejects_csv_headers_that_cannot_be_safely_shown_in_column_mapping(header_row: str) -> None:
    content = f"{header_row}\n".encode()

    with pytest.raises(ReadingCsvError):
        inspect_reading_csv(content, source="goodreads")


def test_parses_alternate_headers_only_after_explicit_column_mapping() -> None:
    content = (
        "Título,Autores,ISBN-13,Nota,Estado,Tags,Adicionado em,Data lida\r\n"
        '"O Hobbit","J. R. R. Tolkien",9780345339683,4.5,read,"fantasia, releitura",'
        "2026-09-01,2026-10-02\r\n"
    ).encode()
    mapping = {
        "external_id": None,
        "title": "Título",
        "author": "Autores",
        "isbn_10": None,
        "isbn_13": "ISBN-13",
        "rating": "Nota",
        "status": "Estado",
        "shelves": "Tags",
        "read_count": None,
        "date_added": "Adicionado em",
        "date_read": "Data lida",
        "review": None,
    }

    preview = parse_reading_csv(content, source="goodreads", column_mapping=mapping)

    assert len(preview.records) == 1
    assert preview.records[0].title == "O Hobbit"
    assert preview.records[0].authors == ("J. R. R. Tolkien",)
    assert preview.records[0].isbn_13 == "9780345339683"
    assert preview.records[0].rating == 4.5
    assert preview.records[0].reading_status == "read"
    assert preview.records[0].shelves == ("fantasia", "releitura")
    assert preview.records[0].date_added == "2026-09-01"
    assert preview.records[0].date_read == "2026-10-02"
    assert preview.records[0].source_fields == {}


@pytest.mark.parametrize(
    "mapping",
    [
        {"title": "Título", "author": "Autores"},
        {
            "external_id": None,
            "title": "Título",
            "author": "Autores",
            "isbn_10": None,
            "isbn_13": None,
            "rating": "Nota",
            "status": "Nota",
            "shelves": None,
            "read_count": None,
            "date_added": None,
            "date_read": None,
            "review": None,
        },
        {
            "external_id": None,
            "title": "Título ausente",
            "author": "Autores",
            "isbn_10": None,
            "isbn_13": None,
            "rating": None,
            "status": None,
            "shelves": None,
            "read_count": None,
            "date_added": None,
            "date_read": None,
            "review": None,
        },
    ],
)
def test_rejects_incomplete_or_ambiguous_column_mapping(mapping: dict[str, str | None]) -> None:
    with pytest.raises(ReadingCsvError) as error:
        parse_reading_csv(
            "Título,Autores,Nota\r\nLivro,Autora,5\r\n".encode(),
            source="goodreads",
            column_mapping=mapping,
        )

    assert error.value.reason_code == "invalid_column_mapping"


def test_preserves_custom_shelf_and_warns_without_inventing_a_status() -> None:
    content = b"Title,Author,Exclusive Shelf\r\nReading Challenge,Enzo,Paused\r\n"

    preview = parse_reading_csv(content, source="maratona")

    assert preview.records[0].exclusive_shelf == "Paused"
    assert preview.records[0].reading_status is None
    assert preview.records[0].warnings == ("unmapped_status",)


def test_reports_invalid_optional_values_without_dropping_the_row() -> None:
    content = (
        b"Title,Author,My Rating,Date Read,Date Added,Read Count\r\n"
        b"Book,Author,4.25,not-a-date,2026/02/30,-1\r\n"
    )

    preview = parse_reading_csv(content, source="storygraph")

    assert len(preview.records) == 1
    assert preview.records[0].rating is None
    assert preview.records[0].date_read is None
    assert preview.records[0].date_added is None
    assert preview.records[0].read_count is None
    assert preview.records[0].warnings == (
        "invalid_rating",
        "invalid_date",
        "invalid_read_count",
    )


def test_zero_rating_and_empty_optional_fields_mean_no_value() -> None:
    content = b"Title,Author,My Rating,Date Read,Bookshelves\r\nBook,Author,0,,\r\n"

    record = parse_reading_csv(content, source="goodreads").records[0]

    assert record.rating is None
    assert record.date_read is None
    assert record.shelves == ()


def test_rejects_duplicate_headers_instead_of_overwriting_a_field() -> None:
    content = b"Title,Author,My Rating,my rating\r\nBook,Author,3,5\r\n"

    with pytest.raises(ReadingCsvError) as error:
        parse_reading_csv(content, source="goodreads")

    assert error.value.reason_code == "duplicate_headers"


def test_normalizes_goodreads_isbn_formula_and_rejects_invalid_checksum() -> None:
    content = b'Title,Author,ISBN,ISBN13\r\nBook,Author,="0345339681",9780345339684\r\n'

    record = parse_reading_csv(content, source="goodreads").records[0]

    assert record.isbn_10 == "0345339681"
    assert record.isbn_13 is None
    assert record.warnings == ("invalid_isbn13",)


def test_rejects_csv_field_larger_than_configured_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from koreader_ecosystem import platform_csv

    monkeypatch.setattr(platform_csv, "MAX_CSV_FIELD_CHARS", 8)

    with pytest.raises(ReadingCsvError) as error:
        parse_reading_csv(
            b"Title,Author,My Review\r\nBook,Author,This review is too long\r\n",
            source="goodreads",
        )

    assert error.value.reason_code == "field_too_large"


@pytest.mark.parametrize(
    ("content", "source", "reason_code"),
    [
        (b"", "goodreads", "empty_file"),
        (b"Title,Rating\r\nBook,4\r\n", "goodreads", "missing_required_columns"),
        (b'Title,Author\r\n"unfinished,Author\r\n', "goodreads", "malformed_csv"),
        (b"Title,Author\r\nBook,Author\r\n", "unknown", "unsupported_source"),
        (b"Title,Author\r\n\xff,Author\r\n", "goodreads", "invalid_encoding"),
    ],
)
def test_rejects_structurally_invalid_csv(content: bytes, source: str, reason_code: str) -> None:
    with pytest.raises(ReadingCsvError) as error:
        parse_reading_csv(content, source=source)

    assert error.value.reason_code == reason_code


def test_skips_rows_without_required_values_and_reports_only_safe_issue_codes() -> None:
    content = b"Title,Author\r\nGood Book,Author\r\n,Unknown Author\r\n"

    preview = parse_reading_csv(content, source="goodreads")

    assert len(preview.records) == 1
    assert [(issue.row_number, issue.reason_code) for issue in preview.issues] == [
        (3, "missing_title_or_author")
    ]


def test_rejects_csv_larger_than_configured_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from koreader_ecosystem import platform_csv

    monkeypatch.setattr(platform_csv, "MAX_CSV_BYTES", 5)

    with pytest.raises(ReadingCsvError) as error:
        parse_reading_csv(b"Title,Author\r\n", source="goodreads")

    assert error.value.reason_code == "file_too_large"


def test_rejects_more_than_configured_row_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from koreader_ecosystem import platform_csv

    monkeypatch.setattr(platform_csv, "MAX_CSV_ROWS", 1)

    with pytest.raises(ReadingCsvError) as error:
        parse_reading_csv(b"Title,Author\r\nOne,Author\r\nTwo,Author\r\n", source="goodreads")

    assert error.value.reason_code == "too_many_rows"


def test_exports_deterministic_goodreads_csv_with_reimportable_standard_fields() -> None:
    record = parse_reading_csv(
        b"Book Id,Title,Author,ISBN,ISBN13,My Rating,Exclusive Shelf,Bookshelves,Read Count,"
        b"Date Added,Date Read,Author l-f,Publisher,Personal Field\r\n"
        b'42,"The Hobbit","J. R. R. Tolkien",0345339681,9780345339683,4.5,read,'
        b'"fantasy, reread",2,2026/09/01,2026/10/02,"Tolkien, J. R. R.",Harper,custom\r\n',
        source="goodreads",
    ).records[0]

    exported = export_goodreads_csv((record,))
    text = exported.content.decode("utf-8-sig")
    rows = list(csv.reader(text.splitlines()))
    parsed = parse_reading_csv(exported.content, source="goodreads").records[0]

    assert exported.content.startswith(b"\xef\xbb\xbf")
    assert rows[0] == list(GOODREADS_HEADERS)
    assert rows[1][0:3] == ["42", "The Hobbit", "J. R. R. Tolkien"]
    assert rows[1][5:9] == ["0345339681", "9780345339683", "4.5", ""]
    assert rows[1][14:20] == ["2026/10/02", "2026/09/01", "fantasy, reread", "", "read", ""]
    assert rows[1][22] == "2"
    assert exported.rows_exported == 1
    assert exported.reviews_included is False
    assert exported.unexported_fields == ("Author l-f", "Personal Field", "Publisher")
    assert parsed.title == record.title
    assert parsed.isbn_10 == record.isbn_10
    assert parsed.date_read == record.date_read


def test_export_omits_review_by_default_and_includes_it_only_by_explicit_choice() -> None:
    mapping = {
        "external_id": None,
        "title": "Title",
        "author": "Author",
        "isbn_10": None,
        "isbn_13": None,
        "rating": None,
        "status": "Exclusive Shelf",
        "shelves": None,
        "read_count": None,
        "date_added": None,
        "date_read": None,
        "review": "My Review",
    }
    record = parse_reading_csv(
        b"Title,Author,My Review,Exclusive Shelf\r\n"
        b'"A Book","An Author","=HYPERLINK(""https://example.invalid"")",read\r\n',
        source="goodreads",
        column_mapping=mapping,
    ).records[0]

    private_export = export_goodreads_csv((record,))
    selected_export = export_goodreads_csv((record,), include_reviews=True)
    private_row = list(csv.reader(private_export.content.decode("utf-8-sig").splitlines()))[1]
    selected_row = list(csv.reader(selected_export.content.decode("utf-8-sig").splitlines()))[1]

    assert private_row[19] == ""
    assert private_export.unexported_fields == ("My Review",)
    assert selected_row[19] == '\t=HYPERLINK("https://example.invalid")'
    assert selected_export.reviews_included is True
    assert selected_export.formula_escaped_cells == 1


def test_export_prefixes_formula_like_cells_and_parser_round_trips_them_as_text() -> None:
    record = parse_reading_csv(
        b'Title,Author,Exclusive Shelf,Bookshelves\r\n" =1+1","@reader",read,"-SUM(A1:A2)"\r\n',
        source="goodreads",
    ).records[0]

    exported = export_goodreads_csv((record,))
    row = list(csv.reader(exported.content.decode("utf-8-sig").splitlines()))[1]
    reparsed = parse_reading_csv(exported.content, source="goodreads").records[0]

    assert row[1] == "\t=1+1"
    assert row[2] == "\t@reader"
    assert row[16] == "\t-SUM(A1:A2)"
    assert exported.formula_escaped_cells == 3
    assert reparsed.title == "=1+1"
    assert reparsed.authors == ("@reader",)
    assert reparsed.shelves == ("-SUM(A1:A2)",)


def test_export_preserves_unknown_shelves_but_rejects_invalid_options() -> None:
    record = parse_reading_csv(
        b"Title,Author,Exclusive Shelf\r\nA Book,An Author,paused\r\n",
        source="goodreads",
    ).records[0]

    exported = export_goodreads_csv((record,))
    row = list(csv.reader(exported.content.decode("utf-8-sig").splitlines()))[1]

    assert row[18] == "paused"
    with pytest.raises(ReadingCsvError) as bad_option:
        export_goodreads_csv((record,), include_reviews=1)  # type: ignore[arg-type]
    assert bad_option.value.reason_code == "invalid_export"


def test_export_never_includes_private_notes_and_reports_omitted_columns() -> None:
    mapping = {
        "external_id": None,
        "title": "Title",
        "author": "Author",
        "isbn_10": None,
        "isbn_13": None,
        "rating": None,
        "status": None,
        "shelves": None,
        "read_count": None,
        "date_added": None,
        "date_read": None,
        "review": "My Review",
    }
    record = parse_reading_csv(
        b"Title,Author,My Review,Spoiler,Private Notes\r\n"
        b'"A Book","An Author","A personal review","true","local-only note"\r\n',
        source="goodreads",
        column_mapping=mapping,
    ).records[0]

    default_export = export_goodreads_csv((record,))
    reviewed_export = export_goodreads_csv((record,), include_reviews=True)
    default_row = list(csv.reader(default_export.content.decode("utf-8-sig").splitlines()))[1]
    reviewed_row = list(csv.reader(reviewed_export.content.decode("utf-8-sig").splitlines()))[1]

    assert "local-only note" not in default_export.content.decode("utf-8-sig")
    assert "local-only note" not in reviewed_export.content.decode("utf-8-sig")
    assert default_row[19:22] == ["", "", ""]
    assert reviewed_row[19:22] == ["A personal review", "", ""]
    assert default_export.unexported_fields == ("My Review", "Private Notes", "Spoiler")


def test_export_observes_file_and_field_size_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    from koreader_ecosystem import platform_csv

    record = parse_reading_csv(b"Title,Author\r\nA Book,An Author\r\n", source="goodreads").records[
        0
    ]
    monkeypatch.setattr(platform_csv, "MAX_CSV_BYTES", 64)
    with pytest.raises(ReadingCsvError) as file_limit:
        export_goodreads_csv((record,))
    assert file_limit.value.reason_code == "file_too_large"

    monkeypatch.setattr(platform_csv, "MAX_CSV_BYTES", 50 * 1024 * 1024)
    monkeypatch.setattr(platform_csv, "MAX_CSV_FIELD_CHARS", 4)
    with pytest.raises(ReadingCsvError) as field_limit:
        export_goodreads_csv((record,))
    assert field_limit.value.reason_code == "field_too_large"


def test_restores_typed_records_from_private_sqlite_shape() -> None:
    record = parse_reading_csv(
        b"Title,Author,My Rating,Bookshelves,My Review\r\n"
        b'"Stored Book","Stored Author",4.5,"fiction, reread","Stored review"\r\n',
        source="goodreads",
    ).records[0]

    restored = csv_reading_record_from_mapping(asdict(record))

    assert restored == record
    invalid = {**asdict(record), "rating": 4.25}
    with pytest.raises(ReadingCsvError) as error:
        csv_reading_record_from_mapping(invalid)
    assert error.value.reason_code == "invalid_stored_record"
