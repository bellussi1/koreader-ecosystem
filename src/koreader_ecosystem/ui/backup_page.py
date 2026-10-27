from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from koreader_ecosystem.mtp import MtpPresence
from koreader_ecosystem.ui.operation_worker import CancellationToken, OperationController


class BackupPage(QWidget):
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
        self._refresh_after_backup = False
        self._device_ready = False
        self.setObjectName("pageRoot")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        heading = QLabel("Backups e restauração")
        heading.setObjectName("sectionHeading" if embedded else "pageTitle")
        intro = QLabel(
            "Snapshots Restic criptografados. Antes de restaurar, escolha uma pasta nova; "
            "a biblioteca ativa nunca é substituída. Todos incluem o estado local do app, "
            "sem credenciais."
        )
        intro.setObjectName("pageDescription")
        intro.setWordWrap(True)
        root.addWidget(heading)
        root.addWidget(intro)

        self.status_label = QLabel("Verifique o repositório antes de criar ou restaurar snapshots.")
        self.status_label.setObjectName("backupStatus")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        create_section = self._section("Criar backup")
        create_form = QFormLayout()
        create_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        create_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.calibre_only = QCheckBox("Fazer backup sem os arquivos do Kindle")
        self.calibre_only.setObjectName("calibreOnlyBackup")
        self.verify_restore = QCheckBox("Verificar a restauração temporária após o backup")
        self.verify_restore.setObjectName("verifyBackupRestore")
        self.verify_restore.setChecked(True)
        create_form.addRow("Conteúdo", self.calibre_only)
        create_form.addRow("Validação", self.verify_restore)
        self.create_backup_button = QPushButton("Criar backup verificado")
        self.create_backup_button.setObjectName("createBackupButton")
        self.create_backup_button.setProperty("primary", True)
        self.create_backup_button.setEnabled(False)
        create_form.addRow("", self.create_backup_button)
        cast(QVBoxLayout, create_section.layout()).addLayout(create_form)
        root.addWidget(create_section)

        restore_section = self._section("Consultar e restaurar snapshots")
        snapshots_row = QHBoxLayout()
        self.snapshot_combo = QComboBox()
        self.snapshot_combo.setObjectName("snapshotList")
        self.snapshot_combo.setPlaceholderText("Atualize para consultar snapshots")
        self.refresh_snapshots_button = QPushButton("Atualizar snapshots")
        self.refresh_snapshots_button.setObjectName("refreshSnapshotsButton")
        snapshots_row.addWidget(self.snapshot_combo, 1)
        snapshots_row.addWidget(self.refresh_snapshots_button)
        cast(QVBoxLayout, restore_section.layout()).addLayout(snapshots_row)

        restore_form = QFormLayout()
        restore_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        restore_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.restore_target_edit = QLineEdit()
        self.restore_target_edit.setObjectName("restoreTargetEdit")
        self.restore_target_edit.setPlaceholderText("C:\\Restauracao\\reading-ecosystem")
        choose_target = QPushButton("Escolher pasta…")
        choose_target.setObjectName("chooseRestoreTargetButton")
        target_row = QWidget()
        target_layout = QHBoxLayout(target_row)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.addWidget(self.restore_target_edit, 1)
        target_layout.addWidget(choose_target)
        restore_form.addRow("Restaurar para uma pasta nova", target_row)
        self.restore_button = QPushButton("Validar e restaurar snapshot")
        self.restore_button.setObjectName("restoreSnapshotButton")
        self.restore_button.setProperty("primary", True)
        self.restore_button.setEnabled(False)
        restore_form.addRow("", self.restore_button)
        cast(QVBoxLayout, restore_section.layout()).addLayout(restore_form)
        root.addWidget(restore_section)
        root.addStretch(1)

        self.snapshot_combo.currentIndexChanged.connect(self._update_restore_enabled)
        self.calibre_only.toggled.connect(self._update_create_backup_enabled)
        self.restore_target_edit.textChanged.connect(self._update_restore_enabled)
        self.create_backup_button.clicked.connect(self.create_backup)
        self.refresh_snapshots_button.clicked.connect(self.refresh_snapshots)
        self.restore_button.clicked.connect(self.restore_snapshot)
        choose_target.clicked.connect(self.choose_restore_target)
        self.operations.progress.connect(self._on_progress)
        self.operations.completed.connect(self._on_completed)
        self.operations.failed.connect(self._on_failed)
        self.operations.busyChanged.connect(self._on_busy_changed)

    def set_device_presence(self, presence: MtpPresence) -> None:
        self._device_ready = presence.connected
        self._update_create_backup_enabled()

    @staticmethod
    def _section(title: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("contentSection")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("sectionHeading")
        layout.addWidget(heading)
        return frame

    def create_backup(self) -> None:
        if not self.calibre_only.isChecked() and not self._device_ready:
            self.status_label.setText(
                "Conecte o Kindle e deixe-o na tela USB, ou escolha incluir somente o Calibre."
            )
            return
        answer = QMessageBox.question(
            self,
            "Confirmar backup",
            "Confirme que a senha Restic está guardada em um gerenciador de senhas fora deste "
            "computador. O Kindle precisa permanecer na tela de USB durante a cópia.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        calibre_only = self.calibre_only.isChecked()
        verify = self.verify_restore.isChecked()
        self._start(
            "backup",
            "Criando snapshot criptografado…",
            lambda token: self.services.create_backup(
                calibre_only=calibre_only,
                verify_restore=verify,
                recovery_copy_confirmed=True,
            ),
        )

    def refresh_snapshots(self) -> None:
        self._start(
            "snapshots", "Consultando snapshots Restic…", lambda token: self.services.status()
        )

    def choose_restore_target(self) -> None:
        parent = QFileDialog.getExistingDirectory(self, "Selecionar pasta pai da restauração")
        if parent:
            candidate = Path(parent) / "reading-ecosystem-restore"
            self.restore_target_edit.setText(str(candidate))

    def restore_snapshot(self) -> None:
        snapshot = self.snapshot_combo.currentData()
        target = Path(self.restore_target_edit.text()).expanduser()
        if not isinstance(snapshot, str) or not target.is_absolute() or target.exists():
            self.status_label.setText(
                "Escolha um snapshot e uma pasta absoluta que ainda não exista."
            )
            return
        answer = QMessageBox.question(
            self,
            "Confirmar restauração",
            f"Restaurar o snapshot selecionado para esta pasta nova?\n\n{target}\n\n"
            "O conteúdo restaurado deve ser revisado antes de reabrir o Calibre.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start(
            "restore",
            "Restaurando e validando os arquivos…",
            lambda token: self.services.validate_restore(snapshot, target),
        )

    def _start(
        self,
        action: str,
        title: str,
        operation: Callable[[CancellationToken], object],
    ) -> None:
        self._pending_action = action
        status_key = {
            "backup": "local_backup",
            "restore": "local_restore",
        }.get(action)
        if not self.operations.start(title, operation, status_key=status_key):
            self._pending_action = None
            self.status_label.setText("Aguarde a tarefa em andamento terminar.")

    def _on_completed(self, result: object) -> None:
        action = self._pending_action
        if action is None:
            return
        self._pending_action = None
        if action == "snapshots":
            if not isinstance(result, tuple):
                self._show_error("invalid_response")
                return
            self.snapshot_combo.clear()
            for item in result:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    snapshot_id = item["id"]
                    when = item.get("time", "horário indisponível")
                    self.snapshot_combo.addItem(f"{snapshot_id[:12]} · {when}", snapshot_id)
            self.status_label.setText(
                f"{self.snapshot_combo.count()} snapshots disponíveis."
                if self.snapshot_combo.count()
                else "Nenhum snapshot encontrado."
            )
        elif action == "backup":
            snapshot_id = getattr(result, "snapshot_id", None)
            if isinstance(snapshot_id, str):
                restore_verified = "sim" if getattr(result, "restore_verified", False) else "não"
                self.status_label.setText(
                    f"Backup criado · {snapshot_id[:12]} · "
                    f"{getattr(result, 'file_count', 0)} arquivos · "
                    f"restauração verificada: {restore_verified}"
                )
                self._refresh_after_backup = True
            else:
                self._show_error("invalid_response")
        elif action == "restore":
            self.status_label.setText(
                f"Restauração validada · {getattr(result, 'file_count', 0)} arquivos. "
                "Revise a pasta antes de usar os dados."
            )

    def _on_failed(self, reason_code: str) -> None:
        if self._pending_action is None:
            return
        self._pending_action = None
        self._show_error(reason_code)

    def _show_error(self, reason_code: str) -> None:
        messages = {
            "restic_password_unavailable": "Cadastre a senha Restic em Configuração.",
            "restic_repository_unavailable": "O repositório Restic não está disponível.",
            "calibre_open": "Feche o Calibre e o servidor antes do backup.",
            "kindle_unavailable": "Conecte o Kindle por USB e deixe a tela USB aberta.",
            "multiple_kindles": "Deixe conectado somente o Kindle que deseja usar.",
            "restore_target_exists": "O destino precisa ser uma pasta nova.",
        }
        self.status_label.setText(messages.get(reason_code, f"A operação falhou ({reason_code})."))

    def _on_progress(self, title: str) -> None:
        if self._pending_action is not None:
            self.status_label.setText(title)

    def _on_busy_changed(self, busy: bool) -> None:
        self._update_create_backup_enabled()
        self.refresh_snapshots_button.setEnabled(not busy)
        self._update_restore_enabled()
        if not busy and self._refresh_after_backup:
            self._refresh_after_backup = False
            self.refresh_snapshots()

    def _update_restore_enabled(self, *_: object) -> None:
        enabled = (
            not self.operations.is_busy
            and self.snapshot_combo.currentData() is not None
            and Path(self.restore_target_edit.text()).is_absolute()
            and not Path(self.restore_target_edit.text()).exists()
        )
        self.restore_button.setEnabled(enabled)

    def _update_create_backup_enabled(self, *_: object) -> None:
        self.create_backup_button.setEnabled(
            not self.operations.is_busy and (self._device_ready or self.calibre_only.isChecked())
        )
