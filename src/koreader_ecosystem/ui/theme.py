from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from koreader_ecosystem.portable_bundle import resolve_application_asset

_APP_FONTS_LOADED = False


def _load_app_fonts() -> None:
    global _APP_FONTS_LOADED
    if _APP_FONTS_LOADED:
        return
    for font_name in ("Inter.ttf", "Lora.ttf"):
        font_id = QFontDatabase.addApplicationFont(str(resolve_application_asset(font_name)))
        if font_id < 0:
            raise RuntimeError("A fonte da interface não pôde ser carregada.")
    _APP_FONTS_LOADED = True


APP_STYLE = """
QMainWindow, QWidget#contentRoot, QWidget#mainArea, QStackedWidget,
QWidget#pageRoot, QWidget#readingsPage {
    background-color: #1e293b;
    color: #f8fafc;
    font-family: "Inter";
    font-size: 13px;
}
QWidget#appToolbar {
    background-color: #1e293b;
    border-bottom: 1px solid #334155;
}
QLabel#brand {
    color: #f8fafc;
    font-family: "Inter";
    font-size: 16px;
    font-weight: 700;
    padding: 3px 0;
}
QListWidget#mainNavigation {
    background: transparent;
    border: 0;
    color: #cbd5e1;
    outline: 0;
    max-height: 48px;
}
QListWidget#mainNavigation:focus {
    border: 0;
}
QListWidget#mainNavigation::item {
    border: 1px solid transparent;
    border-radius: 7px;
    margin: 2px;
    min-height: 25px;
    padding: 7px 11px;
}
QListWidget#mainNavigation::item:selected {
    background-color: #0f172a;
    border-color: #334155;
    border-bottom: 2px solid #f97316;
    color: #f8fafc;
    font-weight: 650;
}
QListWidget#mainNavigation::item:hover:!selected {
    background-color: #273449;
    color: #f1f5f9;
}
QLabel#pageTitle {
    color: #f8fafc;
    font-family: "Lora";
    font-size: 24px;
    font-weight: 500;
}
QLabel#pageDescription {
    color: #94a3b8;
    font-size: 13px;
}
QLabel#eyebrow {
    color: #c4b5fd;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#globalOperationStatus {
    background-color: #111827;
    border: 1px solid #334155;
    border-radius: 12px;
    color: #cbd5e1;
    max-width: 360px;
    padding: 7px 12px;
}
QLabel#deviceConnectionStatus {
    background-color: #111827;
    border: 1px solid #334155;
    border-radius: 12px;
    color: #cbd5e1;
    padding: 7px 12px;
}
QFrame#pageSurface, QFrame#homeStatusCard, QFrame[surfaceRole="bookCard"],
QFrame#setupSection, QFrame#contentSection {
    background-color: #0f172a;
    border: 1px solid #334155;
    border-radius: 11px;
}
QFrame#homeStatusCard {
    border-left: 3px solid #818cf8;
}
QFrame#setupSection, QFrame#contentSection {
    margin-top: 2px;
}
QLabel#sectionHeading {
    color: #f8fafc;
    font-family: "Lora";
    font-size: 18px;
    font-weight: 600;
}
QLabel#homeStatus {
    color: #cbd5e1;
}
QLabel[textRole="cardTitle"] {
    color: #f1f5f9;
    font-size: 14px;
    font-weight: 650;
}
QLabel[textRole="cardValue"] {
    color: #94a3b8;
}
QLabel[textRole="bookStatusBadge"] {
    background-color: #312e81;
    border: 1px solid #4338ca;
    border-radius: 8px;
    color: #e0e7ff;
    font-size: 9px;
    font-weight: 700;
    padding: 4px 8px;
}
QLabel[textRole="bookStatusBadge"][shelfStatus="want"] {
    background-color: #422006;
    border-color: #92400e;
    color: #fcd34d;
}
QLabel[textRole="bookStatusBadge"][shelfStatus="paused"] {
    background-color: #312e81;
    border-color: #4338ca;
    color: #c7d2fe;
}
QLabel[textRole="bookStatusBadge"][shelfStatus="completed"] {
    background-color: #1e3a5f;
    border-color: #1d4ed8;
    color: #bfdbfe;
}
QLabel[textRole="bookStatusBadge"][shelfStatus="dnf"] {
    background-color: #482f31;
    border-color: #704448;
    color: #f1c5c1;
}
QLabel[textRole="bookTitle"] {
    color: #f8fafc;
    font-family: "Lora";
    font-size: 16px;
    font-weight: 600;
}
QLabel[textRole="bookAuthor"] {
    color: #94a3b8;
    font-size: 12px;
}
QLabel[textRole="bookFormat"] {
    color: #94a3b8;
    font-size: 11px;
}
QLabel#readingsStatus, QLabel#backupStatus, QLabel#deviceStatus,
QLabel#previewStatus, QLabel#remoteStatus, QLabel#setupDiagnostics,
QLabel#credentialStatus, QLabel#pluginStatus {
    background-color: #111827;
    border: 1px solid #334155;
    border-radius: 8px;
    color: #cbd5e1;
    padding: 10px 12px;
}
QLabel#readingsStatus {
    background: transparent;
    border: 0;
    color: #94a3b8;
    padding: 0;
}
QLabel#hardcoverSyncConfirmation {
    background: transparent;
    border: 0;
    color: #94a3b8;
    font-size: 11px;
    padding: 0;
}
QLabel[textRole="progressText"] {
    color: #cbd5e1;
    font-size: 12px;
}
QLabel#empty_reading, QLabel#empty_want, QLabel#empty_paused,
QLabel#empty_completed, QLabel#empty_dnf {
    background-color: #111827;
    border: 1px dashed #475569;
    border-radius: 10px;
    color: #94a3b8;
    padding: 18px;
}
QPushButton {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
    color: #e2e8f0;
    min-height: 34px;
    padding: 5px 13px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #334155;
    border-color: #64748b;
}
QPushButton:pressed {
    background-color: #3730a3;
    border-color: #4f46e5;
}
QPushButton:focus {
    border: 1px solid #818cf8;
}
QPushButton:disabled {
    background-color: #1e293b;
    border-color: #334155;
    color: #94a3b8;
}
QPushButton[primary="true"] {
    background-color: #f97316;
    border-color: #f97316;
    color: #111827;
    font-weight: 700;
}
QPushButton[primary="true"]:hover {
    background-color: #fb923c;
    border-color: #fb923c;
    color: #111827;
}
QPushButton[primary="true"]:pressed {
    background-color: #ea580c;
    border-color: #ea580c;
}
QLineEdit, QPlainTextEdit, QComboBox {
    background-color: #0f172a;
    border: 1px solid #475569;
    border-radius: 7px;
    color: #f8fafc;
    min-height: 22px;
    padding: 7px 9px;
    selection-background-color: #4338ca;
    selection-color: #ffffff;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
    border: 1px solid #818cf8;
}
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled {
    background-color: #1e293b;
    color: #94a3b8;
}
QComboBox::drop-down {
    border: 0;
    width: 28px;
}
QComboBox QAbstractItemView {
    background-color: #111827;
    border: 1px solid #334155;
    color: #f1f5f9;
    selection-background-color: #3730a3;
    selection-color: #ffffff;
    outline: 0;
    padding: 4px;
}
QCheckBox {
    color: #e2e8f0;
    spacing: 9px;
}
QCheckBox:focus {
    border: 1px solid #818cf8;
    border-radius: 4px;
}
QCheckBox::indicator {
    background-color: #0f172a;
    border: 1px solid #64748b;
    border-radius: 4px;
    height: 16px;
    width: 16px;
}
QCheckBox::indicator:hover {
    border-color: #818cf8;
}
QCheckBox::indicator:checked {
    background-color: #f97316;
    border-color: #f97316;
}
QTableWidget#syncPreviewTable {
    background-color: #0f172a;
    alternate-background-color: #111827;
    border: 1px solid #334155;
    border-radius: 9px;
    color: #e2e8f0;
    gridline-color: #273449;
    selection-background-color: #3730a3;
    selection-color: #ffffff;
}
QTableWidget#syncPreviewTable QTableCornerButton::section {
    background-color: #111827;
    border: 0;
}
QHeaderView::section {
    background-color: #111827;
    border: 0;
    border-bottom: 1px solid #334155;
    color: #cbd5e1;
    font-weight: 650;
    padding: 9px;
}
QProgressBar#bookProgress {
    background-color: #334155;
    border: 0;
    border-radius: 4px;
    max-height: 8px;
    min-height: 8px;
    text-align: center;
}
QProgressBar#bookProgress::chunk {
    background-color: #f97316;
    border-radius: 4px;
}
QScrollArea, QAbstractScrollArea {
    background: transparent;
    border: 0;
}
QScrollArea QWidget#qt_scrollarea_viewport {
    background: transparent;
}
QScrollBar:vertical {
    background: transparent;
    margin: 2px;
    width: 10px;
}
QScrollBar:horizontal {
    background: transparent;
    margin: 2px;
    height: 10px;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background-color: #475569;
    border-radius: 4px;
    min-height: 24px;
    min-width: 24px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background-color: #64748b;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
    border: 0;
}
QToolTip {
    background-color: #1e293b;
    border: 1px solid #475569;
    color: #f8fafc;
    padding: 5px 7px;
}
"""


def install_dark_theme(application: QApplication) -> None:
    """Install a consistent dark palette for widgets and native Qt dialogs."""
    _load_app_fonts()
    application.setFont(QFont("Inter"))
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#1e293b"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f8fafc"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#0f172a"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#111827"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#1e293b"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#f8fafc"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#f8fafc"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#1e293b"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#e2e8f0"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#4f46e5"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    application.setPalette(palette)
    application.setStyleSheet(APP_STYLE)
