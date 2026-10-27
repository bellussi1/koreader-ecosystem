from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from koreader_ecosystem.restic import (
    ResticClient,
    ResticCredentialError,
    ResticError,
    ResticTimeoutError,
)
from koreader_ecosystem.secrets import S3Credentials


def test_restic_client_discovers_latest_winget_binary_when_path_alias_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.restic as restic_module

    local_app_data = tmp_path / "local-app-data"
    packages = local_app_data / "Microsoft" / "WinGet" / "Packages"
    older = packages / "restic.restic_old" / "restic_0.18.1_windows_amd64.exe"
    latest = packages / "restic.restic_new" / "restic_0.19.1_windows_amd64.exe"
    older.parent.mkdir(parents=True)
    latest.parent.mkdir(parents=True)
    older.touch()
    latest.touch()
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr(restic_module.shutil, "which", lambda _: None)

    client = ResticClient(tmp_path / "repo", password_provider=lambda: "secret")

    assert client.executable == str(latest)


def _runner_for(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
    calls: list[dict[str, Any]] | None = None,
):
    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if calls is not None:
            calls.append({"args": args, **kwargs})
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    return run


def test_restic_run_passes_password_only_in_child_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RESTIC_PASSWORD", raising=False)
    calls: list[dict[str, Any]] = []
    secret = "private-passphrase"
    client = ResticClient(
        tmp_path / "repo",
        password_provider=lambda: secret,
        executable="restic-test",
        runner=_runner_for(calls=calls),
    )

    result = client.run(["check"])

    assert result.ok
    assert calls[0]["args"] == ["restic-test", "check"]
    assert calls[0]["capture_output"] is True
    assert calls[0]["text"] is True
    assert calls[0]["check"] is False
    assert calls[0]["env"]["RESTIC_PASSWORD"] == secret
    assert calls[0]["env"]["RESTIC_REPOSITORY"] == str(tmp_path / "repo")
    assert "RESTIC_PASSWORD_FILE" not in calls[0]["env"]
    assert "RESTIC_PASSWORD_COMMAND" not in calls[0]["env"]
    assert "RESTIC_PASSWORD" not in os.environ
    assert secret not in repr(result)


def test_s3_restic_run_passes_credentials_only_to_child_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    access_key = "access-id-example"
    secret_key = "private-s3-secret"
    repository = "s3:https://account.r2.cloudflarestorage.com/books/reading-ecosystem"
    client = ResticClient(
        repository,
        password_provider=lambda: "restic-passphrase",
        executable="restic-test",
        runner=_runner_for(calls=calls),
        s3_credentials_provider=lambda: S3Credentials(access_key, secret_key),
        s3_region="auto",
    )

    result = client.run(["check"])

    assert result.ok
    assert calls[0]["args"] == ["restic-test", "check"]
    assert calls[0]["env"]["RESTIC_REPOSITORY"] == repository
    assert calls[0]["env"]["AWS_ACCESS_KEY_ID"] == access_key
    assert calls[0]["env"]["AWS_SECRET_ACCESS_KEY"] == secret_key
    assert calls[0]["env"]["AWS_DEFAULT_REGION"] == "auto"
    assert access_key not in repr(calls[0]["args"])
    assert secret_key not in repr(calls[0]["args"])
    assert access_key not in repr(result)
    assert secret_key not in repr(result)
    assert "AWS_SECRET_ACCESS_KEY" not in os.environ


def test_s3_restic_clears_inherited_aws_profiles_and_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_PROFILE", "private-profile")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://unexpected.example.test")
    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "https://other.example.test")
    calls: list[dict[str, Any]] = []
    client = ResticClient(
        "s3:https://account.r2.cloudflarestorage.com/books/reading-ecosystem",
        password_provider=lambda: "restic-passphrase",
        executable="restic-test",
        runner=_runner_for(calls=calls),
        s3_credentials_provider=lambda: S3Credentials("access-id", "s3-secret"),
    )

    client.run(["check"])

    assert "AWS_PROFILE" not in calls[0]["env"]
    assert "AWS_ENDPOINT_URL" not in calls[0]["env"]
    assert "AWS_ENDPOINT_URL_S3" not in calls[0]["env"]


def test_local_restic_run_does_not_inherit_source_repository_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RESTIC_FROM_PASSWORD", "private-source-passphrase")
    calls: list[dict[str, Any]] = []
    client = ResticClient(
        tmp_path / "repo",
        password_provider=lambda: "local-passphrase",
        executable="restic-test",
        runner=_runner_for(calls=calls),
    )

    client.run(["check"])

    assert "RESTIC_FROM_PASSWORD" not in calls[0]["env"]


def test_s3_restic_rejects_invalid_region_before_starting_process() -> None:
    with pytest.raises(ResticError, match="settings are invalid"):
        ResticClient(
            "s3:https://account.r2.cloudflarestorage.com/books/reading-ecosystem",
            s3_region="bad region",
        )


def test_remote_restic_copy_keeps_source_password_in_child_environment(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []
    source = ResticClient(
        tmp_path / "local-repo",
        password_provider=lambda: "local-passphrase",
        executable="restic-test",
        runner=_runner_for(calls=calls),
    )
    destination = ResticClient(
        "s3:https://account.r2.cloudflarestorage.com/books/reading-ecosystem",
        password_provider=lambda: "remote-passphrase",
        executable="restic-test",
        runner=_runner_for(calls=calls),
        s3_credentials_provider=lambda: S3Credentials("access-id", "s3-secret"),
        s3_region="auto",
    )

    result = destination.run_copy_from(["copy"], source)

    assert result.ok
    assert calls[0]["args"] == ["restic-test", "copy"]
    assert calls[0]["env"]["RESTIC_REPOSITORY"].startswith("s3:https://")
    assert calls[0]["env"]["RESTIC_FROM_REPOSITORY"] == str(source.repository)
    assert calls[0]["env"]["RESTIC_PASSWORD"] == "remote-passphrase"
    assert calls[0]["env"]["RESTIC_FROM_PASSWORD"] == "local-passphrase"
    assert "local-passphrase" not in repr(calls[0]["args"])
    assert "s3-secret" not in repr(calls[0]["args"])


def test_restic_run_uses_requested_working_directory(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    client = ResticClient(
        tmp_path / "repo",
        password_provider=lambda: "secret",
        executable="restic-test",
        runner=_runner_for(calls=calls),
    )

    result = client.run(["backup", "--json", "source"], cwd=tmp_path)

    assert result.ok
    assert calls[0]["cwd"] == tmp_path


def test_restic_run_parses_only_safe_backup_summary_fields(tmp_path: Path) -> None:
    snapshot_id = "a" * 64
    stdout = "\n".join(
        [
            json.dumps({"message_type": "status", "current_files": ["private-book.epub"]}),
            json.dumps(
                {
                    "message_type": "summary",
                    "snapshot_id": snapshot_id,
                    "files_new": 12,
                    "total_files_processed": 48,
                    "data_added": 8192,
                    "path": "C:/private/book-library",
                }
            ),
        ]
    )
    client = ResticClient(
        tmp_path / "repo",
        password_provider=lambda: "secret",
        executable="restic-test",
        runner=_runner_for(stdout=stdout),
    )

    result = client.run(["backup", "--json"])

    assert result.summary == {
        "snapshot_id": snapshot_id,
        "files_new": 12,
        "total_files_processed": 48,
        "data_added": 8192,
    }
    assert "private-book.epub" not in repr(result)
    assert "private/book-library" not in repr(result)


def test_restic_run_sanitizes_failure_output(tmp_path: Path) -> None:
    client = ResticClient(
        tmp_path / "repo",
        password_provider=lambda: "secret-passphrase",
        executable="restic-test",
        runner=_runner_for(
            stderr="private path and secret-passphrase must not be exposed", returncode=12
        ),
    )

    result = client.run(["check"])

    assert not result.ok
    assert result.returncode == 12
    assert "private path" not in repr(result)
    assert "secret-passphrase" not in repr(result)


def test_restic_run_rejects_empty_password_before_calling_process(tmp_path: Path) -> None:
    called = False

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess(args, 0, "", "")

    client = ResticClient(
        tmp_path / "repo",
        password_provider=lambda: "   ",
        executable="restic-test",
        runner=runner,
    )

    with pytest.raises(ResticCredentialError):
        client.run(["check"])
    assert called is False


def test_restic_run_sanitizes_timeout_output(tmp_path: Path) -> None:
    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, 10, stderr="private-book.epub")

    client = ResticClient(
        tmp_path / "repo",
        password_provider=lambda: "secret",
        executable="restic-test",
        runner=runner,
    )

    with pytest.raises(ResticTimeoutError) as error:
        client.run(["backup"])
    assert "private-book.epub" not in str(error.value)


def test_restic_snapshots_omit_source_paths(tmp_path: Path) -> None:
    snapshot_id = "b" * 64
    stdout = json.dumps(
        [
            {
                "id": snapshot_id,
                "time": "2026-10-23T12:00:00Z",
                "paths": ["C:/private/book-library"],
                "tags": ["kindle-ecosystem"],
            }
        ]
    )
    client = ResticClient(
        tmp_path / "repo",
        password_provider=lambda: "secret",
        executable="restic-test",
        runner=_runner_for(stdout=stdout),
    )

    result = client.run(["snapshots", "--json"])

    assert len(result.snapshots) == 1
    assert result.snapshots[0].id == snapshot_id
    assert result.snapshots[0].time == "2026-10-23T12:00:00+00:00"
    assert "private/book-library" not in repr(result)
