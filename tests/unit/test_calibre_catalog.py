from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from koreader_ecosystem.calibre_catalog import (
    CalibreBook,
    CalibreCatalogError,
    CalibreCustomColumn,
    load_calibre_catalog,
    match_sidecar,
)

UUID_ONE = "0f8fad5b-d9cb-469f-a165-70867728950e"
UUID_TWO = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
SIDE_CAR_KEY = "a" * 64


def _book(
    book_id: int,
    uuid: str,
    *,
    title: str = "Livro",
    identifiers: dict[str, str] | None = None,
    custom_values: dict[str, object] | None = None,
) -> CalibreBook:
    return CalibreBook(
        book_id=book_id,
        uuid=uuid,
        title=title,
        authors=("Autora",),
        identifiers=identifiers or {},
        custom_values=custom_values or {},
    )


def _runner(
    books: list[dict[str, object]],
    *,
    custom_columns: str = "",
    list_stdout: str | None = None,
) -> tuple[object, list[list[str]]]:
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if "custom_columns" in args:
            return subprocess.CompletedProcess(args, 0, custom_columns, "")
        stdout = list_stdout if list_stdout is not None else json.dumps(books)
        return subprocess.CompletedProcess(args, 0, stdout, "")

    return run, calls


def test_loads_machine_readable_catalog_and_existing_custom_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "calibre"
    library.mkdir()
    (library / "metadata.db").touch()
    detail = """koreader_progress

{'datatype': 'float', 'display': {}, 'editable': True, 'is_multiple': False,
 'label': 'koreader_progress', 'name': 'KOReader Progress', 'normalized': False,
 'num': 1}
"""
    books = [
        {
            "id": 7,
            "uuid": UUID_ONE,
            "title": "Nome de teste",
            "authors": "Autora & Coautor",
            "identifiers": {"isbn": "9780000000000"},
            "*koreader_progress": 0.45,
        }
    ]
    runner, calls = _runner(books, custom_columns=detail)
    monkeypatch.setattr("koreader_ecosystem.calibre_catalog.shutil.which", lambda _: "calibredb")

    catalog = load_calibre_catalog(library, runner=runner)

    assert catalog == (
        CalibreBook(
            book_id=7,
            uuid=UUID_ONE,
            title="Nome de teste",
            authors=("Autora", "Coautor"),
            identifiers={"isbn": "9780000000000"},
            custom_values={"koreader_progress": 0.45},
            custom_column_definitions=(
                CalibreCustomColumn(
                    label="koreader_progress",
                    name="KOReader Progress",
                    datatype="float",
                    editable=True,
                    is_multiple=False,
                ),
            ),
        ),
    )
    assert calls[0][calls[0].index("custom_columns") + 1] == "--details"
    assert "--for-machine" not in calls[0]
    assert "*koreader_progress" in calls[1][calls[1].index("--fields") + 1]
    assert calls[1][calls[1].index("--for-machine") + 1] == "--fields"


def test_normalizes_calibre_format_file_paths_to_extensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "calibre"
    library.mkdir()
    (library / "metadata.db").touch()
    books = [
        {
            "id": 7,
            "uuid": UUID_ONE,
            "title": "Nome de teste",
            "authors": ["Autora"],
            "identifiers": {},
            "formats": [
                r"C:\Books\Autora\Nome de teste (7)\Nome de teste.EPUB",
                "/books/autora/nome-de-teste.pdf",
            ],
            "size": 2048,
        }
    ]
    runner, _calls = _runner(books)
    monkeypatch.setattr("koreader_ecosystem.calibre_catalog.shutil.which", lambda _: "calibredb")

    catalog = load_calibre_catalog(library, runner=runner)

    assert catalog[0].formats == ("EPUB", "PDF")


def test_matches_only_an_exact_koreader_calibre_uuid() -> None:
    catalog = (
        _book(7, UUID_ONE, title="Título repetido"),
        _book(8, UUID_TWO, title="Título repetido"),
    )
    sidecar = {"stats": {"identifiers": f"isbn:9780000000000 calibre:{UUID_TWO}"}}

    result = match_sidecar(SIDE_CAR_KEY, sidecar, catalog)

    assert result.status == "matched"
    assert result.book == catalog[1]


def test_matches_unique_isbn_from_koreader_doc_props() -> None:
    catalog = (
        _book(7, UUID_ONE, identifiers={"isbn": "9780306406157"}),
        _book(8, UUID_TWO, identifiers={"isbn": "9781861972712"}),
    )
    sidecar = {
        "doc_props": {
            "identifiers": "uuid:edition-id\\\n ISBN:978-0-306-40615-7",
        }
    }

    result = match_sidecar(SIDE_CAR_KEY, sidecar, catalog)

    assert result.status == "matched"
    assert result.reason_code == "exact_isbn"
    assert result.book == catalog[0]


def test_isbn10_matches_equivalent_calibre_isbn13() -> None:
    catalog = (_book(7, UUID_ONE, identifiers={"isbn": "9780306406157"}),)

    result = match_sidecar(
        SIDE_CAR_KEY,
        {"doc_props": {"identifiers": "ISBN-10:0-306-40615-2"}},
        catalog,
    )

    assert result.status == "matched"
    assert result.reason_code == "exact_isbn"
    assert result.book == catalog[0]


def test_duplicate_isbn_matches_are_ambiguous() -> None:
    catalog = (
        _book(7, UUID_ONE, identifiers={"isbn": "9780306406157"}),
        _book(8, UUID_TWO, identifiers={"isbn": "978-0-306-40615-7"}),
    )

    result = match_sidecar(
        SIDE_CAR_KEY,
        {"doc_props": {"identifiers": "ISBN:9780306406157"}},
        catalog,
    )

    assert result.status == "ambiguous"
    assert result.reason_code == "duplicate_isbn"
    assert result.book is None


def test_invalid_isbn_does_not_match() -> None:
    catalog = (_book(7, UUID_ONE, identifiers={"isbn": "9780306406157"}),)

    result = match_sidecar(
        SIDE_CAR_KEY,
        {"doc_props": {"identifiers": "ISBN:9780000000000"}},
        catalog,
    )

    assert result.status == "unmatched"
    assert result.book is None


def test_explicit_unknown_calibre_uuid_does_not_fall_back_to_isbn() -> None:
    catalog = (_book(7, UUID_ONE, identifiers={"isbn": "9780306406157"}),)

    result = match_sidecar(
        SIDE_CAR_KEY,
        {
            "stats": {"identifiers": "calibre:unknown-uuid"},
            "doc_props": {"identifiers": "ISBN:9780306406157"},
        },
        catalog,
    )

    assert result.status == "unmatched"
    assert result.reason_code == "calibre_uuid_not_found"
    assert result.book is None


def test_title_similarity_never_creates_a_match() -> None:
    catalog = (_book(7, UUID_ONE, title="Título igual"),)

    result = match_sidecar(SIDE_CAR_KEY, {"title": "Título igual"}, catalog)

    assert result.status == "unmatched"
    assert result.book is None


def test_duplicate_calibre_uuids_are_ambiguous() -> None:
    catalog = (_book(7, UUID_ONE), _book(8, UUID_ONE))

    result = match_sidecar(SIDE_CAR_KEY, {"stats": {"identifiers": f"calibre:{UUID_ONE}"}}, catalog)

    assert result.status == "ambiguous"
    assert result.book is None


def test_manual_mapping_overrides_identifier_and_reports_missing_target() -> None:
    catalog = (_book(7, UUID_ONE), _book(8, UUID_TWO))
    sidecar = {"stats": {"identifiers": f"calibre:{UUID_ONE}"}}

    manual = match_sidecar(SIDE_CAR_KEY, sidecar, catalog, {SIDE_CAR_KEY: 8})
    missing = match_sidecar(SIDE_CAR_KEY, sidecar, catalog, {SIDE_CAR_KEY: 99})

    assert manual.status == "manual"
    assert manual.book == catalog[1]
    assert missing.status == "unmatched"
    assert missing.reason_code == "manual_target_missing"


@pytest.mark.parametrize(
    "custom_columns",
    [
        "#bad\n\n{'label': 'different', 'datatype': 'text', 'num': 1}\n",
        "#bad\n\n{'label': 'bad', 'datatype': 'unknown', 'num': 1}\n",
        "#bad\n\n__import__('os').system('whoami')\n",
    ],
)
def test_rejects_invalid_or_executable_custom_column_descriptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, custom_columns: str
) -> None:
    library = tmp_path / "calibre"
    library.mkdir()
    (library / "metadata.db").touch()
    runner, _ = _runner([], custom_columns=custom_columns)
    monkeypatch.setattr("koreader_ecosystem.calibre_catalog.shutil.which", lambda _: "calibredb")

    with pytest.raises(CalibreCatalogError):
        load_calibre_catalog(library, runner=runner)


def test_rejects_invalid_machine_json_without_exposing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "calibre"
    library.mkdir()
    (library / "metadata.db").touch()
    runner, _ = _runner([], list_stdout="private malformed output")
    monkeypatch.setattr("koreader_ecosystem.calibre_catalog.shutil.which", lambda _: "calibredb")

    with pytest.raises(CalibreCatalogError) as error:
        load_calibre_catalog(library, runner=runner)

    assert "private malformed output" not in str(error.value)
