from __future__ import annotations

import re
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot

from koreader_ecosystem.operation_history import (
    OperationHistory,
    OperationHistoryError,
    OperationStatus,
)


class OperationCancelled(Exception):
    """Raised by cooperative work at a safe cancellation checkpoint."""


class CancellationToken:
    def __init__(self, progress: Callable[[str], None] | None = None) -> None:
        self._event = threading.Event()
        self._progress = progress

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise OperationCancelled

    def report_progress(self, message: str) -> None:
        if self._progress is not None and message.strip():
            self._progress(message.strip())


def safe_reason_code(error: Exception) -> str:
    reason_code = getattr(error, "reason_code", None)
    if isinstance(reason_code, str) and re.fullmatch(r"[a-z0-9_]{1,64}", reason_code):
        return reason_code
    return "operation_failed"


class _OperationWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    finished = Signal()

    def __init__(
        self,
        operation: Callable[[CancellationToken], object],
        token: CancellationToken,
        safe_cancel: bool,
    ) -> None:
        super().__init__()
        self._operation = operation
        self._token = token
        self._safe_cancel = safe_cancel

    @Slot()
    def run(self) -> None:
        try:
            self._token.raise_if_cancelled()
            result = self._operation(self._token)
            if self._safe_cancel:
                self._token.raise_if_cancelled()
            self.completed.emit(result)
        except OperationCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(safe_reason_code(error))
        finally:
            self.finished.emit()


class OperationController(QObject):
    """Run one service operation at a time without blocking the GUI thread."""

    progress = Signal(str)
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    busyChanged = Signal(bool)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        history: OperationHistory | None = None,
    ) -> None:
        super().__init__(parent)
        self.history = history
        self._thread: QThread | None = None
        self._worker: _OperationWorker | None = None
        self._token: CancellationToken | None = None
        self._is_busy = False
        self._safe_cancel = False
        self._status_key: str | None = None

    @property
    def is_busy(self) -> bool:
        return self._is_busy

    def start(
        self,
        title: str,
        operation: Callable[[CancellationToken], object],
        *,
        safe_cancel: bool = False,
        status_key: str | None = None,
    ) -> bool:
        if self._is_busy or not title.strip():
            return False
        self._status_key = status_key
        if status_key is not None and self.history is not None:
            try:
                self.history.record(key=status_key, status="running")
            except OperationHistoryError:
                self._status_key = None
        token = CancellationToken(self.progress.emit)
        thread = QThread(self)
        worker = _OperationWorker(operation, token, safe_cancel)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_thread_finished)

        self._thread = thread
        self._worker = worker
        self._token = token
        self._safe_cancel = safe_cancel
        self._is_busy = True
        self.progress.emit(title.strip())
        self.busyChanged.emit(True)
        thread.start()
        return True

    @Slot(object)
    def _on_completed(self, result: object) -> None:
        self._record_status("success")
        self.completed.emit(result)

    @Slot(str)
    def _on_failed(self, reason_code: str) -> None:
        self._record_status("failed", reason_code)
        self.failed.emit(reason_code)

    @Slot()
    def _on_cancelled(self) -> None:
        self._record_status("cancelled")
        self.cancelled.emit()

    def _record_status(self, status: OperationStatus, reason_code: str | None = None) -> None:
        if self._status_key is None or self.history is None:
            return
        try:
            self.history.record(
                key=self._status_key,
                status=status,
                reason_code=reason_code,
            )
        except OperationHistoryError:
            pass

    def cancel_current(self) -> bool:
        if not self._is_busy or not self._safe_cancel or self._token is None:
            return False
        self._token.cancel()
        return True

    @Slot()
    def _on_thread_finished(self) -> None:
        thread = self._thread
        if thread is not None and QThread.currentThread() != thread:
            # QThread.finished may arrive before native thread cleanup completes.
            # Join it before exposing idle or allowing its owner to be destroyed.
            thread.wait()
        self._worker = None
        self._thread = None
        self._token = None
        self._safe_cancel = False
        self._status_key = None
        self._is_busy = False
        self.busyChanged.emit(False)
