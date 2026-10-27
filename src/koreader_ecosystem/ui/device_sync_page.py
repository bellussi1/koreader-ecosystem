from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from koreader_ecosystem.koreader_calibre import PreviewItem, PreviewResult
from koreader_ecosystem.mtp import MtpPresence
from koreader_ecosystem.ui.operation_worker import CancellationToken, OperationController


class DeviceSyncPage(QWidget):
    def __init__(
        self,
        services: Any,
        operations: OperationController,
        *,
        embedded: bool = False,
    ) -> None:
        super().__init__()
        self.services = services
        self.operations = operations
        self._pending_action: str | None = None
        self._preview_id: str | None = None
        self._change_count = 0
        self._pending_count = 0
        self._report_path: Path | None = None
        self._preview_items: tuple[PreviewItem, ...] = ()
        self._device_ready = False
        self.setObjectName("pageRoot")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        heading = QLabel("Kindle e sincronização")
        heading.setObjectName("sectionHeading" if embedded else "pageTitle")
        intro = QLabel(
            "O Kindle usa MTP/WPD. Conecte por USB e mantenha a tela de conexão aberta; "
            "a prévia mostra as mudanças antes de qualquer gravação no Calibre."
        )
        intro.setObjectName("pageDescription")
        intro.setWordWrap(True)
        root.addWidget(heading)
        root.addWidget(intro)

        self.device_status_label = QLabel("Kindle USB: verificando o dispositivo em segundo plano…")
        self.device_status_label.setObjectName("kindlePresenceStatus")
        self.device_status_label.setWordWrap(True)
        root.addWidget(self.device_status_label)

        toolbar = QHBoxLayout()
        self.preview_button = QPushButton("Gerar prévia KOReader–Calibre")
        self.preview_button.setObjectName("previewSyncButton")
        self.preview_button.setProperty("primary", True)
        self.preview_button.setEnabled(False)
        toolbar.addWidget(self.preview_button)
        toolbar.addStretch(1)
        root.addLayout(toolbar)

        self.preview_status = QLabel("Gere uma prévia para revisar correspondências e alterações.")
        self.preview_status.setObjectName("previewStatus")
        self.preview_status.setWordWrap(True)
        root.addWidget(self.preview_status)

        self.table = QTableWidget(0, 5)
        self.table.setObjectName("syncPreviewTable")
        self.table.setHorizontalHeaderLabels(["Sidecar", "Livro", "Vínculo", "Motivo", "ID"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setMinimumWidth(0)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 1)

        mapping = QFormLayout()
        mapping.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        mapping.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.sidecar_key_edit = QLineEdit()
        self.sidecar_key_edit.setObjectName("mappingSidecarKeyEdit")
        self.sidecar_key_edit.setPlaceholderText("Selecione uma linha para preencher")
        self.calibre_id_edit = QLineEdit()
        self.calibre_id_edit.setObjectName("mappingCalibreIdEdit")
        self.calibre_id_edit.setPlaceholderText("ID numérico visível no Calibre")
        mapping.addRow("Sidecar SHA-256", self.sidecar_key_edit)
        mapping.addRow("Calibre book ID", self.calibre_id_edit)
        root.addLayout(mapping)

        actions = QHBoxLayout()
        self.save_mapping_button = QPushButton("Salvar vínculo manual")
        self.save_mapping_button.setObjectName("saveManualMappingButton")
        self.save_mapping_button.setEnabled(False)
        self.open_report_button = QPushButton("Abrir relatório detalhado")
        self.open_report_button.setObjectName("openSyncReportButton")
        self.open_report_button.setEnabled(False)
        self.apply_button = QPushButton("Aplicar com backup verificado")
        self.apply_button.setObjectName("applyPreviewButton")
        self.apply_button.setProperty("primary", True)
        self.apply_button.setEnabled(False)
        actions.addWidget(self.save_mapping_button)
        actions.addWidget(self.open_report_button)
        actions.addStretch(1)
        actions.addWidget(self.apply_button)
        root.addLayout(actions)

        self.preview_button.clicked.connect(self.generate_preview)
        self.save_mapping_button.clicked.connect(self.save_mapping)
        self.open_report_button.clicked.connect(self.open_report)
        self.apply_button.clicked.connect(self.apply_preview)
        self.table.itemSelectionChanged.connect(self._populate_mapping_fields)
        self.operations.progress.connect(self._on_progress)
        self.operations.completed.connect(self._on_completed)
        self.operations.failed.connect(self._on_failed)
        self.operations.busyChanged.connect(self._on_busy_changed)

    def generate_preview(self) -> None:
        if not self._device_ready:
            self.preview_status.setText(
                "Conecte o Kindle e deixe-o na tela USB para preparar a prévia."
            )
            return
        self._start(
            "preview",
            "Copiando sidecars para uma prévia local…",
            lambda token: self.services.preview_koreader_calibre(),
        )

    def set_device_presence(self, presence: MtpPresence) -> None:
        self._device_ready = presence.connected
        messages = {
            "ready": "Kindle USB: pronto para prévia e backup.",
            "device_not_found": "Kindle USB: não conectado ou não visível pelo Windows.",
            "multiple_devices": "Kindle USB: mais de um dispositivo; deixe apenas um conectado.",
            "storage_not_found": (
                "Kindle USB: armazenamento indisponível. Deixe o aparelho na tela de conexão USB."
            ),
            "multiple_storages": "Kindle USB: mais de um armazenamento interno detectado.",
            "check_failed": (
                "Kindle USB: não foi possível verificar agora; nova tentativa automática em breve."
            ),
        }
        self.device_status_label.setText(
            messages.get(presence.reason_code, "Kindle USB: estado indisponível.")
        )
        self._update_buttons()

    def show_preview_summary(
        self, *, preview_id: str, change_count: int, pending_count: int
    ) -> None:
        self._preview_id = preview_id
        self._change_count = max(0, change_count)
        self._pending_count = max(0, pending_count)
        self.preview_status.setText(
            f"Prévia {preview_id[:12]} · {self._change_count} alterações · "
            f"{self._pending_count} itens pendentes de correspondência."
        )
        self.apply_button.setEnabled(self._change_count > 0 and not self.operations.is_busy)
        self.save_mapping_button.setEnabled(bool(self._preview_id) and not self.operations.is_busy)

    def save_mapping(self) -> None:
        preview_id = self._preview_id
        sidecar_key = self.sidecar_key_edit.text().strip()
        try:
            book_id = int(self.calibre_id_edit.text())
        except ValueError:
            book_id = 0
        if not preview_id or not sidecar_key or book_id < 1:
            self.preview_status.setText("Selecione um sidecar e informe um Calibre book ID válido.")
            return
        self._start(
            "mapping",
            "Salvando vínculo manual…",
            lambda token: self.services.map_koreader_sidecar(preview_id, sidecar_key, book_id),
        )

    def open_report(self) -> None:
        if self._report_path is not None and self._report_path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._report_path)))

    def apply_preview(self) -> None:
        if not self._preview_id or self._change_count < 1:
            return
        answer = QMessageBox.question(
            self,
            "Aplicar alterações no Calibre",
            f"Aplicar {self._change_count} alterações da prévia {self._preview_id[:12]}? "
            "A biblioteca será respaldada e o Restic será verificado antes da gravação.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        preview_id = self._preview_id
        self._start(
            "apply",
            "Criando backup e aplicando alterações aprovadas…",
            lambda token: self.services.apply_koreader_preview(preview_id, confirmed=True),
        )

    def _start(
        self,
        action: str,
        title: str,
        operation: Callable[[CancellationToken], object],
    ) -> None:
        self._pending_action = action
        status_key = {
            "preview": "koreader_calibre_preview",
            "apply": "koreader_calibre_apply",
        }.get(action)
        if not self.operations.start(title, operation, status_key=status_key):
            self._pending_action = None
            self.preview_status.setText("Aguarde a tarefa em andamento terminar.")

    def _on_completed(self, result: object) -> None:
        action = self._pending_action
        if action is None:
            return
        self._pending_action = None
        if action == "preview":
            if (
                not isinstance(result, tuple)
                or len(result) != 2
                or not isinstance(result[0], PreviewResult)
                or not isinstance(result[1], Path)
            ):
                self.preview_status.setText("A prévia retornou dados inválidos.")
                return
            preview, report_path = result
            self._preview_items = preview.items
            self._report_path = report_path
            self._render_items(preview.items)
            pending = sum(
                item.reason_code
                not in {"exact_calibre_uuid", "exact_isbn", "manual_mapping", "already_current"}
                for item in preview.items
            )
            self.show_preview_summary(
                preview_id=preview.preview_id,
                change_count=len(preview.changes),
                pending_count=pending,
            )
            self.open_report_button.setEnabled(True)
        elif action == "mapping":
            self.preview_status.setText(
                "Vínculo salvo. Gere uma nova prévia para revisar o resultado."
            )
        elif action == "apply":
            self.preview_status.setText(
                f"Aplicação concluída · {getattr(result, 'applied_book_count', 0)} livros · "
                f"{getattr(result, 'applied_field_count', 0)} campos verificados."
            )

    def _render_items(self, items: tuple[PreviewItem, ...]) -> None:
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            values = (
                item.relative_path,
                item.title or "Título ausente",
                item.match_status,
                item.reason_code,
                str(item.book_id) if item.book_id is not None else "—",
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, item.sidecar_key)
                cell.setToolTip(value)
                self.table.setItem(row, column, cell)

    def _populate_mapping_fields(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._preview_items):
            return
        item = self._preview_items[row]
        self.sidecar_key_edit.setText(item.sidecar_key)
        if item.book_id is not None:
            self.calibre_id_edit.setText(str(item.book_id))

    def _on_failed(self, reason_code: str) -> None:
        action = self._pending_action
        if action is None:
            return
        self._pending_action = None
        messages = {
            "kindle_unavailable": "Conecte o Kindle por USB e mantenha a tela USB aberta.",
            "multiple_kindles": "Deixe conectado somente o Kindle que deseja usar.",
            "calibre_integrity_failed": (
                "O banco Calibre precisa passar na verificação antes da sincronização."
            ),
            "backup_failed": "Não foi possível validar o backup prévio; nada foi aplicado.",
            "staging_cleanup_failed": (
                "A cópia temporária não foi removida; confira a pasta de estado local."
            ),
        }
        text = messages.get(reason_code, f"A operação falhou ({reason_code}).")
        self.preview_status.setText(text)

    def _on_progress(self, title: str) -> None:
        if self._pending_action is not None:
            self.preview_status.setText(title)

    def _on_busy_changed(self, busy: bool) -> None:
        self._update_buttons()
        self.apply_button.setEnabled(not busy and bool(self._preview_id) and self._change_count > 0)
        self.save_mapping_button.setEnabled(not busy and bool(self._preview_id))
        self.open_report_button.setEnabled(not busy and self._report_path is not None)

    def _update_buttons(self) -> None:
        self.preview_button.setEnabled(not self.operations.is_busy and self._device_ready)
