from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from koreader_ecosystem.ui import theme


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_dark_theme_uses_hardcover_navy_surfaces_and_non_green_accents(
    qapp: QApplication,
) -> None:
    theme.install_dark_theme(qapp)
    palette = qapp.palette()

    assert palette.color(palette.ColorRole.Window).name() == "#1e293b"
    assert palette.color(palette.ColorRole.Base).name() == "#0f172a"
    assert palette.color(palette.ColorRole.Highlight).name() == "#4f46e5"
    assert "#f97316" in theme.APP_STYLE
    assert "#151815" not in theme.APP_STYLE
    assert "#6eae8d" not in theme.APP_STYLE
    assert 'font-family: "Inter";' in theme.APP_STYLE
    assert 'font-family: "Lora";' in theme.APP_STYLE
    assert "font-family: Georgia;" not in theme.APP_STYLE
    assert "#385344" not in theme.APP_STYLE
    assert "Inter" in QFontDatabase.families()
    assert "Lora" in QFontDatabase.families()


def test_application_icon_uses_the_same_non_green_palette() -> None:
    icon = Path(__file__).resolve().parents[2] / "assets" / "KOReaderEcosystem.svg"
    svg = icon.read_text(encoding="utf-8")

    assert "#4f9c78" not in svg
    assert "#74c29a" not in svg
    assert "#f97316" in svg
