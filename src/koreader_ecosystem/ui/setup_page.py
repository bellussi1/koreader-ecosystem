from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from koreader_ecosystem.secrets import SecretStoreError, restic_password_is_stored
from koreader_ecosystem.ui.operation_worker import CancellationToken, OperationController


class SetupPage(QWidget):
    def __init__(
        self,
        services: Any,
        operations: OperationController,
        *,
        additional_sections: tuple[QWidget, ...] = (),
    ) -> None:
        super().__init__()
        self.services = services
        self.operations = operations
        self._additional_sections = additional_sections
        self._pending_action: str | None = None
        self._hardcover_ready = bool(getattr(services, "hardcover_ready", False))
        self._kindle_ready = False
        self._diagnostics_after_idle = False
        self._diagnostic_report: dict[str, Any] | None = None
        self.setObjectName("pageRoot")
        try:
            self._restic_ready = restic_password_is_stored()
        except SecretStoreError:
            self._restic_ready = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        heading = QLabel("Configuração")
        heading.setObjectName("pageTitle")
        intro = QLabel(
            "Ajuste os caminhos e confira as credenciais locais. Chaves são digitadas em campos "
            "mascarados e ficam no Gerenciador de Credenciais do Windows."
        )
        intro.setObjectName("pageDescription")
        intro.setWordWrap(True)
        root.addWidget(heading)
        root.addWidget(intro)

        self.diagnostic_label = QLabel("Execute a verificação para conferir o que está pronto.")
        self.diagnostic_label.setObjectName("setupDiagnostics")
        self.diagnostic_label.setWordWrap(True)
        self.diagnostic_label.setMinimumHeight(52)
        root.addWidget(self.diagnostic_label)
        self.run_diagnostics_button = QPushButton("Verificar este computador")
        self.run_diagnostics_button.setObjectName("runDiagnosticsButton")
        self.run_diagnostics_button.setProperty("primary", True)
        root.addWidget(self.run_diagnostics_button, alignment=Qt.AlignmentFlag.AlignLeft)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 4, 0, 0)
        body_layout.setSpacing(14)

        paths_frame = self._section("Pastas locais")
        paths = QFormLayout()
        paths.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        paths.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        cast(QVBoxLayout, paths_frame.layout()).addLayout(paths)
        self.calibre_path_edit = QLineEdit(str(services.config.calibre_library))
        self.calibre_path_edit.setObjectName("calibrePathEdit")
        self.restic_path_edit = QLineEdit(str(services.config.restic_repository))
        self.restic_path_edit.setObjectName("resticPathEdit")
        paths.addRow("Biblioteca Calibre", self._path_row(self.calibre_path_edit, "Calibre"))
        paths.addRow("Repositório Restic", self._path_row(self.restic_path_edit, "Restic"))
        self.save_paths_button = QPushButton("Salvar caminhos")
        self.save_paths_button.setObjectName("savePathsButton")
        paths.addRow("", self.save_paths_button)
        body_layout.addWidget(paths_frame)

        credentials_frame = self._section("Credenciais protegidas")
        credentials = QFormLayout()
        credentials.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        credentials.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        cast(QVBoxLayout, credentials_frame.layout()).addLayout(credentials)
        self.restic_password_edit = QLineEdit()
        self.restic_password_edit.setObjectName("resticPasswordEdit")
        self.restic_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.restic_password_edit.setPlaceholderText(
            "Necessária se a senha ainda não estiver no Credential Manager"
        )
        save_restic = QPushButton("Salvar senha Restic")
        save_restic.setObjectName("saveResticPasswordButton")
        credentials.addRow("Senha Restic", self._secret_row(self.restic_password_edit, save_restic))

        self.hardcover_token_edit = QLineEdit()
        self.hardcover_token_edit.setObjectName("hardcoverTokenEdit")
        self.hardcover_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.hardcover_token_edit.setPlaceholderText("Cole aqui uma chave nova do Hardcover")
        save_hardcover = QPushButton("Salvar e validar chave")
        save_hardcover.setObjectName("saveHardcoverTokenButton")
        save_hardcover.setProperty("primary", True)
        credentials.addRow(
            "Nova chave Hardcover",
            self._secret_row(self.hardcover_token_edit, save_hardcover),
        )
        self.credential_status = QLabel()
        self.credential_status.setObjectName("credentialStatus")
        self.credential_status.setWordWrap(True)
        credentials.addRow("Estado", self.credential_status)
        body_layout.addWidget(credentials_frame)

        device_frame = self._section("Kindle e KOReader")
        device_layout = QVBoxLayout()
        cast(QVBoxLayout, device_frame.layout()).addLayout(device_layout)
        device_layout.addWidget(
            QLabel(
                "Para acompanhar a leitura com o computador fechado, mantenha o KOReader ativo, "
                "vincule a edição correta, habilite o rastreamento e conecte o Kindle ao Wi-Fi. "
                "A cópia USB é usada somente para backup e instalação."
            )
        )
        self.install_plugin_button = QPushButton("Instalar integração no Kindle via USB")
        self.install_plugin_button.setObjectName("installPluginButton")
        self.install_plugin_button.setEnabled(False)
        device_layout.addWidget(self.install_plugin_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.update_plugin_button = QPushButton("Atualizar plugin existente com backup verificado")
        self.update_plugin_button.setObjectName("updatePluginButton")
        self.update_plugin_button.setEnabled(False)
        device_layout.addWidget(self.update_plugin_button, alignment=Qt.AlignmentFlag.AlignLeft)
        device_layout.addWidget(
            QLabel(
                "A atualização cria e testa um backup criptografado antes de trocar somente "
                "main.lua. A configuração atual do plugin é preservada."
            )
        )
        self.plugin_status = QLabel(
            "Instalações novas precisam de uma chave validada; a atualização segura também "
            "exige Kindle conectado, senha Restic e Calibre fechado."
        )
        self.plugin_status.setObjectName("pluginStatus")
        self.plugin_status.setWordWrap(True)
        device_layout.addWidget(self.plugin_status)
        body_layout.addWidget(device_frame)
        for section in additional_sections:
            body_layout.addWidget(section)
        body_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.setMinimumWidth(0)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self.run_diagnostics_button.clicked.connect(self.run_diagnostics)
        save_restic.clicked.connect(self.save_restic_password)
        save_hardcover.clicked.connect(self.save_hardcover_token)
        self.save_paths_button.clicked.connect(self.save_paths)
        self.install_plugin_button.clicked.connect(self.install_plugin)
        self.update_plugin_button.clicked.connect(self.update_plugin)
        self.operations.progress.connect(self._show_progress)
        self.operations.completed.connect(self._on_completed)
        self.operations.failed.connect(self._on_failed)
        self.operations.busyChanged.connect(self._on_busy_changed)
        self._update_credential_status()
        self._update_install_enabled()

    @staticmethod
    def _section(title: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("setupSection")
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(11)
        heading = QLabel(title)
        heading.setObjectName("sectionHeading")
        layout.addWidget(heading)
        return frame

    @staticmethod
    def _path_row(edit: QLineEdit, label: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        browse = QPushButton("Escolher…")
        browse.setObjectName(f"browse{label}PathButton")
        browse.clicked.connect(lambda: SetupPage._browse_path(edit))
        layout.addWidget(edit, 1)
        layout.addWidget(browse)
        return row

    @staticmethod
    def _secret_row(edit: QLineEdit, button: QPushButton) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        return row

    @staticmethod
    def _browse_path(edit: QLineEdit) -> None:
        directory = QFileDialog.getExistingDirectory(None, "Selecionar pasta", edit.text())
        if directory:
            edit.setText(directory)

    def run_diagnostics(self) -> None:
        self._start(
            "diagnostics",
            "Verificando Calibre, Kindle e Restic…",
            lambda _: self.services.doctor(),
        )

    def save_paths(self) -> None:
        calibre = Path(self.calibre_path_edit.text()).expanduser()
        repository = Path(self.restic_path_edit.text()).expanduser()
        if not calibre.is_absolute() or not repository.is_absolute():
            self.diagnostic_label.setText("Informe caminhos absolutos para as duas pastas.")
            return
        self._start(
            "paths",
            "Salvando caminhos…",
            lambda token: self.services.update_local_paths(
                calibre_library=calibre,
                restic_repository=repository,
            ),
        )

    def save_restic_password(self) -> None:
        password = self.restic_password_edit.text()
        if not password.strip():
            self.diagnostic_label.setText("Digite a senha Restic no campo mascarado.")
            return
        self.restic_password_edit.clear()
        self._start(
            "restic",
            "Salvando senha no Gerenciador de Credenciais…",
            lambda token: self.services.configure_restic_password(password),
        )

    def save_hardcover_token(self) -> None:
        token = self.hardcover_token_edit.text()
        if not token:
            self.diagnostic_label.setText("Digite uma chave nova do Hardcover no campo mascarado.")
            return
        self.hardcover_token_edit.clear()
        self._start(
            "hardcover",
            "Salvando e validando a chave do Hardcover…",
            lambda cancellation: self.services.configure_hardcover(token),
        )

    def install_plugin(self) -> None:
        if not self._hardcover_ready or not self._kindle_ready:
            return
        answer = QMessageBox.question(
            self,
            "Instalar no Kindle",
            "O plugin precisa guardar a chave do Hardcover em um arquivo de configuração "
            "texto simples no Kindle. O aparelho está conectado por USB e parado na tela de "
            "conexão?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start(
            "install",
            "Preparando e verificando a integração KOReader…",
            lambda token: self.services.install_hardcover_plugin(),
        )

    def update_plugin(self) -> None:
        if not self._kindle_ready or not self._restic_ready:
            return
        answer = QMessageBox.question(
            self,
            "Atualizar plugin no Kindle",
            "O executável fará um backup criptografado da biblioteca Calibre e do Kindle, "
            "verificará o snapshot e uma restauração temporária, guardará os arquivos Lua "
            "alterados e só então atualizará o plugin com conferência SHA-256. Confirme que "
            "o Calibre está fechado, "
            "o Kindle está parado na tela USB e a senha Restic também está guardada fora deste "
            "computador.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start(
            "plugin_update",
            "Criando e validando backup antes de atualizar o plugin…",
            lambda _: self.services.update_hardcover_plugin(recovery_copy_confirmed=True),
        )

    def _start(
        self, action: str, title: str, operation: Callable[[CancellationToken], object]
    ) -> None:
        self._pending_action = action
        if not self.operations.start(title, operation):
            self._pending_action = None
            self.diagnostic_label.setText("Aguarde a tarefa em andamento terminar.")

    def _on_completed(self, result: object) -> None:
        action = self._pending_action
        if action is None:
            return
        self._pending_action = None
        if action == "diagnostics":
            if not isinstance(result, dict):
                self.diagnostic_label.setText("O diagnóstico retornou uma resposta inválida.")
                return
            self._show_diagnostics(result)
        elif action == "hardcover":
            self._hardcover_ready = True
            self.plugin_status.setText("Chave nova validada com sucesso no Hardcover.")
            self._update_credential_status()
            self._update_install_enabled()
            self._diagnostics_after_idle = True
        elif action == "restic":
            self._restic_ready = True
            self.diagnostic_label.setText("Senha salva e conferida no Gerenciador de Credenciais.")
            self._update_credential_status()
            self._update_install_enabled()
        elif action == "paths":
            self.diagnostic_label.setText("Caminhos locais salvos.")
        elif action == "install":
            self.plugin_status.setText("Instalação concluída e arquivos conferidos por hash.")
        elif action == "plugin_update":
            snapshot_id = getattr(result, "restic_snapshot_id", None)
            rollback_id = getattr(result, "rollback_backup_id", None)
            if getattr(result, "updated", False):
                self.plugin_status.setText(
                    f"Plugin atualizado; backup Restic {str(snapshot_id)[:12]} verificado. "
                    f"{getattr(result, 'file_count', 0)} arquivos conferidos; "
                    f"rollback local preservado ({rollback_id})."
                )
            else:
                self.plugin_status.setText(
                    f"Plugin já estava atualizado; backup Restic "
                    f"{str(snapshot_id)[:12]} verificado."
                )

    def _on_failed(self, reason_code: str) -> None:
        if self._pending_action is None:
            return
        action = self._pending_action
        self._pending_action = None
        message = {
            "hardcover_authentication_failed": (
                "A chave não foi validada. Confira e tente novamente."
            ),
            "hardcover_token_unavailable": "A chave não pôde ser salva no Credential Manager.",
            "restic_password_unavailable": "A senha não pôde ser salva no Credential Manager.",
            "kindle_unavailable": "Conecte o Kindle por USB e deixe a tela USB aberta.",
            "multiple_kindles": "Há mais de um dispositivo conectado; deixe apenas o Kindle alvo.",
            "destination_exists": "A pasta do plugin já existe. O instalador não a substituiu.",
            "plugin_update_backup_unverified": (
                "O backup e a restauração temporária não foram validados; "
                "o plugin não foi alterado."
            ),
            "plugin_not_installed": "Não foi encontrada uma instalação existente do plugin.",
            "plugin_version_mismatch": (
                "A versão instalada é diferente da esperada; nenhuma alteração foi feita."
            ),
            "rollback_failed": (
                "A atualização não foi confirmada e o rollback não pôde ser verificado. "
                "Mantenha o Kindle conectado e não abra o KOReader."
            ),
        }.get(reason_code, f"A operação falhou ({reason_code}).")
        if action in {"hardcover", "restic"}:
            self._update_credential_status()
        if action in {"install", "plugin_update"}:
            self.plugin_status.setText(message)
        else:
            self.diagnostic_label.setText(message)

    def _show_diagnostics(self, report: dict[str, Any]) -> None:
        checks = report.get("checks")
        if not isinstance(checks, dict):
            self.diagnostic_label.setText("O diagnóstico retornou uma resposta inválida.")
            return
        self._diagnostic_report = report
        calibre_ok = self._check_ok(checks, "calibre")
        kindle_ok = self._check_ok(checks, "kindle")
        restic_ok = self._check_ok(checks, "restic")
        credentials = checks.get("credentials")
        self._restic_ready = bool(
            isinstance(credentials, dict) and credentials.get("restic_password_stored") is True
        )
        self._kindle_ready = kindle_ok
        lines = [
            f"Calibre: {'pronto' if calibre_ok else 'ausente ou fechado incorretamente'}",
            f"Kindle USB: {'pronto' if kindle_ok else 'ausente ou indisponível'}",
            f"Restic: {'pronto' if restic_ok else 'instale ou ajuste o Restic'}",
            f"Senha Restic: {'configurada' if self._restic_ready else 'ausente'}",
            "Hardcover: "
            + ("chave nova validada" if self._hardcover_ready else "cadastre uma chave nova"),
        ]
        self.diagnostic_label.setText("\n".join(lines))
        self._update_credential_status()
        self._update_install_enabled()

    @staticmethod
    def _check_ok(checks: dict[str, Any], name: str) -> bool:
        check = checks.get(name)
        return isinstance(check, dict) and check.get("status") == "ok"

    def _update_credential_status(self) -> None:
        self.credential_status.setText(
            f"Senha Restic: {'configurada' if self._restic_ready else 'ausente'} · "
            "Hardcover: "
            + ("nova chave validada" if self._hardcover_ready else "precisa de chave nova")
        )

    def _update_install_enabled(self) -> None:
        self.install_plugin_button.setEnabled(self._hardcover_ready and self._kindle_ready)
        self.update_plugin_button.setEnabled(self._restic_ready and self._kindle_ready)

    def _show_progress(self, title: str) -> None:
        if self._pending_action is not None:
            self.diagnostic_label.setText(title)

    def _on_busy_changed(self, busy: bool) -> None:
        for button in self.findChildren(QPushButton):
            if any(section.isAncestorOf(button) for section in self._additional_sections):
                continue
            button.setEnabled(not busy)
        if not busy:
            self._update_install_enabled()
            if self._diagnostics_after_idle:
                self._diagnostics_after_idle = False
                self.run_diagnostics()
