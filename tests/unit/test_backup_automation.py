from __future__ import annotations

import multiprocessing
import threading
import time
from pathlib import Path

import pytest

from koreader_ecosystem.backup_automation import (
    AutomationError,
    BackupAutomationSettings,
    ProcessOperationLock,
    TaskScheduler,
    validate_backup_time,
)


def _hold_operation_lock(root: str, entered: multiprocessing.synchronize.Event) -> None:
    with ProcessOperationLock(Path(root)):
        entered.set()
        time.sleep(30)


def test_backup_automation_is_disabled_by_default() -> None:
    settings = BackupAutomationSettings()

    assert settings.daily_backup_enabled is False
    assert settings.backup_on_kindle_connect is False
    assert settings.daily_backup_time == "20:00"


@pytest.mark.parametrize("value", ["00:00", "09:05", "23:59"])
def test_validate_backup_time_accepts_twenty_four_hour_time(value: str) -> None:
    assert validate_backup_time(value) == value


@pytest.mark.parametrize("value", ["24:00", "8:05", "08:5", "08:05:00", " 08:05"])
def test_validate_backup_time_rejects_noncanonical_or_invalid_time(value: str) -> None:
    with pytest.raises(AutomationError, match="time"):
        validate_backup_time(value)


def test_task_scheduler_registers_interactive_daily_task_without_password() -> None:
    calls: list[list[str]] = []
    scheduler = TaskScheduler(
        runner=lambda args: calls.append(args) or _completed(0),
        executable=Path("C:/Apps/KOReader Ecosystem/KOReaderEcosystem.exe"),
    )

    scheduler.register_daily_backup("06:30")

    assert calls == [
        [
            "schtasks.exe",
            "/Create",
            "/SC",
            "DAILY",
            "/ST",
            "06:30",
            "/TN",
            "KOReaderEcosystem-DailyBackup",
            "/TR",
            '"C:\\Apps\\KOReader Ecosystem\\KOReaderEcosystem.exe" --scheduled-backup',
            "/IT",
            "/RL",
            "LIMITED",
            "/F",
        ]
    ]
    assert not any("/RP" in argument for argument in calls[0])


def test_task_scheduler_removes_future_runs_without_ending_running_task() -> None:
    calls: list[list[str]] = []
    scheduler = TaskScheduler(
        runner=lambda args: calls.append(args) or _completed(0),
        executable=Path("C:/Apps/KOReaderEcosystem/KOReaderEcosystem.exe"),
    )

    scheduler.remove_daily_backup()

    assert calls == [["schtasks.exe", "/Delete", "/TN", "KOReaderEcosystem-DailyBackup", "/F"]]
    assert all("/End" not in argument for argument in calls[0])


def test_process_lock_excludes_a_second_process(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    process = context.Process(target=_hold_operation_lock, args=(str(tmp_path), entered))
    process.start()
    try:
        assert entered.wait(10)
        with pytest.raises(AutomationError, match="backup_in_progress"):
            with ProcessOperationLock(tmp_path):
                pass
    finally:
        process.terminate()
        process.join(10)


def test_process_lock_allows_nested_backup_calls_but_stays_exclusive_until_outer_exit(
    tmp_path: Path,
) -> None:
    with ProcessOperationLock(tmp_path):
        with ProcessOperationLock(tmp_path):
            pass

        attempted = threading.Event()
        results: list[str] = []

        def try_from_another_thread() -> None:
            attempted.set()
            try:
                with ProcessOperationLock(tmp_path):
                    results.append("acquired")
            except AutomationError as error:
                results.append(str(error))

        thread = threading.Thread(target=try_from_another_thread)
        thread.start()
        assert attempted.wait(2)
        thread.join(2)
        assert not thread.is_alive()
        assert results == ["backup_in_progress"]

    with ProcessOperationLock(tmp_path):
        pass


def test_process_lock_is_released_after_the_owner_exits(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    process = context.Process(target=_hold_operation_lock, args=(str(tmp_path), entered))
    process.start()
    assert entered.wait(10)
    process.terminate()
    process.join(10)

    with ProcessOperationLock(tmp_path):
        pass


def test_scheduler_failure_does_not_leak_command_output() -> None:
    scheduler = TaskScheduler(
        runner=lambda _args: _completed(1, stderr="user-specific task detail"),
        executable=Path("C:/Apps/KOReaderEcosystem-Service.exe"),
    )

    with pytest.raises(AutomationError) as error:
        scheduler.register_daily_backup("20:00")

    assert str(error.value) == "scheduled_task_unavailable"
    assert "user-specific" not in str(error.value)


def _completed(returncode: int, *, stderr: str = "") -> object:
    return type("Completed", (), {"returncode": returncode, "stderr": stderr})()
