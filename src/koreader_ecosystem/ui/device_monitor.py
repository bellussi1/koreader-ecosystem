from __future__ import annotations

from collections.abc import Callable
from threading import Event

from PySide6.QtCore import QObject, QThread, Signal

from koreader_ecosystem.mtp import MtpError, MtpPresence, probe_kindle


class DevicePresenceMonitor(QThread):
    """Poll Kindle MTP readiness without walking the device tree or blocking Qt."""

    presenceChanged = Signal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        probe: Callable[[], MtpPresence] = probe_kindle,
        interval_seconds: float = 15,
    ) -> None:
        super().__init__(parent)
        self._probe = probe
        self._interval_seconds = max(1.0, interval_seconds)
        self._stop_event = Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                result = self._probe()
            except MtpError:
                result = MtpPresence(connected=False, reason_code="check_failed")
            self.presenceChanged.emit(result)
            if self._stop_event.wait(self._interval_seconds):
                break

    def stop(self) -> None:
        self._stop_event.set()
