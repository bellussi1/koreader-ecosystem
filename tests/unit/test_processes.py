from __future__ import annotations

import subprocess
from typing import Any

import pytest

from koreader_ecosystem import processes


def test_run_hidden_suppresses_console_windows_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, "ok", "")

    monkeypatch.setattr(processes.os, "name", "nt")
    monkeypatch.setattr(processes.subprocess, "run", fake_run)

    result = processes.run_hidden(["helper.exe"], capture_output=True, creationflags=0x200)

    assert result.returncode == 0
    assert captured["capture_output"] is True
    assert captured["creationflags"] == (0x200 | 0x08000000)


def test_run_hidden_preserves_platform_default_on_non_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, "ok", "")

    monkeypatch.setattr(processes.os, "name", "posix")
    monkeypatch.setattr(processes.subprocess, "run", fake_run)

    result = processes.run_hidden(["helper"], capture_output=True)

    assert result.returncode == 0
    assert "creationflags" not in captured
