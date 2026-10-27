from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from koreader_ecosystem.operation_history import OperationHistory
from koreader_ecosystem.ui.operation_worker import OperationController


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_operation_runs_off_ui_thread_and_emits_completion(qapp: QApplication) -> None:
    controller = OperationController()
    completed = QSignalSpy(controller.completed)
    caller_thread = threading.get_ident()

    accepted = controller.start("Carregando", lambda _: threading.get_ident())

    assert accepted
    assert controller.is_busy
    assert _wait_for_spy(qapp, completed)
    assert completed.at(0)[0] != caller_thread
    _wait_for_idle(qapp, controller)
    assert not controller.is_busy


def test_only_one_conflicting_operation_can_run(qapp: QApplication) -> None:
    controller = OperationController()
    completed = QSignalSpy(controller.completed)
    gate = threading.Event()

    assert controller.start("Operação 1", lambda _: gate.wait(2))
    assert not controller.start("Operação 2", lambda _: None)
    gate.set()
    assert _wait_for_spy(qapp, completed)
    _wait_for_idle(qapp, controller)


def test_failures_emit_only_safe_reason_code(qapp: QApplication) -> None:
    class PrivateError(RuntimeError):
        reason_code = "hardcover_authentication_failed"

    controller = OperationController()
    failed = QSignalSpy(controller.failed)

    assert controller.start("Verificando Hardcover", lambda _: _raise(PrivateError("secret-token")))
    assert _wait_for_spy(qapp, failed)
    rendered = str(failed.at(0)[0])

    assert rendered == "hardcover_authentication_failed"
    assert "secret-token" not in rendered


def test_status_key_persists_success_and_sanitized_failure(qapp: QApplication, tmp_path) -> None:
    history = OperationHistory(tmp_path / "operation-history.json")
    controller = OperationController(history=history)
    completed = QSignalSpy(controller.completed)

    assert controller.start(
        "Atualizando estante", lambda _: object(), status_key="hardcover_library_refresh"
    )
    assert _wait_for_spy(qapp, completed)
    _wait_for_idle(qapp, controller)
    success = history.get("hardcover_library_refresh")
    assert success is not None and success.status == "success"

    failed = QSignalSpy(controller.failed)
    assert controller.start(
        "Atualizando estante",
        lambda _: _raise(RuntimeError("private book title")),
        status_key="hardcover_library_refresh",
    )
    assert _wait_for_spy(qapp, failed)
    _wait_for_idle(qapp, controller)
    result = history.get("hardcover_library_refresh")
    assert result is not None and result.status == "failed"
    assert result.reason_code == "operation_failed"
    assert "private book title" not in history.path.read_text(encoding="utf-8")


def test_cancel_is_available_only_for_safe_cooperative_work(qapp: QApplication) -> None:
    controller = OperationController()
    cancelled = QSignalSpy(controller.cancelled)
    completed = QSignalSpy(controller.completed)
    started = threading.Event()
    gate = threading.Event()

    def cooperative(token) -> None:
        started.set()
        while not token.cancelled:
            time.sleep(0.005)
        token.raise_if_cancelled()

    def non_cancellable(_: object) -> bool:
        started.set()
        return gate.wait(2)

    assert controller.start("Backup", non_cancellable, safe_cancel=False)
    assert started.wait(1)
    assert controller.cancel_current() is False
    gate.set()
    assert _wait_for_spy(qapp, completed)
    _wait_for_idle(qapp, controller)

    started.clear()
    assert controller.start("Tarefa reversível", cooperative, safe_cancel=True)
    assert started.wait(1)
    assert controller.cancel_current()
    assert _wait_for_spy(qapp, cancelled)
    _wait_for_idle(qapp, controller)


def test_controller_waits_for_native_thread_exit_before_becoming_idle(qapp: QApplication) -> None:
    controller = OperationController()
    completed = QSignalSpy(controller.completed)
    started = threading.Event()
    release_operation = threading.Event()
    release_thread_exit = threading.Event()

    def operation(_: object) -> None:
        started.set()
        release_operation.wait(2)

    assert controller.start("Operação", operation)
    worker_thread = controller._thread
    assert worker_thread is not None

    def delay_thread_exit() -> None:
        threading.Timer(0.1, release_thread_exit.set).start()
        release_thread_exit.wait(2)

    worker_thread.finished.connect(delay_thread_exit, Qt.ConnectionType.DirectConnection)
    assert started.wait(1)
    release_operation.set()
    assert _wait_for_spy(qapp, completed)
    try:
        _wait_for_idle(qapp, controller)
        assert worker_thread.wait(0)
    finally:
        release_thread_exit.set()
        worker_thread.wait(2000)


def _raise(error: Exception) -> object:
    raise error


def _wait_for_idle(app: QApplication, controller: OperationController) -> None:
    deadline = time.monotonic() + 3
    while controller.is_busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


def _wait_for_spy(app: QApplication, spy: QSignalSpy, timeout: float = 3) -> bool:
    deadline = time.monotonic() + timeout
    while spy.count() == 0 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    return spy.count() > 0
