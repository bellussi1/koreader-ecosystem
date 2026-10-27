from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
)

from koreader_ecosystem.config import AppConfig
from koreader_ecosystem.koreader_calibre import PreviewItem
from koreader_ecosystem.mtp import MtpPresence
from koreader_ecosystem.ui.backup_page import BackupPage
from koreader_ecosystem.ui.device_sync_page import DeviceSyncPage
from koreader_ecosystem.ui.home_page import HomePage
from koreader_ecosystem.ui.operation_worker import OperationController
from koreader_ecosystem.ui.readings_page import ReadingPage
from koreader_ecosystem.ui.remote_page import RemotePage
from koreader_ecosystem.ui.setup_page import SetupPage


class StubServices:
    def __init__(self, root: Path, *, remote: bool = False) -> None:
        self.config = AppConfig(
            calibre_library=root / "private-library",
            restic_repository=root / "private-repository",
            state_root=root / "private-state",
            restic_remote_repository=(
                "s3:https://account.example.test/bucket/repo" if remote else None
            ),
            restic_remote_region="auto" if remote else None,
        )
        self.calls: list[tuple[str, object]] = []

    def doctor(self) -> dict[str, object]:
        return {"status": "needs_attention", "checks": {}}

    def status(self) -> tuple[dict[str, str], ...]:
        return ()

    def create_backup(self, **kwargs: object) -> object:
        self.calls.append(("backup", kwargs))
        return object()

    def validate_restore(self, snapshot: str, target: Path) -> object:
        self.calls.append(("restore", (snapshot, target)))
        return object()

    def inspect_kindle(self) -> object:
        self.calls.append(("inspect", None))
        return object()

    def preview_koreader_calibre(self) -> object:
        self.calls.append(("preview", None))
        return object()

    def apply_koreader_preview(self, preview_id: str, *, confirmed: bool = False) -> object:
        self.calls.append(("apply", (preview_id, confirmed)))
        return object()

    def configure_remote(self, **kwargs: object) -> None:
        self.calls.append(("configure_remote", kwargs))

    def sync_remote(self) -> object:
        self.calls.append(("sync_remote", None))
        return object()


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_backup_cancel_does_not_start_the_operation(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = StubServices(tmp_path)
    page = BackupPage(services, OperationController())  # type: ignore[arg-type]
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel,
    )

    page.findChild(QPushButton, "createBackupButton").click()  # type: ignore[union-attr]

    assert services.calls == []


def test_apply_cancel_does_not_write_calibre_or_device(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = StubServices(tmp_path)
    page = DeviceSyncPage(services, OperationController())  # type: ignore[arg-type]
    page.show_preview_summary(preview_id="preview-123", change_count=2, pending_count=0)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel,
    )

    page.findChild(QPushButton, "applyPreviewButton").click()  # type: ignore[union-attr]

    assert services.calls == []


def test_remote_page_masks_keys_and_disables_sync_until_configured(
    qapp: QApplication, tmp_path: Path
) -> None:
    page = RemotePage(StubServices(tmp_path), OperationController())  # type: ignore[arg-type]
    access_key = page.findChild(QLineEdit, "s3AccessKeyEdit")
    secret_key = page.findChild(QLineEdit, "s3SecretKeyEdit")
    sync = page.findChild(QPushButton, "syncRemoteButton")
    status = page.findChild(QLabel, "remoteStatus")

    assert access_key is not None and access_key.echoMode() == QLineEdit.EchoMode.Password
    assert secret_key is not None and secret_key.echoMode() == QLineEdit.EchoMode.Password
    assert sync is not None and not sync.isEnabled()
    assert status is not None and "adiado" in status.text()


def test_usb_preview_and_full_backup_wait_for_detected_device(
    qapp: QApplication, tmp_path: Path
) -> None:
    services = StubServices(tmp_path)
    operations = OperationController()
    device_page = DeviceSyncPage(services, operations)  # type: ignore[arg-type]
    backup_page = BackupPage(services, operations)  # type: ignore[arg-type]
    preview = device_page.findChild(QPushButton, "previewSyncButton")
    backup = backup_page.findChild(QPushButton, "createBackupButton")
    calibre_only = backup_page.findChild(QCheckBox, "calibreOnlyBackup")

    assert preview is not None and not preview.isEnabled()
    assert backup is not None and not backup.isEnabled()
    assert calibre_only is not None

    presence = MtpPresence(connected=True, reason_code="ready")
    device_page.set_device_presence(presence)
    backup_page.set_device_presence(presence)
    assert preview.isEnabled()
    assert backup.isEnabled()

    backup_page.set_device_presence(MtpPresence(False, "device_not_found"))
    assert not backup.isEnabled()
    calibre_only.setChecked(True)
    assert backup.isEnabled()


def test_sync_preview_table_fits_the_viewport_without_horizontal_scrolling(
    qapp: QApplication, tmp_path: Path
) -> None:
    page = DeviceSyncPage(StubServices(tmp_path), OperationController())  # type: ignore[arg-type]
    page.resize(720, 560)
    page.show()
    qapp.processEvents()

    table = page.findChild(type(page.table), "syncPreviewTable")
    assert table is not None
    assert table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert table.textElideMode() == Qt.TextElideMode.ElideMiddle
    assert table.horizontalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch
    assert (
        sum(table.columnWidth(column) for column in range(table.columnCount()))
        <= table.viewport().width()
    )
    long_path = "/".join(("Biblioteca" * 12, "Livro" * 16, "metadata.epub.lua"))
    page._render_items(
        (
            PreviewItem(
                sidecar_key="a" * 64,
                relative_path=long_path,
                source_digest="b" * 64,
                match_status="matched",
                reason_code="exact_calibre_uuid",
                book_id=42,
                calibre_uuid="uuid-42",
                title="Um título de livro longo para conferir a prévia",
                old_values={},
                new_values={},
                warnings=(),
            ),
        )
    )
    assert table.item(0, 0).toolTip() == long_path  # type: ignore[union-attr]
    assert table.horizontalScrollBar().maximum() == 0

    page.close()


def test_vertical_page_scroll_areas_never_scroll_sideways(
    qapp: QApplication, tmp_path: Path
) -> None:
    services = StubServices(tmp_path)
    reading_page = ReadingPage(services, OperationController())  # type: ignore[arg-type]
    setup_page = SetupPage(services, OperationController())  # type: ignore[arg-type]

    scroll_areas = reading_page.findChildren(QScrollArea) + setup_page.findChildren(QScrollArea)
    assert scroll_areas
    assert all(
        area.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        for area in scroll_areas
    )


def test_home_does_not_render_local_paths_or_secret_values(
    qapp: QApplication, tmp_path: Path
) -> None:
    from PySide6.QtWidgets import QLabel

    page = HomePage(StubServices(tmp_path), OperationController())  # type: ignore[arg-type]
    rendered = " ".join(label.text() for label in page.findChildren(QLabel))

    assert "private-library" not in rendered
    assert "private-repository" not in rendered
    assert "PRIVATE" not in rendered
