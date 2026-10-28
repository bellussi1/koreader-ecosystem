from pathlib import Path

import pytest

from koreader_ecosystem.application_state import ApplicationState
from koreader_ecosystem.local_library import LocalLibrary, LocalLibraryError


def test_local_reading_record_survives_reopening_without_external_accounts(tmp_path: Path) -> None:
    database = tmp_path / "application-state.sqlite3"
    library = LocalLibrary(ApplicationState(database))
    book = library.add_book(title="A Fictional Book", author="A Writer")
    library.save_reading(
        book.book_id,
        status="reading",
        progress=42.5,
        rating=4.5,
        started_on="2026-10-01",
        finished_on=None,
        notes="A private note",
    )

    reopened = LocalLibrary(ApplicationState(database)).get_book(book.book_id)
    assert reopened.title == "A Fictional Book"
    assert reopened.author == "A Writer"
    assert reopened.status == "reading"
    assert reopened.progress == 42.5
    assert reopened.rating == 4.5
    assert reopened.started_on == "2026-10-01"
    assert reopened.notes == "A private note"


def test_invalid_reading_update_preserves_existing_record(tmp_path: Path) -> None:
    library = LocalLibrary(ApplicationState(tmp_path / "application-state.sqlite3"))
    book = library.add_book(title="A Fictional Book", author="A Writer")
    with pytest.raises(LocalLibraryError):
        library.save_reading(
            book.book_id,
            status="finished",
            progress=101,
            rating=4,
            started_on="2026-10-02",
            finished_on="2026-10-01",
            notes="invalid",
        )
    unchanged = library.get_book(book.book_id)
    assert unchanged.status == "to_read"
    assert unchanged.progress is None
    assert unchanged.notes == ""


def test_local_library_ipc_round_trip_without_external_service(tmp_path: Path) -> None:
    from koreader_ecosystem.config import AppConfig
    from koreader_ecosystem.ipc_service import build_dispatcher

    config = AppConfig(
        calibre_library=tmp_path / "missing-calibre",
        restic_repository=tmp_path / "restic",
        state_root=tmp_path / "state",
    )
    dispatch = build_dispatcher(config_loader=lambda: config)
    added = dispatch("local-library.add", {"title": "Fixture Book", "author": "Fixture Writer"})
    assert added["title"] == "Fixture Book"
    updated = dispatch(
        "local-library.save",
        {
            "book_id": added["book_id"],
            "status": "reading",
            "progress": 12,
            "rating": None,
            "started_on": None,
            "finished_on": None,
            "notes": "Private fixture",
        },
    )
    assert updated["notes"] == "Private fixture"
    assert dispatch("local-library.list", {})["books"][0]["progress"] == 12


def test_local_reading_data_is_included_in_application_backup(tmp_path: Path) -> None:
    from koreader_ecosystem.backup import _copy_application_state

    state_root = tmp_path / "private-state"
    library = LocalLibrary(ApplicationState(state_root / "application-state.sqlite3"))
    book = library.add_book(title="Fictional", author="Writer")
    library.save_reading(
        book.book_id,
        status="reading",
        progress=17,
        rating=None,
        started_on=None,
        finished_on=None,
        notes="A private fixture",
    )
    destination = tmp_path / "staged-snapshot"
    destination.mkdir()
    assert _copy_application_state(state_root, destination)
    restored = LocalLibrary(ApplicationState(destination / "application-state.sqlite3"))
    assert restored.get_book(book.book_id).notes == "A private fixture"


def test_local_library_rejects_bad_ipc_input_without_changing_saved_book(tmp_path: Path) -> None:
    from koreader_ecosystem.config import AppConfig
    from koreader_ecosystem.ipc_service import IpcOperationError, build_dispatcher

    config = AppConfig(
        calibre_library=tmp_path / "calibre",
        restic_repository=tmp_path / "restic",
        state_root=tmp_path / "state",
    )
    dispatch = build_dispatcher(config_loader=lambda: config)
    book = dispatch("local-library.add", {"title": "Fictional", "author": "Writer"})
    with pytest.raises(IpcOperationError):
        dispatch(
            "local-library.save",
            {
                "book_id": book["book_id"],
                "status": [],
                "progress": 0,
                "rating": None,
                "started_on": None,
                "finished_on": None,
                "notes": "bad",
            },
        )
    assert dispatch("local-library.list", {})["books"][0]["notes"] == ""
    with pytest.raises(IpcOperationError):
        dispatch("local-library.list", {"offset": True})


def test_local_library_pagination_returns_every_book_once(tmp_path: Path) -> None:
    library = LocalLibrary(ApplicationState(tmp_path / "application-state.sqlite3"))
    for index in range(105):
        library.add_book(title=f"Fictional {index}", author="Writer")
    first = library.list_books(limit=100, offset=0)
    second = library.list_books(limit=100, offset=100)
    assert len(first) == 100
    assert len(second) == 5
    assert len({book.book_id for book in first + second}) == 105


def test_list_omits_private_notes_and_get_returns_full_record(tmp_path: Path) -> None:
    library = LocalLibrary(ApplicationState(tmp_path / "application-state.sqlite3"))
    book = library.add_book(title="Fixture", author="Writer")
    library.save_reading(
        book.book_id,
        status="reading",
        progress=5,
        rating=None,
        started_on=None,
        finished_on=None,
        notes="Private fixture",
    )
    assert library.list_books()[0].notes == ""
    assert library.get_book(book.book_id).notes == "Private fixture"


def test_note_serialized_size_is_bounded_for_transport(tmp_path: Path) -> None:
    library = LocalLibrary(ApplicationState(tmp_path / "application-state.sqlite3"))
    book = library.add_book(title="Fixture", author="Writer")
    with pytest.raises(LocalLibraryError):
        library.save_reading(
            book.book_id,
            status="reading",
            progress=None,
            rating=None,
            started_on=None,
            finished_on=None,
            notes="😀" * 90000,
        )
    assert library.get_book(book.book_id).notes == ""


def test_maximum_summary_page_fits_backend_protocol_limit(tmp_path: Path) -> None:
    import json
    from dataclasses import asdict

    library = LocalLibrary(ApplicationState(tmp_path / "application-state.sqlite3"))
    for _ in range(100):
        library.add_book(title="😀" * 200, author="😀" * 200)
    response = {"books": [asdict(book) for book in library.list_books()]}
    assert len(json.dumps(response, ensure_ascii=True).encode("utf-8")) < 1024 * 1024
