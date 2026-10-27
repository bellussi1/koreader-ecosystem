from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from koreader_ecosystem.remote_backup import RemoteSyncError, _snapshot_key, sync_remote
from koreader_ecosystem.restic import ResticClient, ResticSnapshot
from koreader_ecosystem.secrets import S3Credentials


def test_sync_remote_initializes_with_local_chunker_and_verifies_copied_snapshots(
    tmp_path: Path,
) -> None:
    source_snapshot = {
        "id": "a" * 64,
        "time": "2026-10-23T12:00:00Z",
        "hostname": "reading-pc",
        "username": "reader",
        "paths": ["C:/private/library", "C:/private/kindle-stage"],
        "tags": ["calibre", "kindle"],
    }
    remote_snapshot = {**source_snapshot, "id": "b" * 64}
    source_json = json.dumps([source_snapshot])
    remote_json = json.dumps([remote_snapshot])
    responses = [
        subprocess.CompletedProcess([], 0, source_json, ""),
        subprocess.CompletedProcess([], 10, "", "repository does not exist"),
        subprocess.CompletedProcess([], 0, "repository initialized", ""),
        subprocess.CompletedProcess([], 0, "snapshot copied", ""),
        subprocess.CompletedProcess([], 0, "check passed", ""),
        subprocess.CompletedProcess([], 0, source_json, ""),
        subprocess.CompletedProcess([], 0, remote_json, ""),
    ]
    calls: list[dict[str, Any]] = []

    def runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, **kwargs})
        return responses.pop(0)

    source = ResticClient(
        tmp_path / "local-repo",
        password_provider=lambda: "local-passphrase",
        executable="restic-test",
        runner=runner,
    )
    remote = ResticClient(
        "s3:https://account.r2.cloudflarestorage.com/books/reading-ecosystem",
        password_provider=lambda: "remote-passphrase",
        executable="restic-test",
        runner=runner,
        s3_credentials_provider=lambda: S3Credentials("access-id", "private-s3-secret"),
        s3_region="auto",
    )

    result = sync_remote(source, remote)

    assert result.source_snapshot_count == 1
    assert result.remote_snapshot_count == 1
    assert calls[1]["command"] == ["restic-test", "cat", "config"]
    assert calls[2]["command"] == [
        "restic-test",
        "init",
        "--from-repo",
        str(source.repository),
        "--copy-chunker-params",
    ]
    assert calls[2]["env"]["RESTIC_FROM_PASSWORD"] == "local-passphrase"
    assert calls[3]["command"] == ["restic-test", "copy"]
    assert calls[3]["env"]["RESTIC_FROM_PASSWORD"] == "local-passphrase"
    assert "C:/private/library" not in repr(result)


def test_sync_remote_fails_when_snapshot_metadata_does_not_match(tmp_path: Path) -> None:
    source_snapshot = {
        "id": "a" * 64,
        "time": "2026-10-23T12:00:00Z",
        "hostname": "reading-pc",
        "paths": ["C:/private/library"],
        "tags": ["calibre"],
    }
    remote_snapshot = {**source_snapshot, "id": "b" * 64, "paths": ["C:/other/library"]}
    responses = [
        subprocess.CompletedProcess([], 0, json.dumps([source_snapshot]), ""),
        subprocess.CompletedProcess([], 0, "config", ""),
        subprocess.CompletedProcess([], 0, "copied", ""),
        subprocess.CompletedProcess([], 0, "checked", ""),
        subprocess.CompletedProcess([], 0, json.dumps([source_snapshot]), ""),
        subprocess.CompletedProcess([], 0, json.dumps([remote_snapshot]), ""),
    ]

    def runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return responses.pop(0)

    source = ResticClient(
        tmp_path / "local-repo",
        password_provider=lambda: "local-passphrase",
        executable="restic-test",
        runner=runner,
    )
    remote = ResticClient(
        "s3:https://account.r2.cloudflarestorage.com/books/reading-ecosystem",
        password_provider=lambda: "remote-passphrase",
        executable="restic-test",
        runner=runner,
        s3_credentials_provider=lambda: S3Credentials("access-id", "private-s3-secret"),
    )

    with pytest.raises(RemoteSyncError) as error:
        sync_remote(source, remote)

    assert error.value.reason_code == "remote_snapshot_verification_failed"
    assert "C:/private/library" not in str(error.value)


def test_sync_remote_does_not_initialize_destination_when_source_is_empty(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "[]", "")

    source = ResticClient(
        tmp_path / "local-repo",
        password_provider=lambda: "local-passphrase",
        executable="restic-test",
        runner=runner,
    )
    remote = ResticClient(
        "s3:https://account.r2.cloudflarestorage.com/books/reading-ecosystem",
        password_provider=lambda: "remote-passphrase",
        executable="restic-test",
        runner=runner,
        s3_credentials_provider=lambda: S3Credentials("access-id", "private-s3-secret"),
    )

    with pytest.raises(RemoteSyncError) as error:
        sync_remote(source, remote)

    assert error.value.reason_code == "source_repository_has_no_snapshots"
    assert calls == [["restic-test", "snapshots", "--json"]]


def test_snapshot_identity_requires_metadata_signature() -> None:
    with pytest.raises(RemoteSyncError) as error:
        _snapshot_key(ResticSnapshot("a" * 64, "2026-10-23T12:00:00+00:00"))

    assert error.value.reason_code == "snapshot_signature_unavailable"


@pytest.mark.parametrize(
    ("failure_stage", "expected_reason", "expected_call_count"),
    [
        ("config", "remote_repository_unavailable", 2),
        ("copy", "remote_snapshot_copy_failed", 3),
        ("check", "remote_repository_check_failed", 4),
    ],
)
def test_sync_remote_stops_on_remote_operation_failure(
    tmp_path: Path,
    failure_stage: str,
    expected_reason: str,
    expected_call_count: int,
) -> None:
    source_snapshot = {
        "id": "a" * 64,
        "time": "2026-10-23T12:00:00Z",
        "hostname": "reading-pc",
        "paths": ["C:/private/library"],
        "tags": ["calibre"],
    }
    responses = [subprocess.CompletedProcess([], 0, json.dumps([source_snapshot]), "")]
    responses.extend(
        {
            "config": [subprocess.CompletedProcess([], 1, "", "unavailable")],
            "copy": [
                subprocess.CompletedProcess([], 0, "config", ""),
                subprocess.CompletedProcess([], 1, "", "copy failed"),
            ],
            "check": [
                subprocess.CompletedProcess([], 0, "config", ""),
                subprocess.CompletedProcess([], 0, "copied", ""),
                subprocess.CompletedProcess([], 1, "", "check failed"),
            ],
        }[failure_stage]
    )
    calls: list[list[str]] = []

    def runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return responses.pop(0)

    source = ResticClient(
        tmp_path / "local-repo",
        password_provider=lambda: "local-passphrase",
        executable="restic-test",
        runner=runner,
    )
    remote = ResticClient(
        "s3:https://account.r2.cloudflarestorage.com/books/reading-ecosystem",
        password_provider=lambda: "remote-passphrase",
        executable="restic-test",
        runner=runner,
        s3_credentials_provider=lambda: S3Credentials("access-id", "private-s3-secret"),
    )

    with pytest.raises(RemoteSyncError) as error:
        sync_remote(source, remote)

    assert error.value.reason_code == expected_reason
    assert len(calls) == expected_call_count
