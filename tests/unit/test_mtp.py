from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import koreader_ecosystem.mtp as mtp_module
from koreader_ecosystem.mtp import (
    MtpAmbiguousError,
    MtpCopyError,
    MtpError,
    MtpPresence,
    MtpReadingStageSummary,
    MtpUnavailableError,
    detect_kindle,
    probe_kindle,
    stage_kindle,
    stage_koreader_reading_data,
)


def _payload(mode: str, **changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 4,
        "mode": mode,
        "status": "ok",
        "reason_code": None,
        "device_count": 1,
        "storage_count": 1,
        "roots": ["documents", "koreader", "system"],
        "file_count": 4,
        "directory_count": 3,
        "total_bytes": 32,
    }
    if mode == "stage":
        payload["content_verified"] = True
        payload["content_verification_method"] = "wpd-stream-sha256"
        payload["source_paths_verified"] = True
        payload["source_sha256"] = {
            "koreader/config.lua": (
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
            )
        }
    if mode == "presence":
        payload["roots"] = []
        payload["file_count"] = 0
        payload["directory_count"] = 0
        payload["total_bytes"] = 0
    payload.update(changes)
    if mode == "stage" and "source_directories" not in changes:
        payload["source_directories"] = list(payload["roots"])
    return payload


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell integration")
def test_stage_adapter_reports_relative_destination_with_windows_powershell() -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is not available")

    script = Path(__file__).resolve().parents[2] / "scripts" / "stage-kindle.ps1"
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
            "stage",
            "-Destination",
            "relative-stage-destination",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["reason_code"] == "invalid_destination"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell WPD helper")
def test_wpd_readback_rejects_mismatched_inputs_with_a_safe_diagnostic() -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is not available")

    helper = Path(__file__).resolve().parents[2] / "scripts" / "WpdReadback.cs"
    command = (
        "$ErrorActionPreference='Stop'; "
        f"Add-Type -Path '{helper}'; "
        "[KreWpdReadback]::ReadFiles([string[]]@('documents\\sample.epub'), "
        "[System.UInt64[]]@()) | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["Status"] == "source_changed"
    assert payload["DiagnosticCode"] == "input_mismatch"
    assert payload["DiagnosticIndex"] == -1
    assert payload["DiagnosticDepth"] == -1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell WPD helper")
def test_wpd_name_comparison_recognizes_unicode_canonical_equivalence() -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is not available")

    helper = Path(__file__).resolve().parents[2] / "scripts" / "WpdReadback.cs"
    command = (
        "$ErrorActionPreference='Stop'; "
        f"Add-Type -Path '{helper}'; "
        "[KreWpdReadback]::NamesEquivalent(('Caf' + [char]0x00E9), "
        "('Cafe' + [char]0x0301)) | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell WPD helper")
def test_wpd_readback_skips_zero_byte_files_but_reads_nonempty_files() -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is not available")

    helper = Path(__file__).resolve().parents[2] / "scripts" / "WpdReadback.cs"
    command = (
        "$ErrorActionPreference='Stop'; "
        f"Add-Type -Path '{helper}'; "
        "[pscustomobject]@{empty=[KreWpdReadback]::RequiresWpdReadback("
        "'koreader\\empty.klld',[System.UInt64]0); "
        "nonempty=[KreWpdReadback]::RequiresWpdReadback("
        "'koreader\\reader.lua',[System.UInt64]1); "
        "document=[KreWpdReadback]::RequiresWpdReadback("
        "'documents\\book.epub',[System.UInt64]1)} | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "empty": False,
        "nonempty": True,
        "document": False,
    }


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell WPD helper")
def test_wpd_device_refresh_happens_before_device_enumeration() -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is not available")

    helper = Path(__file__).resolve().parents[2] / "scripts" / "WpdReadback.cs"
    command = (
        "$ErrorActionPreference='Stop'; "
        f"Add-Type -Path '{helper}'; "
        "$script:order = [System.Collections.Generic.List[string]]::new(); "
        "$refresh = [System.Func[int]] { [void]$script:order.Add('refresh'); return 0 }; "
        "$enumerate = [System.Func[string]] { [void]$script:order.Add('enumerate'); "
        "return 'kindle' }; "
        "$result = [KreWpdReadback]::AfterDeviceListRefresh($refresh, $enumerate); "
        "[pscustomobject]@{ result = $result; order = @($script:order) } | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "result": "kindle",
        "order": ["refresh", "enumerate"],
    }


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell WPD helper")
def test_wpd_device_discovery_retries_only_after_an_empty_scan() -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is not available")

    helper = Path(__file__).resolve().parents[2] / "scripts" / "WpdReadback.cs"
    command = (
        "$ErrorActionPreference='Stop'; "
        f"Add-Type -Path '{helper}'; "
        "$script:attempt = 0; "
        "$script:order = [System.Collections.Generic.List[string]]::new(); "
        "$refresh = [System.Func[int]] { [void]$script:order.Add('refresh'); return 0 }; "
        "$enumerate = [System.Func[string]] { "
        "[void]$script:order.Add('enumerate'); $script:attempt++; "
        "if ($script:attempt -eq 1) { return '' }; return 'kindle' }; "
        "$hasDevice = [System.Func[string,bool]] { param($value) "
        "return -not [string]::IsNullOrWhiteSpace($value) }; "
        "$result = [KreWpdReadback]::RetryAfterDeviceListRefresh("
        "$refresh, $enumerate, $hasDevice, 3, 1); "
        "[pscustomobject]@{ result = $result; attempts = $script:attempt; "
        "order = @($script:order) } | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "result": "kindle",
        "attempts": 2,
        "order": ["refresh", "enumerate", "refresh", "enumerate"],
    }


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell WPD helper")
def test_wpd_helper_routes_document_content_to_shell_readback() -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is not available")

    helper = Path(__file__).resolve().parents[2] / "scripts" / "WpdReadback.cs"
    command = (
        "$ErrorActionPreference='Stop'; "
        f"Add-Type -Path '{helper}'; "
        r"[pscustomobject]@{document=[KreWpdReadback]::RequiresShellReadback("
        r"'documents\Book.epub'); "
        r"sidecar=[KreWpdReadback]::RequiresShellReadback("
        r"'koreader\Book.sdr\metadata.epub.lua'); "
        r"system=[KreWpdReadback]::RequiresShellReadback("
        r"'system\thumbnails\cover.jpg'); "
        + r"other=[KreWpdReadback]::RequiresShellReadback('audible\file.bin')} | "
        "ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "document": True,
        "sidecar": True,
        "system": False,
        "other": True,
    }


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell WPD helper")
def test_wpd_path_mismatch_can_fall_back_to_shell_readback() -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is not available")

    helper = Path(__file__).resolve().parents[2] / "scripts" / "WpdReadback.cs"
    command = (
        "$ErrorActionPreference='Stop'; "
        f"Add-Type -Path '{helper}'; "
        "[pscustomobject]@{file=[KreWpdReadback]::IsPathResolutionFailure('file_name_unmatched'); "
        "folder=[KreWpdReadback]::IsPathResolutionFailure('folder_unmatched'); "
        "stream=[KreWpdReadback]::IsPathResolutionFailure('resource_stream'); "
        "device=[KreWpdReadback]::IsPathResolutionFailure('device_not_found')} | "
        "ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "file": True,
        "folder": True,
        "stream": False,
        "device": False,
    }


@pytest.mark.parametrize(
    "method",
    [
        "wpd-stream-sha256",
        "shell-content-readback-sha256",
        "empty-file-size",
        "wpd-stream-sha256+shell-content-readback-sha256",
        "wpd-stream-sha256+empty-file-size",
        "shell-content-readback-sha256+empty-file-size",
        "wpd-stream-sha256+shell-content-readback-sha256+empty-file-size",
    ],
)
def test_stage_kindle_accepts_reported_verification_methods(tmp_path: Path, method: str) -> None:
    payload = _payload(
        "stage",
        roots=["koreader"],
        file_count=1,
        directory_count=1,
        total_bytes=3,
        content_verification_method=method,
    )

    result = stage_kindle(tmp_path / "verified-method", runner=_runner(payload))

    assert result.file_count == 1


def _runner(payload: dict[str, Any] | str, returncode: int = 0):
    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "-Destination" in args:
            destination = Path(args[args.index("-Destination") + 1])
            (destination / "koreader").mkdir(parents=True)
            (destination / "koreader" / "config.lua").write_bytes(b"abc")
        output = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.CompletedProcess(args, returncode, output, "")

    return run


def _stage_runner(
    payload: dict[str, Any],
    files: dict[str, bytes],
    *,
    directories: tuple[str, ...] = (),
):
    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        destination = Path(args[args.index("-Destination") + 1])
        for relative_path in directories:
            destination.joinpath(*relative_path.split("/")).mkdir(parents=True, exist_ok=True)
        for relative_path, content in files.items():
            path = destination.joinpath(*relative_path.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    return run


def _reading_runner(
    files: dict[str, bytes],
    *,
    source_hashes: dict[str, str] | None = None,
    changes: dict[str, Any] | None = None,
):
    payload: dict[str, Any] = {
        "schema_version": 4,
        "mode": "stage-readings",
        "status": "ok",
        "reason_code": None,
        "device_count": 1,
        "storage_count": 1,
        "roots": sorted({path.split("/")[0] for path in files}),
        "file_count": len(files),
        "directory_count": len(
            {
                "/".join(path.split("/")[:index])
                for path in files
                for index in range(1, len(path.split("/")))
            }
        ),
        "total_bytes": sum(len(content) for content in files.values()),
        "content_verified": True,
        "content_verification_method": "wpd-stream-sha256",
        "source_paths_verified": True,
        "source_sha256": source_hashes
        if source_hashes is not None
        else {path: hashlib.sha256(content).hexdigest() for path, content in files.items()},
        "source_directories": sorted(
            {
                "/".join(path.split("/")[:index])
                for path in files
                for index in range(1, len(path.split("/")))
            }
        ),
    }
    payload.update(changes or {})

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert args[args.index("-Mode") + 1] == "stage-readings"
        destination = Path(args[args.index("-Destination") + 1])
        for relative_path, content in files.items():
            path = destination.joinpath(*relative_path.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    return run


def test_mtp_adapter_decodes_unicode_json_as_utf8() -> None:
    payload = {
        "schema_version": 4,
        "mode": "inventory",
        "status": "ok",
        "reason_code": None,
        "roots": ["Tolstói"],
    }
    captured: dict[str, Any] = {}

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        raw_stdout = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        stdout = raw_stdout.decode(
            kwargs.get("encoding") or "ascii",
            errors=kwargs.get("errors") or "strict",
        )
        return subprocess.CompletedProcess(args, 0, stdout, "")

    result = mtp_module._run_adapter("inventory", runner)

    assert result["roots"] == ["Tolstói"]
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "strict"


def test_detect_kindle_parses_sanitized_inventory() -> None:
    summary = detect_kindle(
        runner=_runner(
            _payload(
                "inventory",
                roots=["documents", "koreader", "system"],
                file_count=4,
                directory_count=118,
                total_bytes=4096,
            )
        )
    )

    assert summary.roots == ("documents", "koreader", "system")
    assert summary.file_count == 4
    assert summary.directory_count == 118
    assert summary.total_bytes == 4096


def test_detect_kindle_reports_disconnected_device() -> None:
    with pytest.raises(MtpUnavailableError):
        detect_kindle(
            runner=_runner(
                _payload(
                    "inventory",
                    status="error",
                    reason_code="device_not_found",
                    device_count=0,
                    storage_count=0,
                    roots=[],
                    file_count=0,
                    directory_count=0,
                    total_bytes=0,
                ),
                returncode=2,
            )
        )


def test_detect_kindle_reports_ambiguous_devices() -> None:
    with pytest.raises(MtpAmbiguousError):
        detect_kindle(
            runner=_runner(
                _payload(
                    "inventory",
                    status="error",
                    reason_code="multiple_devices",
                    device_count=2,
                    storage_count=0,
                    roots=[],
                    file_count=0,
                    directory_count=0,
                    total_bytes=0,
                ),
                returncode=3,
            )
        )


def test_detect_kindle_rejects_invalid_json() -> None:
    with pytest.raises(MtpError):
        detect_kindle(runner=_runner("not-json"))


def test_probe_kindle_presence_does_not_require_tree_inventory() -> None:
    presence = probe_kindle(
        runner=_runner(
            _payload(
                "presence",
                roots=[],
                file_count=0,
                directory_count=0,
                total_bytes=0,
            )
        )
    )

    assert presence == MtpPresence(connected=True, reason_code="ready")


@pytest.mark.parametrize(
    ("reason_code", "device_count", "storage_count"),
    [
        ("device_not_found", 0, 0),
        ("multiple_devices", 2, 0),
        ("storage_not_found", 1, 0),
    ],
)
def test_probe_kindle_presence_reports_sanitized_unavailable_states(
    reason_code: str, device_count: int, storage_count: int
) -> None:
    presence = probe_kindle(
        runner=_runner(
            _payload(
                "presence",
                status="error",
                reason_code=reason_code,
                device_count=device_count,
                storage_count=storage_count,
                roots=[],
                file_count=0,
                directory_count=0,
                total_bytes=0,
            ),
            returncode=2,
        )
    )

    assert presence == MtpPresence(connected=False, reason_code=reason_code)


def test_stage_kindle_returns_manifest_when_counts_match(tmp_path: Path) -> None:
    destination = tmp_path / "stage"
    summary = stage_kindle(
        destination,
        runner=_runner(
            _payload(
                "stage",
                roots=["koreader"],
                file_count=1,
                directory_count=1,
                total_bytes=3,
            )
        ),
    )

    assert summary.destination == destination
    assert summary.roots == ("koreader",)
    assert summary.file_count == 1
    assert summary.directory_count == 1
    assert summary.total_bytes == 3
    assert tuple(entry.path for entry in summary.manifest) == ("koreader", "koreader/config.lua")


def test_stage_kindle_rejects_count_or_byte_mismatch(tmp_path: Path) -> None:
    with pytest.raises(MtpCopyError):
        stage_kindle(
            tmp_path / "partial",
            runner=_runner(
                _payload(
                    "stage",
                    roots=["koreader"],
                    file_count=2,
                    directory_count=1,
                    total_bytes=3,
                )
            ),
        )


@pytest.mark.parametrize("content_verified", [None, False])
def test_stage_kindle_rejects_a_copy_without_source_content_verification(
    tmp_path: Path, content_verified: bool | None
) -> None:
    payload = _payload("stage", roots=["koreader"], file_count=1, directory_count=1, total_bytes=3)
    if content_verified is None:
        payload.pop("content_verified")
    else:
        payload["content_verified"] = content_verified

    with pytest.raises(MtpCopyError):
        stage_kindle(tmp_path / "unverified", runner=_runner(payload))


@pytest.mark.parametrize("method", [None, "shell-copyhere-sha256"])
def test_stage_kindle_rejects_without_wpd_stream_verification(
    tmp_path: Path, method: str | None
) -> None:
    payload = _payload("stage", roots=["koreader"], file_count=1, directory_count=1, total_bytes=3)
    if method is None:
        payload.pop("content_verification_method")
    else:
        payload["content_verification_method"] = method

    with pytest.raises(MtpCopyError):
        stage_kindle(tmp_path / "shell-readback", runner=_runner(payload))


@pytest.mark.parametrize("source_paths_verified", [None, False])
def test_stage_kindle_rejects_a_copy_without_source_path_attestation(
    tmp_path: Path, source_paths_verified: bool | None
) -> None:
    payload = _payload("stage", roots=["koreader"], file_count=1, directory_count=1, total_bytes=3)
    if source_paths_verified is None:
        payload.pop("source_paths_verified")
    else:
        payload["source_paths_verified"] = source_paths_verified

    with pytest.raises(MtpCopyError):
        stage_kindle(tmp_path / "unattested-paths", runner=_runner(payload))


def test_stage_kindle_rejects_a_source_hash_that_differs_from_the_staging(
    tmp_path: Path,
) -> None:
    payload = _payload("stage", roots=["koreader"], file_count=1, directory_count=1, total_bytes=3)
    payload["source_sha256"] = {"koreader/config.lua": "0" * 64}

    with pytest.raises(MtpCopyError):
        stage_kindle(tmp_path / "mismatched", runner=_runner(payload))


def test_stage_kindle_accepts_verified_hashes_with_windows_path_separators(
    tmp_path: Path,
) -> None:
    payload = _payload("stage", roots=["koreader"], file_count=1, directory_count=1, total_bytes=3)
    payload["source_sha256"] = {
        r"koreader\config.lua": ("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    }

    result = stage_kindle(tmp_path / "windows-paths", runner=_runner(payload))

    assert result.file_count == 1


def test_stage_kindle_restores_source_case_after_windows_copy_changes_it(tmp_path: Path) -> None:
    payload = _payload(
        "stage",
        roots=["koreader"],
        file_count=1,
        directory_count=1,
        total_bytes=3,
        source_sha256={
            "koreader/CONFIG.LUA": (
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
            )
        },
    )

    result = stage_kindle(tmp_path / "windows-case", runner=_runner(payload))

    assert tuple(entry.path for entry in result.manifest) == ("koreader", "koreader/CONFIG.LUA")


def test_stage_kindle_restores_compatibility_normalized_source_path(tmp_path: Path) -> None:
    source_path = "koreader/\uff23\uff2f\uff2e\uff26\uff29\uff27.lua"
    payload = _payload(
        "stage",
        roots=["koreader"],
        file_count=1,
        directory_count=1,
        total_bytes=3,
        source_sha256={
            source_path: ("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        },
    )

    result = stage_kindle(tmp_path / "compatibility-normalization", runner=_runner(payload))

    assert tuple(entry.path for entry in result.manifest) == ("koreader", source_path)
    assert (result.destination / source_path).read_bytes() == b"abc"


def test_stage_kindle_maps_a_unique_file_through_a_renamed_directory(tmp_path: Path) -> None:
    source_path = "koreader/source/reading.lua"
    digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    payload = _payload(
        "stage",
        roots=["koreader"],
        file_count=1,
        directory_count=2,
        total_bytes=3,
        source_directories=["koreader", "koreader/source"],
        source_sha256={source_path: digest},
    )

    result = stage_kindle(
        tmp_path / "renamed-directory",
        runner=_stage_runner(payload, {"koreader/copy/renamed.lua": b"abc"}),
    )

    assert tuple(entry.path for entry in result.manifest) == (
        "koreader",
        "koreader/source",
        source_path,
    )
    assert (result.destination / source_path).read_bytes() == b"abc"


def test_stage_kindle_rejects_ambiguous_duplicate_hashes_in_renamed_directories(
    tmp_path: Path,
) -> None:
    digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    payload = _payload(
        "stage",
        roots=["koreader"],
        file_count=2,
        directory_count=3,
        total_bytes=6,
        source_directories=["koreader", "koreader/first", "koreader/second"],
        source_sha256={
            "koreader/first/reading.lua": digest,
            "koreader/second/reading.lua": digest,
        },
    )

    with pytest.raises(MtpCopyError):
        stage_kindle(
            tmp_path / "ambiguous-renamed-directories",
            runner=_stage_runner(
                payload,
                {
                    "koreader/copy-a/one.lua": b"abc",
                    "koreader/copy-b/two.lua": b"abc",
                },
            ),
        )


def test_stage_kindle_uses_attested_path_map_for_duplicate_directory_contents(
    tmp_path: Path,
) -> None:
    digest = hashlib.sha256(b"abc").hexdigest()
    payload = _payload(
        "stage",
        roots=["koreader"],
        file_count=2,
        directory_count=3,
        total_bytes=6,
        source_directories=["koreader", "koreader/first", "koreader/second"],
        source_sha256={
            "koreader/first/reading.lua": digest,
            "koreader/second/reading.lua": digest,
        },
        source_path_map={
            "koreader": "koreader",
            "koreader/first": "koreader/copy-a",
            "koreader/first/reading.lua": "koreader/copy-a/one.lua",
            "koreader/second": "koreader/copy-b",
            "koreader/second/reading.lua": "koreader/copy-b/two.lua",
        },
    )

    result = stage_kindle(
        tmp_path / "attested-duplicate-directories",
        runner=_stage_runner(
            payload,
            {
                "koreader/copy-a/one.lua": b"abc",
                "koreader/copy-b/two.lua": b"abc",
            },
            directories=("koreader/copy-a", "koreader/copy-b"),
        ),
    )

    assert tuple(entry.path for entry in result.manifest) == (
        "koreader",
        "koreader/first",
        "koreader/first/reading.lua",
        "koreader/second",
        "koreader/second/reading.lua",
    )
    assert (result.destination / "koreader/first/reading.lua").read_bytes() == b"abc"
    assert (result.destination / "koreader/second/reading.lua").read_bytes() == b"abc"


def test_stage_kindle_rejects_attested_path_map_with_wrong_file_content(
    tmp_path: Path,
) -> None:
    payload = _payload(
        "stage",
        roots=["koreader"],
        file_count=1,
        directory_count=1,
        total_bytes=3,
        source_directories=["koreader"],
        source_sha256={
            "koreader/reading.lua": hashlib.sha256(b"abc").hexdigest(),
        },
        source_path_map={
            "koreader": "koreader",
            "koreader/reading.lua": "koreader/reading.lua",
        },
    )

    with pytest.raises(MtpCopyError) as error:
        stage_kindle(
            tmp_path / "attested-wrong-content",
            runner=_stage_runner(
                payload,
                {"koreader/reading.lua": b"xyz"},
            ),
        )

    assert error.value.reason_code == "source_changed"


def test_stage_kindle_restores_empty_directories_even_when_file_hashes_match(
    tmp_path: Path,
) -> None:
    digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    payload = _payload(
        "stage",
        roots=["koreader"],
        file_count=1,
        directory_count=2,
        total_bytes=3,
        source_directories=["koreader", "koreader/empty-source"],
        source_sha256={"koreader/config.lua": digest},
    )

    result = stage_kindle(
        tmp_path / "empty-directory",
        runner=_stage_runner(
            payload,
            {"koreader/config.lua": b"abc"},
            directories=("koreader/empty-copy",),
        ),
    )

    assert tuple(entry.path for entry in result.manifest) == (
        "koreader",
        "koreader/config.lua",
        "koreader/empty-source",
    )


def test_stage_kindle_rejects_ambiguous_empty_directory_mapping(tmp_path: Path) -> None:
    digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    payload = _payload(
        "stage",
        roots=["koreader"],
        file_count=1,
        directory_count=3,
        total_bytes=3,
        source_directories=[
            "koreader",
            "koreader/empty-source-a",
            "koreader/empty-source-b",
        ],
        source_sha256={"koreader/config.lua": digest},
    )

    with pytest.raises(MtpCopyError):
        stage_kindle(
            tmp_path / "ambiguous-empty-directories",
            runner=_stage_runner(
                payload,
                {"koreader/config.lua": b"abc"},
                directories=("koreader/empty-copy-a", "koreader/empty-copy-b"),
            ),
        )


def test_stage_kindle_keeps_canonical_tree_if_old_stage_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload(
        "stage",
        roots=["koreader"],
        file_count=1,
        directory_count=1,
        total_bytes=3,
        source_sha256={
            "koreader/reading.lua": (
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
            )
        },
    )
    real_rmtree = mtp_module.shutil.rmtree
    partial_cleanup_path: Path | None = None

    def fail_after_partial_cleanup(path: Path, *args: Any, **kwargs: Any) -> None:
        nonlocal partial_cleanup_path
        cleanup_path = Path(path)
        if cleanup_path.name.endswith(".original") and partial_cleanup_path is None:
            partial_cleanup_path = cleanup_path
            (cleanup_path / "koreader" / "config.lua").unlink()
            raise PermissionError("synthetic cleanup failure")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(mtp_module.shutil, "rmtree", fail_after_partial_cleanup)

    result = stage_kindle(tmp_path / "cleanup-failure", runner=_runner(payload))

    assert (result.destination / "koreader" / "reading.lua").read_bytes() == b"abc"
    assert result.cleanup_paths == (partial_cleanup_path,)
    assert partial_cleanup_path is not None
    assert not (partial_cleanup_path / "koreader" / "config.lua").exists()


def test_stage_kindle_restores_source_file_names_after_windows_copy_renames_them(
    tmp_path: Path,
) -> None:
    source_path = "koreader/reading.lua"
    payload = _payload(
        "stage",
        roots=["koreader"],
        file_count=1,
        directory_count=1,
        total_bytes=3,
        source_directories=["koreader"],
        source_sha256={
            source_path: "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        },
    )
    destination = tmp_path / "canonicalized"

    result = stage_kindle(destination, runner=_runner(payload))

    assert tuple(entry.path for entry in result.manifest) == ("koreader", source_path)
    assert (destination / "koreader" / "reading.lua").read_bytes() == b"abc"
    assert not (destination / "koreader" / "config.lua").exists()


def test_stage_kindle_rejects_source_hash_attached_to_different_path(
    tmp_path: Path,
) -> None:
    payload = _payload("stage", roots=["koreader"], file_count=1, directory_count=1, total_bytes=3)
    payload["source_sha256"] = {
        "windows-normalized/config.lua": (
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )
    }

    with pytest.raises(MtpCopyError):
        stage_kindle(tmp_path / "normalized-paths", runner=_runner(payload))


def test_stage_kindle_rejects_path_representation_mismatch_without_verified_binding(
    tmp_path: Path,
) -> None:
    payload = _payload("stage", roots=["koreader"], file_count=1, directory_count=1, total_bytes=3)
    payload["source_paths_verified"] = False
    payload["source_sha256"] = {
        "different/config.lua": ("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    }

    with pytest.raises(MtpCopyError):
        stage_kindle(tmp_path / "unverified-paths", runner=_runner(payload))


def test_stage_kindle_rejects_an_existing_destination(tmp_path: Path) -> None:
    destination = tmp_path / "existing"
    destination.mkdir()
    called = False

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess(args, 0, "{}", "")

    with pytest.raises(MtpCopyError):
        stage_kindle(destination, runner=runner)
    assert called is False


def test_stage_kindle_rejects_copy_process_failure(tmp_path: Path) -> None:
    with pytest.raises(MtpCopyError) as error:
        stage_kindle(
            tmp_path / "failed",
            runner=_runner(
                _payload("stage", status="error", reason_code="copy_failed"),
                returncode=4,
            ),
        )
    assert error.value.reason_code == "copy_failed"


def test_stage_kindle_preserves_safe_short_path_failure_reason(tmp_path: Path) -> None:
    with pytest.raises(MtpCopyError) as error:
        stage_kindle(
            tmp_path / "stage",
            runner=_runner(
                _payload("stage", status="error", reason_code="short_path_unavailable"),
                returncode=4,
            ),
        )

    assert error.value.reason_code == "short_path_unavailable"


def test_stage_koreader_reading_data_filters_and_verifies_the_selected_files(
    tmp_path: Path,
) -> None:
    files = {
        "koreader/settings/statistics.sqlite3": b"database",
        "koreader/settings/statistics.sqlite3-wal": b"wal",
        "koreader/settings/statistics.sqlite3-shm": b"shm",
        "documents/book.epub.sdr/metadata.epub.lua": b"sidecar",
    }

    summary = stage_koreader_reading_data(tmp_path / "reading-stage", runner=_reading_runner(files))

    assert isinstance(summary, MtpReadingStageSummary)
    assert summary.file_count == len(files)
    assert summary.total_bytes == sum(map(len, files.values()))
    assert {entry.path for entry in summary.manifest if entry.kind == "file"} == set(files)
    assert not any(
        Path(entry.path).suffix.casefold() in {".epub", ".pdf"} for entry in summary.manifest
    )


def test_stage_koreader_reading_data_accepts_mixed_content_verification_method(
    tmp_path: Path,
) -> None:
    files = {
        "koreader/settings/statistics.sqlite3": b"database",
        "documents/book.epub.sdr/metadata.epub.lua": b"sidecar",
    }
    summary = stage_koreader_reading_data(
        tmp_path / "mixed-reading-stage",
        runner=_reading_runner(
            files,
            changes={
                "content_verification_method": ("wpd-stream-sha256+shell-content-readback-sha256")
            },
        ),
    )

    assert summary.file_count == len(files)


def test_stage_koreader_reading_data_accepts_sidecars_without_statistics_database(
    tmp_path: Path,
) -> None:
    files = {"documents/book.epub.sdr/metadata.epub.lua": b"sidecar"}

    summary = stage_koreader_reading_data(tmp_path / "sidecar-stage", runner=_reading_runner(files))

    assert summary.file_count == 1
    assert tuple(entry.path for entry in summary.manifest if entry.kind == "file") == tuple(files)


@pytest.mark.parametrize(
    "companions",
    [
        {"koreader/settings/statistics.sqlite3-wal": b"wal"},
        {"koreader/settings/statistics.sqlite3-shm": b"shm"},
    ],
)
def test_stage_koreader_reading_data_rejects_a_partial_wal_family(
    tmp_path: Path, companions: dict[str, bytes]
) -> None:
    files = {"koreader/settings/statistics.sqlite3": b"database", **companions}

    with pytest.raises(MtpCopyError) as error:
        stage_koreader_reading_data(tmp_path / "partial-wal", runner=_reading_runner(files))

    assert error.value.reason_code == "reading_wal_incomplete"


def test_stage_koreader_reading_data_rejects_multiple_statistics_databases(
    tmp_path: Path,
) -> None:
    files = {
        "koreader/settings/statistics.sqlite3": b"first",
        "koreader/backup/statistics.sqlite3": b"second",
    }

    with pytest.raises(MtpCopyError) as error:
        stage_koreader_reading_data(tmp_path / "ambiguous", runner=_reading_runner(files))

    assert error.value.reason_code == "reading_data_ambiguous"


def test_stage_koreader_reading_data_rejects_unverified_source_hashes(tmp_path: Path) -> None:
    files = {"koreader/settings/statistics.sqlite3": b"database"}

    with pytest.raises(MtpCopyError) as error:
        stage_koreader_reading_data(
            tmp_path / "unverified",
            runner=_reading_runner(files, changes={"content_verified": False}),
        )

    assert error.value.reason_code == "source_changed"


@pytest.mark.parametrize("method", [None, "shell-copyhere-sha256"])
def test_stage_koreader_reading_data_rejects_without_wpd_stream_verification(
    tmp_path: Path, method: str | None
) -> None:
    files = {"koreader/settings/statistics.sqlite3": b"database"}
    changes = {} if method is not None else {"content_verification_method": None}
    if method is not None:
        changes["content_verification_method"] = method

    with pytest.raises(MtpCopyError) as error:
        stage_koreader_reading_data(
            tmp_path / "shell-readback",
            runner=_reading_runner(files, changes=changes),
        )

    assert error.value.reason_code == "source_changed"


def test_stage_koreader_reading_data_rejects_unattested_path_hashes(tmp_path: Path) -> None:
    files = {"koreader/settings/statistics.sqlite3": b"database"}

    with pytest.raises(MtpCopyError) as error:
        stage_koreader_reading_data(
            tmp_path / "unattested-paths",
            runner=_reading_runner(files, changes={"source_paths_verified": False}),
        )

    assert error.value.reason_code == "source_changed"


def test_stage_koreader_reading_data_rejects_path_collisions(tmp_path: Path) -> None:
    files = {"koreader/settings/statistics.sqlite3": b"database"}
    digest = hashlib.sha256(b"database").hexdigest()
    hashes = {
        "koreader/settings/statistics.sqlite3": digest,
        r"koreader\settings\statistics.sqlite3": digest,
    }

    with pytest.raises(MtpCopyError) as error:
        stage_koreader_reading_data(
            tmp_path / "collision",
            runner=_reading_runner(files, source_hashes=hashes),
        )

    assert error.value.reason_code == "source_changed"


def test_stage_koreader_reading_data_accepts_unicode_normalization_difference(
    tmp_path: Path,
) -> None:
    staged_path = "koreader/Caf\u00e9.sdr/metadata.epub.lua"
    source_path = "koreader/Cafe\u0301.sdr/metadata.epub.lua"
    content = b"return { summary = true }"

    result = stage_koreader_reading_data(
        tmp_path / "unicode-normalization",
        runner=_reading_runner(
            {staged_path: content},
            source_hashes={source_path: hashlib.sha256(content).hexdigest()},
            changes={
                "source_directories": ["koreader", "koreader/Cafe\u0301.sdr"],
            },
        ),
    )

    assert result.file_count == 1
    assert tuple(entry.path for entry in result.manifest) == (
        "koreader",
        "koreader/Cafe\u0301.sdr",
        source_path,
    )


def test_stage_koreader_reading_data_rejects_ebook_payloads_from_adapter(
    tmp_path: Path,
) -> None:
    files = {
        "koreader/settings/statistics.sqlite3": b"database",
        "documents/book.epub": b"ebook",
    }

    with pytest.raises(MtpCopyError):
        stage_koreader_reading_data(tmp_path / "ebook-leak", runner=_reading_runner(files))
