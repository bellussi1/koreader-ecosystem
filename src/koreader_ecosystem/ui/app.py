from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from koreader_ecosystem.config import load_config
from koreader_ecosystem.mtp import MtpPresence
from koreader_ecosystem.operation_history import OperationHistory, OperationRecord
from koreader_ecosystem.portable_bundle import resolve_application_asset
from koreader_ecosystem.services import ApplicationServices
from koreader_ecosystem.ui.backup_page import BackupPage
from koreader_ecosystem.ui.device_monitor import DevicePresenceMonitor
from koreader_ecosystem.ui.device_sync_page import DeviceSyncPage
from koreader_ecosystem.ui.operation_worker import OperationController
from koreader_ecosystem.ui.readings_page import ReadingPage
from koreader_ecosystem.ui.remote_page import RemotePage
from koreader_ecosystem.ui.setup_page import SetupPage
from koreader_ecosystem.ui.theme import install_dark_theme

NAV_SECTIONS: tuple[tuple[str, str], ...] = (
    ("readings", "Estante"),
    ("sync", "Sincronização"),
    ("setup", "Configuração"),
)


def _combined_page(*sections: QWidget) -> QScrollArea:
    content = QWidget()
    content.setObjectName("combinedPageContent")
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(18)
    for section in sections:
        layout.addWidget(section)
    layout.addStretch(1)

    area = QScrollArea()
    area.setObjectName("pageScroll")
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(content)
    return area


class MainWindow(QMainWindow):
    def __init__(self, services: ApplicationServices, *, monitor_devices: bool = False) -> None:
        super().__init__()
        self.services = services
        history_path = services.config.state_root / "application-state.sqlite3"
        self.operation_history = OperationHistory(
            history_path,
            legacy_path=services.config.state_root / "operation-history.json",
        )
        self.operations = OperationController(self, history=self.operation_history)
        self.device_monitor: DevicePresenceMonitor | None = None
        self.setWindowTitle("KOReader Ecosystem")
        self.setWindowIcon(QIcon(str(resolve_application_asset("KOReaderEcosystem.ico"))))
        self.setObjectName("mainWindow")
        self.setMinimumSize(760, 560)
        self.resize(1280, 860)

        central = QWidget()
        central.setObjectName("contentRoot")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        toolbar = QWidget()
        toolbar.setObjectName("appToolbar")
        toolbar.setMinimumHeight(62)
        toolbar.setMaximumHeight(68)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(20, 6, 20, 6)
        toolbar_layout.setSpacing(10)
        self.toolbar = toolbar

        brand_area = QWidget()
        brand_area.setObjectName("brandArea")
        brand_layout = QHBoxLayout(brand_area)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(8)
        brand_icon = QLabel()
        brand_icon.setObjectName("appBrandIcon")
        brand_icon.setPixmap(self.windowIcon().pixmap(QSize(30, 30)))
        brand_icon.setAccessibleName("Ícone KOReader Ecosystem")
        self.brand_icon = brand_icon
        brand = QLabel("KOReader Ecosystem")
        brand.setObjectName("brand")
        self.brand_label = brand
        brand_layout.addWidget(brand_icon)
        brand_layout.addWidget(brand)
        toolbar_layout.addWidget(brand_area)
        self.brand_area = brand_area

        navigation = QListWidget()
        navigation.setObjectName("mainNavigation")
        navigation.setFlow(QListView.Flow.LeftToRight)
        navigation.setWrapping(False)
        navigation.setFrameShape(QFrame.Shape.NoFrame)
        navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        navigation.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        navigation.setSpacing(3)
        navigation.setFixedHeight(48)
        navigation.setFixedWidth(440)
        navigation.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        for key, title in NAV_SECTIONS:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(QSize({"readings": 100, "sync": 160, "setup": 150}[key], 42))
            navigation.addItem(item)
        self.navigation = navigation
        toolbar_layout.addStretch(1)
        toolbar_layout.addWidget(navigation)

        main_area = QWidget()
        main_area.setObjectName("mainArea")
        main_layout = QVBoxLayout(main_area)
        main_layout.setContentsMargins(24, 20, 24, 24)
        main_layout.setSpacing(12)
        self.main_layout = main_layout

        self.compact_navigation = QComboBox()
        self.compact_navigation.setObjectName("compactNavigation")
        self.compact_navigation.setMinimumWidth(140)
        self.compact_navigation.setMaximumWidth(280)
        for key, title in NAV_SECTIONS:
            self.compact_navigation.addItem(title, key)
        toolbar_layout.addWidget(self.compact_navigation)
        toolbar_layout.addStretch(1)
        self.operation_status = QLabel("Pronto")
        self.operation_status.setObjectName("globalOperationStatus")
        self.operation_status.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.operation_status.setMinimumWidth(104)
        self.operation_status.setWordWrap(True)
        self.operation_status.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        toolbar_layout.addWidget(self.operation_status)
        self.device_status = QLabel("Kindle: inativo")
        self.device_status.setObjectName("deviceConnectionStatus")
        self.device_status.setToolTip("Kindle USB: monitoramento inativo")
        self.device_status.setMinimumWidth(132)
        self.device_status.setWordWrap(True)
        self.device_status.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.device_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        toolbar_layout.addWidget(self.device_status)
        root.addWidget(toolbar)
        self.pages = QStackedWidget()
        self.pages.setObjectName("mainPages")
        self.page_indices: dict[str, int] = {}
        self.readings_page = ReadingPage(services, self.operations)
        self.device_page = DeviceSyncPage(services, self.operations, embedded=True)
        self.backup_page = BackupPage(services, self.operations, embedded=True)
        remote_page = RemotePage(services, self.operations, embedded=True)
        self.setup_page = SetupPage(
            services,
            self.operations,
            additional_sections=(remote_page,),
        )
        # Keep the former attribute available for integrations that used the first view as home.
        self.home_page = self.readings_page
        pages: dict[str, QWidget] = {
            "readings": self.readings_page,
            "sync": _combined_page(self.device_page, self.backup_page),
            "setup": self.setup_page,
        }
        for index, (key, _title) in enumerate(NAV_SECTIONS):
            self.page_indices[key] = index
            self.pages.addWidget(pages[key])
        main_layout.addWidget(self.pages)
        root.addWidget(main_area, 1)
        self.setCentralWidget(central)

        navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        navigation.currentRowChanged.connect(self._sync_compact_navigation)
        self.compact_navigation.currentIndexChanged.connect(navigation.setCurrentRow)
        navigation.setCurrentRow(0)
        self.operations.progress.connect(self._operation_progress)
        self.operations.busyChanged.connect(self._operation_busy_changed)
        if monitor_devices:
            self.device_status.setText("Kindle USB: procurando dispositivo…")
            self.device_monitor = DevicePresenceMonitor(self)
            self.device_monitor.presenceChanged.connect(self._on_device_presence)
            self.device_monitor.start()
        self._operation_busy_changed(self.operations.is_busy)
        self._update_responsive_layout(self.width())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "navigation"):
            self._update_responsive_layout(event.size().width())

    def _update_responsive_layout(self, width: int) -> None:
        compact = width < 1080
        self.navigation.setVisible(not compact)
        self.compact_navigation.setVisible(compact)
        self.brand_area.setVisible(True)
        self.brand_label.setVisible(width >= 820)
        self.operation_status.setMaximumWidth(210 if compact else 280)
        self.device_status.setMaximumWidth(200 if compact else 240)
        margin = 16 if compact else 24
        top_margin = 16 if compact else 20
        self.main_layout.setContentsMargins(margin, top_margin, margin, margin)

    def _sync_compact_navigation(self, index: int) -> None:
        blocked = self.compact_navigation.blockSignals(True)
        self.compact_navigation.setCurrentIndex(index)
        self.compact_navigation.blockSignals(blocked)

    def navigate_to(self, key: str) -> None:
        index = self.page_indices.get(key)
        if index is not None:
            self.navigation.setCurrentRow(index)

    def _operation_progress(self, title: str) -> None:
        self.operation_status.setText(title)

    def _operation_busy_changed(self, busy: bool) -> None:
        if busy:
            return
        try:
            record = self.operation_history.latest()
        except Exception:
            record = None
        self.operation_status.setText(self._operation_summary(record))

    @staticmethod
    def _operation_summary(record: OperationRecord | None) -> str:
        if record is None:
            return "Pronto"
        labels = {
            "hardcover_library_refresh": "Estante Hardcover",
            "hardcover_mutation": "Alteração Hardcover",
            "koreader_calibre_preview": "Prévia KOReader–Calibre",
            "koreader_calibre_apply": "Sincronização Calibre",
            "local_backup": "Backup local",
            "local_restore": "Restauração local",
            "remote_sync": "Backup remoto",
        }
        label = labels.get(record.key, "Tarefa")
        if record.status == "running":
            return f"{label}: interrompida"
        if record.status == "failed":
            return f"{label}: falhou"
        if record.status == "cancelled":
            return f"{label}: cancelada"
        try:
            when = datetime.fromisoformat(record.updated_at).astimezone().strftime("%d/%m %H:%M")
        except ValueError, OverflowError:
            when = "agora"
        return f"{label}: {when}"

    def _on_device_presence(self, presence: MtpPresence) -> None:
        labels = {
            "ready": "Kindle: pronto",
            "device_not_found": "Kindle: desconectado",
            "multiple_devices": "Kindles: vários",
            "storage_not_found": "Kindle: abra tela USB",
            "multiple_storages": "Kindle: armazenamento ambíguo",
            "check_failed": "Kindle: tentando novamente",
        }
        self.device_status.setText(labels.get(presence.reason_code, "Kindle: indisponível"))
        self.device_status.setToolTip(f"Kindle USB: {presence.reason_code.replace('_', ' ')}")
        self.device_page.set_device_presence(presence)
        self.backup_page.set_device_presence(presence)

    def closeEvent(self, event: Any) -> None:
        if self.operations.is_busy:
            QMessageBox.information(
                self,
                "Tarefa em andamento",
                "Aguarde a tarefa atual terminar antes de fechar o aplicativo.",
            )
            event.ignore()
            return
        if self.device_monitor is not None and self.device_monitor.isRunning():
            self.device_monitor.stop()
            self.device_monitor.wait(8_500)
        super().closeEvent(event)


def create_application(
    argv: Sequence[str] | None = None,
    *,
    services: ApplicationServices | None = None,
    show: bool = True,
) -> int:
    existing_app = QApplication.instance()
    app = (
        QApplication(list(argv) if argv is not None else sys.argv)
        if existing_app is None
        else cast(QApplication, existing_app)
    )
    app.setApplicationName("KOReader Ecosystem")
    app.setWindowIcon(QIcon(str(resolve_application_asset("KOReaderEcosystem.ico"))))
    app.setStyle("Fusion")
    install_dark_theme(app)

    window = MainWindow(services or ApplicationServices(load_config()), monitor_devices=show)
    if not show:
        return 0
    window.show()
    return app.exec()


def main() -> int:
    if "--smoke-test" in sys.argv[1:]:
        return create_application(show=False)
    return create_application()


if __name__ == "__main__":
    sys.exit(main())
