from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import (
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

from koreader_ecosystem.secrets import SecretStoreError, load_s3_credentials
from koreader_ecosystem.ui.operation_worker import CancellationToken, OperationController


class RemotePage(QWidget):
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
        self.setObjectName("pageRoot")
        self._remote_configured = bool(
            services.config.restic_remote_repository and services.config.restic_remote_region
        )
        try:
            load_s3_credentials()
            self._credentials_ready = True
        except SecretStoreError:
            self._credentials_ready = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(13)
        heading = QLabel("Backup remoto")
        heading.setObjectName("sectionHeading" if embedded else "pageTitle")
        intro = QLabel(
            "Espelhe snapshots criptografados em um destino S3. O aplicativo não cria bucket nem "
            "chaves do provedor; configure esses recursos primeiro no serviço escolhido."
        )
        intro.setObjectName("pageDescription")
        intro.setWordWrap(True)
        root.addWidget(heading)
        root.addWidget(intro)

        self.status_label = QLabel("O backup remoto não foi configurado.")
        self.status_label.setObjectName("remoteStatus")
        self.status_label.setWordWrap(True)
        if not self._remote_configured:
            self.status_label.setText(
                "Backup fora do computador adiado. Nenhum dado foi enviado; configure esta opção "
                "quando escolher e habilitar um destino remoto."
            )
        root.addWidget(self.status_label)

        destination_section = QFrame()
        destination_section.setObjectName("contentSection")
        destination_layout = QVBoxLayout(destination_section)
        destination_layout.setContentsMargins(16, 14, 16, 16)
        destination_layout.setSpacing(12)
        destination_title = QLabel("Destino S3 compatível")
        destination_title.setObjectName("sectionHeading")
        destination_layout.addWidget(destination_title)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.repository_edit = QLineEdit(services.config.restic_remote_repository or "")
        self.repository_edit.setObjectName("remoteRepositoryEdit")
        self.repository_edit.setPlaceholderText(
            "s3:https://account.r2.cloudflarestorage.com/bucket/repo"
        )
        self.region_edit = QLineEdit(services.config.restic_remote_region or "auto")
        self.region_edit.setObjectName("remoteRegionEdit")
        self.access_key_edit = QLineEdit()
        self.access_key_edit.setObjectName("s3AccessKeyEdit")
        self.access_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.access_key_edit.setPlaceholderText("Chave de acesso limitada ao bucket")
        self.secret_key_edit = QLineEdit()
        self.secret_key_edit.setObjectName("s3SecretKeyEdit")
        self.secret_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_key_edit.setPlaceholderText("Chave secreta do provedor")
        form.addRow("Repositório S3", self.repository_edit)
        form.addRow("Região", self.region_edit)
        form.addRow("Access key ID", self.access_key_edit)
        form.addRow("Secret access key", self.secret_key_edit)
        destination_layout.addLayout(form)

        self.configure_button = QPushButton("Salvar configuração remota")
        self.configure_button.setObjectName("configureRemoteButton")
        self.sync_button = QPushButton("Sincronizar snapshots criptografados")
        self.sync_button.setObjectName("syncRemoteButton")
        self.configure_button.setProperty("primary", True)
        actions = QHBoxLayout()
        actions.addWidget(self.configure_button)
        actions.addWidget(self.sync_button)
        actions.addStretch(1)
        destination_layout.addLayout(actions)
        root.addWidget(destination_section)
        root.addStretch(1)

        self.configure_button.clicked.connect(self.configure_remote)
        self.sync_button.clicked.connect(self.sync_remote)
        self.operations.progress.connect(self._on_progress)
        self.operations.completed.connect(self._on_completed)
        self.operations.failed.connect(self._on_failed)
        self.operations.busyChanged.connect(self._on_busy_changed)
        self._update_buttons()

    def configure_remote(self) -> None:
        repository = self.repository_edit.text().strip()
        region = self.region_edit.text().strip()
        access_key_id = self.access_key_edit.text()
        secret_access_key = self.secret_key_edit.text()
        if not repository or not region or not access_key_id or not secret_access_key:
            self.status_label.setText("Preencha repositório, região e as duas chaves S3.")
            return
        self.access_key_edit.clear()
        self.secret_key_edit.clear()
        self._start(
            "configure",
            "Validando e salvando a configuração remota…",
            lambda token: self.services.configure_remote(
                repository=repository,
                region=region,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
            ),
        )

    def sync_remote(self) -> None:
        if not self._remote_configured or not self._credentials_ready:
            self.status_label.setText("Configure o destino e as chaves S3 antes de sincronizar.")
            return
        answer = QMessageBox.question(
            self,
            "Enviar snapshots ao destino remoto",
            "Enviar os snapshots Restic criptografados ao destino configurado? A primeira cópia "
            "pode transferir muitos dados e consumir banda.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start(
            "sync",
            "Espelhando snapshots criptografados…",
            lambda token: self.services.sync_remote(),
        )

    def _start(
        self,
        action: str,
        title: str,
        operation: Callable[[CancellationToken], object],
    ) -> None:
        self._pending_action = action
        status_key = "remote_sync" if action == "sync" else None
        if not self.operations.start(title, operation, status_key=status_key):
            self._pending_action = None
            self.status_label.setText("Aguarde a tarefa em andamento terminar.")

    def _on_completed(self, result: object) -> None:
        action = self._pending_action
        if action is None:
            return
        self._pending_action = None
        if action == "configure":
            self._remote_configured = True
            self._credentials_ready = True
            self.status_label.setText("Destino e credenciais salvos no computador.")
        elif action == "sync":
            snapshot_count = getattr(result, "remote_snapshot_count", 0)
            self.status_label.setText(
                f"Espelhamento verificado · {snapshot_count} snapshots remotos."
            )
        self._update_buttons()

    def _on_failed(self, reason_code: str) -> None:
        if self._pending_action is None:
            return
        self._pending_action = None
        messages = {
            "invalid_configuration": "Confira o formato do endpoint S3 e da região.",
            "s3_credentials_unavailable": (
                "As chaves S3 não puderam ser salvas no Credential Manager."
            ),
            "remote_repository_unconfigured": (
                "Configure o destino antes de iniciar o espelhamento."
            ),
            "remote_repository_unavailable": "O destino remoto não está acessível.",
        }
        self.status_label.setText(messages.get(reason_code, f"A operação falhou ({reason_code})."))
        self._update_buttons()

    def _on_progress(self, title: str) -> None:
        if self._pending_action is not None:
            self.status_label.setText(title)

    def _on_busy_changed(self, busy: bool) -> None:
        self.configure_button.setEnabled(not busy)
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.sync_button.setEnabled(
            not self.operations.is_busy and self._remote_configured and self._credentials_ready
        )
