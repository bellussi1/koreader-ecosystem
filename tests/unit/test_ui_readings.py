from __future__ import annotations

import os
import time
from datetime import date, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLabel, QPushButton, QWidget

from koreader_ecosystem.hardcover_models import HardcoverLibraryEntry, HardcoverRead
from koreader_ecosystem.operation_history import OperationHistory
from koreader_ecosystem.ui.operation_worker import CancellationToken, OperationController
from koreader_ecosystem.ui.readings_page import ReadingPage


class StubServices:
    def __init__(self, entries: tuple[HardcoverLibraryEntry, ...] = ()) -> None:
        self.entries = entries
        self.rating_calls: list[tuple[int, float | None]] = []
        self.note_calls: list[tuple[int, str, int]] = []
        self.list_calls: list[tuple[int, int]] = []

    def list_hardcover_books(self, *, limit: int, offset: int) -> tuple[HardcoverLibraryEntry, ...]:
        self.list_calls.append((limit, offset))
        return self.entries[offset : offset + limit]

    def update_rating(self, *, user_book_id: int, rating: float | None) -> object:
        self.rating_calls.append((user_book_id, rating))
        return object()

    def add_journal_entry(self, *, book_id: int, entry: str, privacy_setting_id: int = 3) -> int:
        self.note_calls.append((book_id, entry, privacy_setting_id))
        return 123


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _entry(
    *,
    status: int,
    rating: float | None = None,
    progress: int = 0,
    pages: int | None = 200,
    edition_format: str | None = "Kindle Edition",
    user_book_id: int = 101,
) -> HardcoverLibraryEntry:
    reads = (
        (
            HardcoverRead(
                id=90,
                started_at=date(2026, 10, 2),
                finished_at=date(2026, 10, 20) if status == 3 else None,
                progress_pages=progress,
                edition_id=303,
            ),
        )
        if status in {2, 3}
        else ()
    )
    return HardcoverLibraryEntry(
        user_book_id=user_book_id,
        book_id=202,
        title="O Nome do Vento",
        authors=("Patrick Rothfuss",),
        status_id=status,
        rating=rating,
        edition_id=303,
        edition_pages=pages,
        edition_format=edition_format,
        reads=reads,
    )


def _wait_until(app: QApplication, predicate, timeout: float = 3) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    return predicate()


def _select_shelf(page: ReadingPage, shelf: str) -> None:
    shelf_filter = page.findChild(QComboBox, "readingShelfFilter")
    assert shelf_filter is not None
    shelf_filter.setCurrentIndex(shelf_filter.findData(shelf))


def test_readings_empty_and_loading_states_are_clear(qapp: QApplication) -> None:
    services = StubServices()
    page = ReadingPage(services, OperationController())  # type: ignore[arg-type]

    assert page.status_label.text() == "Ainda não carregamos suas leituras."
    page.refresh()

    assert page.status_label.text() == "Consultando toda a biblioteca do Hardcover…"
    assert _wait_until(qapp, lambda: page.status_label.text() == "Sua lista está vazia.")


def test_reading_title_and_refresh_action_share_a_compact_header(qapp: QApplication) -> None:
    page = ReadingPage(StubServices(), OperationController())  # type: ignore[arg-type]

    heading = page.findChild(QLabel, "pageTitle")
    header = page.findChild(QWidget, "readingsHeader")

    assert heading is not None
    assert header is not None
    assert heading.parentWidget() is not None
    assert heading.parentWidget().parentWidget() is header
    assert page.refresh_button.parentWidget() is header


def test_reading_shelf_uses_a_centered_editorial_column(qapp: QApplication) -> None:
    page = ReadingPage(StubServices(), OperationController())  # type: ignore[arg-type]

    content = page.findChild(QWidget, "readingContent")

    assert content is not None
    assert content.maximumWidth() == 960


def test_reading_card_action_stays_compact_and_right_aligned(qapp: QApplication) -> None:
    page = ReadingPage(StubServices((_entry(status=2),)), OperationController())  # type: ignore[arg-type]
    page.set_entries((_entry(status=2),))
    page.resize(960, 700)
    page.show()
    qapp.processEvents()

    action = page.findChild(QPushButton, "finishRead_101")
    action_layout = action.parentWidget().layout() if action is not None else None
    action_item = action_layout.itemAt(action_layout.indexOf(action)) if action_layout else None

    assert action is not None
    assert action.maximumWidth() == 220
    assert action_item is not None
    assert action_item.alignment() & Qt.AlignmentFlag.AlignRight
    page.close()


def test_readings_distinguish_last_confirmed_query_from_latest_failure(
    qapp: QApplication, tmp_path
) -> None:
    history = OperationHistory(tmp_path / "operation-history.json")
    operations = OperationController(history=history)
    page = ReadingPage(StubServices(), operations)  # type: ignore[arg-type]
    last_success = "2026-10-24T12:30:00-03:00"
    expected_local_time = (
        datetime.fromisoformat(last_success).astimezone().strftime("%d/%m/%Y às %H:%M")
    )

    history.record(
        key="hardcover_library_refresh",
        status="success",
        updated_at=last_success,
    )
    page._update_sync_confirmation()
    assert expected_local_time in page.sync_confirmation_label.text()
    assert "última consulta" in page.sync_confirmation_label.text()

    history.record(
        key="hardcover_library_refresh",
        status="failed",
        updated_at="2026-10-24T13:00:00-03:00",
        reason_code="remote_unavailable",
    )
    page._update_sync_confirmation()
    assert "última tentativa falhou" in page.sync_confirmation_label.text()
    assert expected_local_time in page.sync_confirmation_label.text()


def test_readings_show_progress_and_start_and_finish_dates(qapp: QApplication) -> None:
    page = ReadingPage(StubServices((_entry(status=2, progress=87),)), OperationController())  # type: ignore[arg-type]

    page.set_entries((_entry(status=2, progress=87),))

    text = " ".join(label.text() for label in page.findChildren(QLabel))
    progress = page.findChild(QLabel, "progressLabel_101")

    assert "44%" in text
    assert progress is not None and "02/10/2026" in progress.text()
    assert "página 87 de 200" in progress.text()


def test_finished_unrated_books_are_queued_for_rating(qapp: QApplication) -> None:
    page = ReadingPage(
        StubServices((_entry(status=3, rating=None, progress=200),)), OperationController()
    )  # type: ignore[arg-type]

    page.set_entries((_entry(status=3, rating=None, progress=200),))
    _select_shelf(page, "completed")

    rating = page.findChild(QComboBox, "ratingCombo_101")
    save = page.findChild(QPushButton, "saveRating_101")
    assert rating is not None and save is not None
    assert rating.currentData() is None
    assert not save.isEnabled()
    assert "20/10/2026" in " ".join(label.text() for label in page.findChildren(QLabel))


def test_rating_control_requires_an_explicit_half_star_choice(
    qapp: QApplication,
) -> None:
    page = ReadingPage(StubServices((_entry(status=3, rating=None),)), OperationController())  # type: ignore[arg-type]
    page.set_entries((_entry(status=3, rating=None),))
    _select_shelf(page, "completed")

    rating = page.findChild(QComboBox, "ratingCombo_101")

    assert rating is not None
    values = [rating.itemData(index) for index in range(1, rating.count())]
    assert values == [step / 2 for step in range(1, 11)]
    assert rating.currentData() is None


def test_rating_is_saved_only_after_the_user_selects_it(qapp: QApplication) -> None:
    services = StubServices((_entry(status=3, rating=None),))
    page = ReadingPage(services, OperationController())  # type: ignore[arg-type]
    page.set_entries(services.entries)
    _select_shelf(page, "completed")

    rating = page.findChild(QComboBox, "ratingCombo_101")
    save = page.findChild(QPushButton, "saveRating_101")
    assert rating is not None and save is not None
    assert not save.isEnabled()

    rating.setCurrentIndex(rating.findData(3.5))

    assert save.isEnabled()
    save.click()
    assert _wait_until(qapp, lambda: services.rating_calls == [(101, 3.5)])


def test_reading_shelves_include_paused_and_did_not_finish(qapp: QApplication) -> None:
    page = ReadingPage(StubServices(), OperationController())  # type: ignore[arg-type]
    entries = tuple(_entry(status=status) for status in (2, 1, 3, 4, 5))
    page.set_entries(entries)

    shelves = page.findChild(QComboBox, "readingShelfFilter")

    assert shelves is not None
    assert tuple(shelves.itemText(index) for index in range(shelves.count())) == (
        "Lendo agora (1)",
        "Quero ler (1)",
        "Pausados (1)",
        "Concluídos (1)",
        "Não terminei (1)",
    )

    shelves.setCurrentIndex(shelves.findData("paused"))
    assert [entry.status_id for entry in page._visible_entries()] == [4]

    shelves.setCurrentIndex(shelves.findData("dnf"))
    assert [entry.status_id for entry in page._visible_entries()] == [5]


def test_search_filter_defaults_to_digital_editions_and_can_show_all(qapp: QApplication) -> None:
    page = ReadingPage(StubServices(), OperationController())  # type: ignore[arg-type]
    entries = (
        _entry(status=2, user_book_id=101, edition_format="Kindle Edition"),
        _entry(status=2, user_book_id=102, edition_format="Paperback"),
        _entry(status=2, user_book_id=103, edition_format=None),
    )
    page.set_entries(entries)

    digital_filter = page.findChild(QCheckBox, "digitalEditionFilter")
    assert digital_filter is not None and digital_filter.isChecked()
    assert [entry.user_book_id for entry in page._visible_entries()] == [101]
    assert "2 fora do filtro" in page.status_label.text()

    digital_filter.setChecked(False)

    assert [entry.user_book_id for entry in page._visible_entries()] == [101, 102, 103]
    assert [entry.user_book_id for entry in entries] == [101, 102, 103]


def test_reading_page_renders_only_the_selected_shelf(qapp: QApplication) -> None:
    page = ReadingPage(StubServices(), OperationController())  # type: ignore[arg-type]
    page.set_entries(
        (
            _entry(status=2, user_book_id=101),
            _entry(status=1, user_book_id=102),
            _entry(status=3, user_book_id=103),
            _entry(status=4, user_book_id=104),
            _entry(status=5, user_book_id=105),
        )
    )

    assert [entry.user_book_id for entry in page._visible_entries()] == [101]

    shelf_filter = page.findChild(QComboBox, "readingShelfFilter")
    assert shelf_filter is not None
    shelf_filter.setCurrentIndex(shelf_filter.findData("want"))

    assert [entry.user_book_id for entry in page._visible_entries()] == [102]
    assert page._book_layout.count() < 5


def test_search_ignores_accents(qapp: QApplication) -> None:
    entry = replace(_entry(status=2), title="Ação e memória")
    page = ReadingPage(StubServices((entry,)), OperationController())  # type: ignore[arg-type]
    page.set_entries((entry,))

    page.search_edit.setText("acao")

    assert _wait_until(qapp, lambda: "resultado" in page.status_label.text())
    assert page._visible_entries() == (entry,)

    page.search_edit.setText("sem resultado")

    assert _wait_until(qapp, lambda: not page._visible_entries())


@pytest.mark.parametrize(
    ("edition_format", "is_digital"),
    [
        ("Kindle Edition", True),
        ("E-book", True),
        ("eBook", True),
        ("EPUB", True),
        ("Audiobook", False),
        ("Paperback", False),
        (None, False),
    ],
)
def test_digital_format_detection_is_explicit(
    qapp: QApplication, edition_format: str | None, is_digital: bool
) -> None:
    assert ReadingPage._is_digital_format(edition_format) is is_digital


def test_all_pages_are_loaded_without_a_load_more_control(qapp: QApplication) -> None:
    entries = tuple(
        replace(_entry(status=1, user_book_id=index + 1), title=f"Livro {index + 1}")
        for index in range(201)
    )
    services = StubServices(entries)
    page = ReadingPage(services, OperationController())  # type: ignore[arg-type]

    loaded = page._load_all_books(CancellationToken())

    assert isinstance(loaded, tuple) and len(loaded) == 201
    assert services.list_calls == [(100, 0), (100, 100), (100, 200)]
    assert not any(
        isinstance(widget, QPushButton) and "Carregar mais" in widget.text()
        for widget in page.findChildren(QWidget)
    )


def test_shelf_empty_message_survives_rebuilding_the_shelves(qapp: QApplication) -> None:
    page = ReadingPage(StubServices(), OperationController())  # type: ignore[arg-type]

    page.set_entries((_entry(status=2),))
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    populated_empty_message = page.findChild(QLabel, "empty_reading")
    assert populated_empty_message is not None
    assert populated_empty_message.isHidden()

    page.set_entries(())
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    empty_message = page.findChild(QLabel, "empty_reading")
    assert empty_message is not None
    assert not empty_message.isHidden()
