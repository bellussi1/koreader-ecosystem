from __future__ import annotations

import os
import re
import subprocess
import threading
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any


class AutomationError(RuntimeError):
    """Raised when local backup automation cannot be validated or coordinated."""


_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_TASK_NAME = "KOReaderEcosystem-DailyBackup"
_LOCK_FILENAME = "backup-operation.lock"
_local_locks_guard = threading.Lock()
_local_locks: dict[Path, Any] = {}
_thread_holds = threading.local()


class _HeldFileLock:
    def __init__(self, stream: Any) -> None:
        self.stream = stream
        self.depth = 1


def validate_backup_time(value: object) -> str:
    if not isinstance(value, str) or not _TIME.fullmatch(value):
        raise AutomationError("backup_time_invalid")
    return value


class BackupAutomationSettings:
    """Opt-in preferences. Every automatic action is disabled for new profiles."""

    __slots__ = ("daily_backup_enabled", "daily_backup_time", "backup_on_kindle_connect")

    def __init__(
        self,
        daily_backup_enabled: bool = False,
        daily_backup_time: str = "20:00",
        backup_on_kindle_connect: bool = False,
    ) -> None:
        if type(daily_backup_enabled) is not bool or type(backup_on_kindle_connect) is not bool:
            raise AutomationError("backup_automation_invalid")
        self.daily_backup_enabled = daily_backup_enabled
        self.daily_backup_time = validate_backup_time(daily_backup_time)
        self.backup_on_kindle_connect = backup_on_kindle_connect

    def to_dict(self) -> dict[str, object]:
        return {
            "daily_backup_enabled": self.daily_backup_enabled,
            "daily_backup_time": self.daily_backup_time,
            "backup_on_kindle_connect": self.backup_on_kindle_connect,
        }


class ProcessOperationLock(AbstractContextManager[None]):
    """Non-blocking per-user process lock released automatically if a process exits."""

    def __init__(self, state_root: Path) -> None:
        candidate = Path(state_root).expanduser()
        if not candidate.is_absolute():
            raise AutomationError("backup_lock_unavailable")
        self.state_root = candidate.resolve()
        self.lock_path = self.state_root / "locks" / _LOCK_FILENAME
        self._stream: Any = None
        self._local_lock: Any = None
        self._held_state: _HeldFileLock | None = None

    def __enter__(self) -> None:
        with _local_locks_guard:
            local_lock = _local_locks.setdefault(self.lock_path, threading.RLock())
        if not local_lock.acquire(blocking=False):
            raise AutomationError("backup_in_progress")
        self._local_lock = local_lock

        try:
            held_locks = getattr(_thread_holds, "locks", None)
            if held_locks is None:
                held_locks = {}
                _thread_holds.locks = held_locks
            existing = held_locks.get(self.lock_path)
            if existing is not None:
                existing.depth += 1
                self._held_state = existing
                self._stream = existing.stream
                return None

            lock_directory = self.lock_path.parent
            lock_directory.mkdir(parents=True, exist_ok=True)
            if lock_directory.is_symlink() or self.lock_path.is_symlink():
                raise AutomationError("backup_lock_unavailable")
            self._stream = self.lock_path.open("a+b")
            if self.lock_path.stat().st_size == 0:
                self._stream.write(b"\0")
                self._stream.flush()
            self._stream.seek(0)
            self._lock_file()
            self._held_state = _HeldFileLock(self._stream)
            held_locks[self.lock_path] = self._held_state
            return None
        except AutomationError:
            self._release_handles()
            raise
        except OSError:
            self._release_handles()
            raise AutomationError("backup_lock_unavailable") from None

    def _lock_file(self) -> None:
        if os.name == "nt":
            import msvcrt

            self._stream.seek(0)
            try:
                msvcrt.locking(self._stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise AutomationError("backup_in_progress") from None
            return

        fcntl_module: Any = __import__("fcntl")

        try:
            fcntl_module.flock(self._stream.fileno(), fcntl_module.LOCK_EX | fcntl_module.LOCK_NB)
        except OSError:
            raise AutomationError("backup_in_progress") from None

    def __exit__(self, *_: object) -> None:
        self._release_handles()

    def _release_file_lock(self) -> None:
        if self._stream is not None:
            try:
                if os.name == "nt":
                    import msvcrt

                    self._stream.seek(0)
                    msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl_module: Any = __import__("fcntl")
                    fcntl_module.flock(self._stream.fileno(), fcntl_module.LOCK_UN)
            except OSError:
                pass

    def _release_handles(self) -> None:
        try:
            if self._held_state is not None:
                self._held_state.depth -= 1
                if self._held_state.depth == 0:
                    self._release_file_lock()
                    held_locks = getattr(_thread_holds, "locks", {})
                    if held_locks.get(self.lock_path) is self._held_state:
                        del held_locks[self.lock_path]
                    if self._stream is not None:
                        self._stream.close()
                self._held_state = None
                self._stream = None
            elif self._stream is not None:
                self._stream.close()
                self._stream = None
        finally:
            if self._local_lock is not None:
                self._local_lock.release()
                self._local_lock = None


Runner = Callable[[Sequence[str]], Any]


def _run_hidden(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    if os.name == "nt":
        return subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        shell=False,
    )


class TaskScheduler:
    """Manage one interactive, per-user Windows Task Scheduler daily backup."""

    def __init__(
        self,
        *,
        runner: Runner = _run_hidden,
        executable: Path,
        task_name: str = _TASK_NAME,
    ) -> None:
        executable_path = Path(executable).expanduser()
        if not executable_path.is_absolute() or not executable_path.name:
            raise AutomationError("scheduled_task_unavailable")
        if not isinstance(task_name, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", task_name):
            raise AutomationError("scheduled_task_unavailable")
        self.runner = runner
        self.executable = executable_path
        self.task_name = task_name

    def register_daily_backup(self, start_time: str) -> None:
        safe_time = validate_backup_time(start_time)
        target = f'"{self.executable}" --scheduled-backup'
        self._run(
            [
                "schtasks.exe",
                "/Create",
                "/SC",
                "DAILY",
                "/ST",
                safe_time,
                "/TN",
                self.task_name,
                "/TR",
                target,
                "/IT",
                "/RL",
                "LIMITED",
                "/F",
            ]
        )

    def remove_daily_backup(self) -> None:
        self._run(["schtasks.exe", "/Delete", "/TN", self.task_name, "/F"])

    def _run(self, command: list[str]) -> None:
        try:
            result = self.runner(command)
        except Exception:
            raise AutomationError("scheduled_task_unavailable") from None
        if getattr(result, "returncode", 1) != 0:
            raise AutomationError("scheduled_task_unavailable")
