from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from koreader_ecosystem.ui.operation_worker import (
    CancellationToken,
    OperationController,
    safe_reason_code,
)


class HomePage(QWidget):
    navigateRequested = Signal(str)

    def __init__(self, services: Any, operations: OperationController) -> None:
        super().__init__()
        self.services = services
        self.operations = operations
        self._pending = False
        self.setObjectName("pageRoot")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        eyebrow = QLabel("KINDLE · CALIBRE · HARDCOVER")
        eyebrow.setObjectName("eyebrow")
        heading = QLabel("Sua biblioteca")
        heading.setObjectName("pageTitle")
        intro = QLabel("Leitura, organização e cópias de segurança em um só lugar.")
        intro.setObjectName("pageDescription")
        root.addWidget(eyebrow)
        root.addWidget(heading)
        root.addWidget(intro)

        toolbar = QHBoxLayout()
        self.refresh_button = QPushButton("Atualizar diagnóstico")
        self.refresh_button.setObjectName("refreshHomeButton")
        self.refresh_button.setProperty("primary", True)
        self.home_status = QLabel("O diagnóstico ainda não foi atualizado.")
        self.home_status.setObjectName("homeStatus")
        self.home_status.setWordWrap(True)
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.home_status, 1)
        root.addLayout(toolbar)

        cards = QGridLayout()
        cards.setSpacing(12)
        self.calibre_card = self._status_card(
            "Calibre", "Ainda não verificado", "homeCalibreStatus"
        )
        self.kindle_card = self._status_card("Kindle", "Ainda não verificado", "homeKindleStatus")
        self.backup_card = self._status_card("Backup", "Ainda não verificado", "homeBackupStatus")
        self.hardcover_card = self._status_card(
            "Hardcover", "Aguardando verificação", "homeHardcoverStatus"
        )
        self._status_grid = cards
        self._status_cards = (
            self.calibre_card,
            self.kindle_card,
            self.backup_card,
            self.hardcover_card,
        )
        self._status_columns = 0
        root.addLayout(cards)

        shortcuts = QGridLayout()
        shortcuts.setHorizontalSpacing(10)
        shortcuts.setVerticalSpacing(8)
        self._shortcut_grid = shortcuts
        self._shortcut_buttons: list[QPushButton] = []
        for label, key in (
            ("Abrir leituras", "readings"),
            ("Preparar backup", "backups"),
            ("Configurar integração", "setup"),
        ):
            button = QPushButton(label)
            button.setProperty("destination", key)
            button.clicked.connect(
                lambda _checked=False, target=key: self.navigateRequested.emit(target)
            )
            self._shortcut_buttons.append(button)
        root.addLayout(shortcuts)
        root.addStretch(1)

        self.refresh_button.clicked.connect(self.refresh)
        self.operations.completed.connect(self._on_completed)
        self.operations.failed.connect(self._on_failed)
        self.operations.busyChanged.connect(self._on_busy_changed)
        self.operations.progress.connect(self._on_progress)
        self._reflow_layout(self.width())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_shortcut_grid"):
            self._reflow_layout(event.size().width())

    def _reflow_layout(self, width: int) -> None:
        status_columns = 2 if width >= 720 else 1
        if status_columns != self._status_columns:
            while self._status_grid.count():
                self._status_grid.takeAt(0)
            for index, card in enumerate(self._status_cards):
                row, column = divmod(index, status_columns)
                self._status_grid.addWidget(card, row, column)
            self._status_columns = status_columns

        shortcut_columns = 3 if width >= 720 else 1
        if getattr(self, "_shortcut_columns", 0) != shortcut_columns:
            while self._shortcut_grid.count():
                self._shortcut_grid.takeAt(0)
            for index, button in enumerate(self._shortcut_buttons):
                row, column = divmod(index, shortcut_columns)
                self._shortcut_grid.addWidget(button, row, column)
            self._shortcut_columns = shortcut_columns

    @staticmethod
    def _status_card(title: str, status: str, object_name: str) -> QFrame:
        card = QFrame()
        card.setObjectName("homeStatusCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 15, 17, 15)
        name = QLabel(title)
        name.setProperty("textRole", "cardTitle")
        value = QLabel(status)
        value.setObjectName(object_name)
        value.setProperty("textRole", "cardValue")
        value.setWordWrap(True)
        layout.addWidget(name)
        layout.addWidget(value)
        return card

    def refresh(self) -> None:
        self._pending = True
        accepted = self.operations.start(
            "Atualizando diagnóstico…",
            self._check_status,
        )
        if not accepted:
            self._pending = False
            self.home_status.setText("Aguarde a tarefa em andamento terminar.")

    def _check_status(self, token: CancellationToken) -> dict[str, object]:
        report = self.services.doctor()
        snapshots = 0
        snapshot_error: str | None = None
        try:
            snapshots = len(self.services.status())
        except Exception as error:
            snapshot_error = safe_reason_code(error)
        return {"report": report, "snapshot_count": snapshots, "snapshot_error": snapshot_error}

    def _on_completed(self, result: object) -> None:
        if not self._pending or not isinstance(result, dict):
            return
        self._pending = False
        report = result.get("report")
        checks = report.get("checks") if isinstance(report, dict) else None
        if not isinstance(report, dict) or not isinstance(checks, dict):
            self.home_status.setText("O diagnóstico retornou dados inválidos.")
            return
        self._set_card(self.calibre_card, checks.get("calibre"))
        self._set_card(self.kindle_card, checks.get("kindle"))
        count = result.get("snapshot_count")
        snapshot_error = result.get("snapshot_error")
        backup_value = (
            f"{count} snapshots disponíveis"
            if snapshot_error is None
            else "Senha Restic ou repositório pendente"
        )
        self._set_card(self.backup_card, backup_value)
        credentials = checks.get("credentials")
        hardcover_ready = (
            isinstance(credentials, dict) and credentials.get("hardcover_token_stored") is True
        )
        self._set_card(
            self.hardcover_card,
            "Chave nova validada" if hardcover_ready else "Precisa cadastrar uma chave nova",
        )
        self.home_status.setText(
            "Diagnóstico atualizado."
            if report.get("status") == "ok"
            else "Alguns itens precisam de atenção; veja Configurações."
        )

    @staticmethod
    def _set_card(card: QFrame, result: object) -> None:
        labels = card.findChildren(QLabel)
        if len(labels) < 2:
            return
        status = result.get("status") if isinstance(result, dict) else None
        if status is not None:
            text = "Pronto" if status == "ok" else "Precisa de atenção"
            if isinstance(result, dict) and status == "ok" and "file_count" in result:
                text = f"Conectado · {result['file_count']} arquivos"
        else:
            text = str(result)
        labels[1].setText(text)

    def _on_failed(self, reason_code: str) -> None:
        if self._pending:
            self._pending = False
            self.home_status.setText(f"Não foi possível atualizar ({reason_code}).")

    def _on_busy_changed(self, busy: bool) -> None:
        self.refresh_button.setEnabled(not busy)

    def _on_progress(self, title: str) -> None:
        if self._pending:
            self.home_status.setText(title)
