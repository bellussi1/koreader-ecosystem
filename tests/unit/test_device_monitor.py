from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from koreader_ecosystem.mtp import MtpPresence
from koreader_ecosystem.ui.device_monitor import DevicePresenceMonitor


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_device_monitor_probes_off_the_gui_thread_and_stops(qapp: QApplication) -> None:
    started = threading.Event()

    def probe() -> MtpPresence:
        started.set()
        return MtpPresence(connected=True, reason_code="ready")

    monitor = DevicePresenceMonitor(probe=probe, interval_seconds=15)
    observed = QSignalSpy(monitor.presenceChanged)
    gui_thread = threading.get_ident()

    monitor.start()
    deadline = time.monotonic() + 2
    while observed.count() == 0 and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    monitor.stop()
    assert monitor.wait(2_000)

    assert started.is_set()
    assert observed.count() == 1
    assert observed.at(0)[0].connected is True
    assert threading.get_ident() == gui_thread
