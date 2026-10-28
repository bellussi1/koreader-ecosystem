from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QListView, QListWidget, QStackedWidget, QWidget

from koreader_ecosystem.config import AppConfig
from koreader_ecosystem.ui.app import NAV_SECTIONS, MainWindow, create_application, main


class StubServices:
    state_root: Path

    def __init__(self) -> None:
        self.private_token = "PRIVATE_TOKEN_SENTINEL"
        self.private_path = r"C:\private\library"
        self.config = AppConfig(
            calibre_library=Path(self.private_path),
            restic_repository=Path(r"C:\private\repository"),
            state_root=self.state_root,
        )
        self.hardcover_ready = False


@pytest.fixture(autouse=True)
def isolated_stub_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(StubServices, "state_root", tmp_path, raising=False)


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_main_window_has_approved_sections_and_starts_at_home(qapp: QApplication) -> None:
    window = MainWindow(StubServices())  # type: ignore[arg-type]

    navigation = window.findChild(QListWidget, "mainNavigation")

    assert navigation is not None
    assert tuple(navigation.item(index).text() for index in range(navigation.count())) == tuple(
        title for _, title in NAV_SECTIONS
    )
    assert navigation.currentRow() == 0
    assert window.windowTitle() == "KOReader Ecosystem"
    assert navigation.parentWidget() is not None
    assert navigation.parentWidget().objectName() == "appToolbar"
    assert navigation.flow() == QListView.Flow.LeftToRight
    assert window.findChild(QWidget, "sidebar") is None
    navigation.setCurrentRow(1)
    assert window.findChild(QStackedWidget, "mainPages").currentIndex() == 1  # type: ignore[union-attr]


def test_main_window_does_not_render_secrets_or_private_paths(qapp: QApplication) -> None:
    window = MainWindow(StubServices())  # type: ignore[arg-type]
    visible_text = " ".join(widget.text() for widget in window.home_page.findChildren(QLabel))

    assert "PRIVATE_TOKEN_SENTINEL" not in visible_text
    assert r"C:\private\library" not in visible_text


def test_main_window_uses_the_project_icon(qapp: QApplication) -> None:
    window = MainWindow(StubServices())  # type: ignore[arg-type]

    assert not window.windowIcon().isNull()
    assert [size.width() for size in window.windowIcon().availableSizes()] == [
        16,
        24,
        32,
        48,
        64,
        128,
        256,
    ]


def test_toolbar_statuses_remain_visible_at_wide_and_compact_widths(
    qapp: QApplication,
) -> None:
    window = MainWindow(StubServices())  # type: ignore[arg-type]
    window.resize(1280, 860)
    window.show()
    qapp.processEvents()

    assert window.navigation.isVisible()
    assert window.compact_navigation.isHidden()
    assert window.operation_status.width() > 0
    assert window.device_status.width() > 0

    window.resize(760, 560)
    qapp.processEvents()

    assert window.navigation.isHidden()
    assert window.compact_navigation.isVisible()
    assert window.operation_status.width() > 0
    assert window.device_status.width() > 0
    window.close()


def test_create_application_accepts_services_without_optional_tools(qapp: QApplication) -> None:
    result = create_application([], services=StubServices(), show=False)  # type: ignore[arg-type]

    assert result == 0


def test_main_supports_packaged_startup_smoke_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import koreader_ecosystem.ui.app as app_module

    calls: list[bool] = []
    monkeypatch.setattr(sys, "argv", ["KOReaderEcosystem.exe", "--smoke-test"])
    monkeypatch.setattr(
        app_module,
        "create_application",
        lambda **kwargs: calls.append(kwargs["show"]) or 0,
    )

    assert main() == 0
    assert calls == [False]
