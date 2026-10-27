from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from koreader_ecosystem.config import AppConfig
from koreader_ecosystem.ui.operation_worker import OperationController
from koreader_ecosystem.ui.setup_page import SetupPage


class StubServices:
    def __init__(self, root: Path) -> None:
        self.config = AppConfig(
            calibre_library=root / "library",
            restic_repository=root / "repository",
            state_root=root / "state",
        )
        self.hardcover_ready = False
        self.update_calls = 0
        self.update_confirmations: list[bool] = []

    def doctor(self) -> dict[str, object]:
        return {
            "status": "needs_attention",
            "checks": {
                "calibre": {"status": "error"},
                "kindle": {"status": "error", "reason_code": "kindle_unavailable"},
                "restic": {"status": "ok"},
                "credentials": {"status": "needs_attention", "restic_password_stored": False},
            },
        }

    def update_hardcover_plugin(self, *, recovery_copy_confirmed: bool = False) -> object:
        self.update_calls += 1
        self.update_confirmations.append(recovery_copy_confirmed)
        return object()


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wait_until(app: QApplication, predicate, timeout: float = 3) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    return predicate()


def test_setup_fields_mask_secrets_and_show_path_settings(
    qapp: QApplication, tmp_path: Path
) -> None:
    page = SetupPage(StubServices(tmp_path), OperationController())  # type: ignore[arg-type]

    token = page.findChild(QLineEdit, "hardcoverTokenEdit")
    restic_password = page.findChild(QLineEdit, "resticPasswordEdit")
    calibre_path = page.findChild(QLineEdit, "calibrePathEdit")

    assert token is not None and token.echoMode() == QLineEdit.EchoMode.Password
    assert restic_password is not None
    assert restic_password.echoMode() == QLineEdit.EchoMode.Password
    assert calibre_path is not None
    assert calibre_path.text() == str(tmp_path / "library")


def test_plugin_install_is_disabled_until_token_and_kindle_are_ready(
    qapp: QApplication, tmp_path: Path
) -> None:
    page = SetupPage(StubServices(tmp_path), OperationController())  # type: ignore[arg-type]
    install = page.findChild(QPushButton, "installPluginButton")

    assert install is not None
    assert not install.isEnabled()


def test_idle_setup_does_not_enable_controls_owned_by_embedded_pages(
    qapp: QApplication, tmp_path: Path
) -> None:
    remote_section = QWidget()
    remote_layout = QVBoxLayout(remote_section)
    sync_button = QPushButton("Sincronizar snapshots")
    sync_button.setObjectName("syncRemoteButton")
    sync_button.setEnabled(False)
    remote_layout.addWidget(sync_button)
    page = SetupPage(
        StubServices(tmp_path),
        OperationController(),
        additional_sections=(remote_section,),
    )  # type: ignore[arg-type]

    page._on_busy_changed(False)

    assert not sync_button.isEnabled()


def test_safe_plugin_update_requires_kindle_and_restic_but_not_a_hardcover_token(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.ui.setup_page as setup_page

    services = StubServices(tmp_path)
    services.doctor = lambda: {
        "status": "ok",
        "checks": {
            "calibre": {"status": "ok"},
            "kindle": {"status": "ok"},
            "restic": {"status": "ok"},
            "credentials": {"status": "ok", "restic_password_stored": True},
        },
    }
    monkeypatch.setattr(setup_page, "restic_password_is_stored", lambda: False)
    page = SetupPage(services, OperationController())  # type: ignore[arg-type]
    update = page.findChild(QPushButton, "updatePluginButton")

    assert update is not None
    assert not update.isEnabled()
    page.run_diagnostics()
    assert _wait_until(qapp, update.isEnabled)
    assert not services.hardcover_ready


def test_safe_plugin_update_cancel_does_not_start_device_write(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.ui.setup_page as setup_page

    services = StubServices(tmp_path)
    services.doctor = lambda: {
        "status": "ok",
        "checks": {
            "calibre": {"status": "ok"},
            "kindle": {"status": "ok"},
            "restic": {"status": "ok"},
            "credentials": {"status": "ok", "restic_password_stored": True},
        },
    }
    monkeypatch.setattr(setup_page, "restic_password_is_stored", lambda: False)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel,
    )
    page = SetupPage(services, OperationController())  # type: ignore[arg-type]
    page.run_diagnostics()
    assert _wait_until(
        qapp,
        lambda: page.findChild(QPushButton, "updatePluginButton").isEnabled(),
    )

    page.findChild(QPushButton, "updatePluginButton").click()

    assert services.update_calls == 0


def test_safe_plugin_update_passes_confirmation_only_after_user_accepts(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.ui.setup_page as setup_page

    services = StubServices(tmp_path)
    services.doctor = lambda: {
        "status": "ok",
        "checks": {
            "calibre": {"status": "ok"},
            "kindle": {"status": "ok"},
            "restic": {"status": "ok"},
            "credentials": {"status": "ok", "restic_password_stored": True},
        },
    }
    monkeypatch.setattr(setup_page, "restic_password_is_stored", lambda: False)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    page = SetupPage(services, OperationController())  # type: ignore[arg-type]
    page.run_diagnostics()
    assert _wait_until(
        qapp,
        lambda: page.findChild(QPushButton, "updatePluginButton").isEnabled(),
    )

    page.findChild(QPushButton, "updatePluginButton").click()

    assert _wait_until(qapp, lambda: services.update_calls == 1), (
        page.diagnostic_label.text(),
        page.plugin_status.text(),
        page._pending_action,
        services.update_calls,
    )
    assert services.update_confirmations == [True]


def test_safe_plugin_update_errors_are_shown_with_plugin_status(
    qapp: QApplication, tmp_path: Path
) -> None:
    page = SetupPage(StubServices(tmp_path), OperationController())  # type: ignore[arg-type]
    page._pending_action = "plugin_update"

    page._on_failed("rollback_failed")

    assert "rollback" in page.plugin_status.text().casefold()
    assert page.diagnostic_label.text() == "Execute a verificação para conferir o que está pronto."


def test_saving_restic_password_enables_safe_update_when_kindle_is_ready(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.ui.setup_page as setup_page

    monkeypatch.setattr(setup_page, "restic_password_is_stored", lambda: False)
    page = SetupPage(StubServices(tmp_path), OperationController())  # type: ignore[arg-type]
    page._kindle_ready = True
    page._update_install_enabled()
    page._pending_action = "restic"

    page._on_completed(object())

    assert page.update_plugin_button.isEnabled()


def test_setup_diagnostic_explains_missing_prerequisites(
    qapp: QApplication, tmp_path: Path
) -> None:
    page = SetupPage(StubServices(tmp_path), OperationController())  # type: ignore[arg-type]
    check = page.findChild(QPushButton, "runDiagnosticsButton")
    assert check is not None
    check.click()

    assert _wait_until(
        qapp,
        lambda: (
            "Calibre" in page.diagnostic_label.text()
            and "Kindle" in page.diagnostic_label.text()
            and "ausente" in page.diagnostic_label.text().casefold()
        ),
    )
    assert "ausente" in page.diagnostic_label.text().casefold()
