from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("READING_LIVE_MTP") != "1",
        reason="set READING_LIVE_MTP=1 to inspect the connected Kindle",
    ),
]


def test_live_kindle_inventory_reports_roots_without_copying() -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")

    project_root = Path(__file__).resolve().parents[2]
    script = project_root / "scripts" / "stage-kindle.ps1"
    result = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Mode",
            "inventory",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, "MTP inventory did not complete"
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 4
    assert payload["mode"] == "inventory"
    assert payload["status"] == "ok"
    assert {"documents", "koreader", "system"} <= set(payload["roots"])
    assert payload["file_count"] > 0
    assert payload["directory_count"] > 0
    assert payload["total_bytes"] > 0
