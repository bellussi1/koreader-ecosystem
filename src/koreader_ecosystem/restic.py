from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from koreader_ecosystem.config import (
    ConfigError,
    validate_s3_region,
    validate_s3_repository,
)
from koreader_ecosystem.portable_bundle import resolve_runtime_asset
from koreader_ecosystem.processes import run_hidden
from koreader_ecosystem.secrets import (
    S3Credentials,
    SecretStoreError,
    load_restic_password,
    load_s3_credentials,
)


class ResticError(RuntimeError):
    """Base class for sanitized Restic failures."""


class ResticUnavailableError(ResticError):
    """Raised when the Restic executable cannot be started."""


class ResticCredentialError(ResticError):
    """Raised when a protected Restic password is unavailable."""

    def __init__(self, message: str, *, reason_code: str = "restic_password_unavailable") -> None:
        self.reason_code = reason_code
        super().__init__(message)


class ResticTimeoutError(ResticError):
    """Raised when a Restic command exceeds its time limit."""


type SummaryValue = str | int | float
Runner = Callable[..., subprocess.CompletedProcess[str]]

_SNAPSHOT_ID = re.compile(r"^[0-9a-f]{64}$")
_WINGET_EXECUTABLE = re.compile(r"^restic_(\d+(?:\.\d+){1,3})_windows_amd64\.exe$")
_SUMMARY_FIELDS = frozenset(
    {
        "files_new",
        "files_changed",
        "files_unmodified",
        "total_files_processed",
        "dirs_new",
        "dirs_changed",
        "dirs_unmodified",
        "data_added",
        "total_duration",
    }
)


def _find_executable() -> str | None:
    bundled = resolve_runtime_asset("restic.exe")
    if bundled.is_file():
        return str(bundled)

    executable = shutil.which("restic")
    if executable is not None:
        return executable

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    packages = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    try:
        candidates = [
            (tuple(int(part) for part in match.group(1).split(".")), candidate)
            for candidate in packages.glob("restic.restic_*/restic_*_windows_amd64.exe")
            if candidate.is_file()
            if (match := _WINGET_EXECUTABLE.fullmatch(candidate.name)) is not None
        ]
    except OSError:
        return None
    if not candidates:
        return None
    return str(max(candidates, key=lambda item: item[0])[1])


@dataclass(frozen=True, slots=True)
class ResticSnapshot:
    id: str
    time: str
    signature: str = ""
    tags: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class ResticResult:
    returncode: int
    summary: Mapping[str, SummaryValue] = field(default_factory=lambda: MappingProxyType({}))
    snapshots: tuple[ResticSnapshot, ...] = ()

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _safe_summary(stdout: str) -> dict[str, SummaryValue]:
    for line in reversed(stdout.splitlines()):
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict) or message.get("message_type") != "summary":
            continue

        summary: dict[str, SummaryValue] = {}
        snapshot_id = message.get("snapshot_id")
        if isinstance(snapshot_id, str) and _SNAPSHOT_ID.fullmatch(snapshot_id):
            summary["snapshot_id"] = snapshot_id
        for key in _SUMMARY_FIELDS:
            value = message.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                summary[key] = value
            elif isinstance(value, float) and math.isfinite(value):
                summary[key] = value
        return summary
    return {}


def _safe_snapshots(stdout: str) -> tuple[ResticSnapshot, ...]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return ()
    if not isinstance(payload, list):
        return ()

    snapshots: list[ResticSnapshot] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        snapshot_id = item.get("id")
        timestamp = item.get("time")
        if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID.fullmatch(snapshot_id):
            continue
        if not isinstance(timestamp, str):
            continue
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        raw_tags = item.get("tags", [])
        tags = tuple(
            sorted(tag for tag in raw_tags if isinstance(tag, str))
            if isinstance(raw_tags, list)
            else ()
        )
        metadata = {
            "time": parsed.isoformat(),
            "hostname": item.get("hostname") if isinstance(item.get("hostname"), str) else "",
            "username": item.get("username") if isinstance(item.get("username"), str) else "",
            "paths": sorted(path for path in item.get("paths", []) if isinstance(path, str))
            if isinstance(item.get("paths", []), list)
            else [],
            "tags": list(tags),
        }
        signature = hashlib.sha256(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        snapshots.append(
            ResticSnapshot(id=snapshot_id, time=parsed.isoformat(), signature=signature, tags=tags)
        )
    return tuple(snapshots)


class ResticClient:
    """Run Restic with credentials isolated to the child process environment."""

    def __init__(
        self,
        repository: Path | str,
        password_provider: Callable[[], str] = load_restic_password,
        executable: str | Path | None = None,
        runner: Runner = run_hidden,
        timeout_seconds: int = 6 * 60 * 60,
        s3_credentials_provider: Callable[[], S3Credentials] = load_s3_credentials,
        s3_region: str | None = None,
    ) -> None:
        if isinstance(repository, str) and repository.startswith("s3:"):
            try:
                self.repository: Path | str = validate_s3_repository(repository)
                self.s3_region = validate_s3_region(s3_region or "auto")
            except ConfigError:
                raise ResticError("Remote Restic settings are invalid") from None
        else:
            self.repository = Path(repository).expanduser().resolve()
            self.s3_region = "auto"
        self.password_provider = password_provider
        self.executable = str(executable) if executable is not None else _find_executable()
        self.runner = runner
        self.timeout_seconds = timeout_seconds
        self.s3_credentials_provider = s3_credentials_provider
        self._password: str | None = None
        self._s3_credentials: S3Credentials | None = None

    @property
    def available(self) -> bool:
        return self.executable is not None

    def ensure_password(self) -> None:
        if self._password is not None:
            return
        try:
            password = self.password_provider()
        except SecretStoreError:
            raise ResticCredentialError("A Restic password is unavailable") from None
        except Exception:
            raise ResticCredentialError("A Restic password is unavailable") from None
        if not isinstance(password, str) or not password.strip():
            raise ResticCredentialError("A Restic password is unavailable")
        self._password = password

    def run(self, args: Sequence[str], *, cwd: Path | None = None) -> ResticResult:
        return self._run(args, cwd=cwd)

    def run_copy_from(
        self, args: Sequence[str], source: ResticClient, *, cwd: Path | None = None
    ) -> ResticResult:
        if not isinstance(source.repository, Path):
            raise ResticError("Remote snapshot copy requires a local source repository")
        return self._run(args, cwd=cwd, source=source)

    def _run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        source: ResticClient | None = None,
    ) -> ResticResult:
        if not args or any(not isinstance(arg, str) for arg in args):
            raise ResticError("Restic arguments are invalid")
        if self.executable is None:
            raise ResticUnavailableError("Restic is not installed")
        self.ensure_password()

        environment = os.environ.copy()
        for name in (
            "RESTIC_REPOSITORY_FILE",
            "RESTIC_FROM_REPOSITORY",
            "RESTIC_FROM_REPOSITORY_FILE",
            "RESTIC_FROM_PASSWORD",
            "RESTIC_FROM_PASSWORD_FILE",
            "RESTIC_FROM_PASSWORD_COMMAND",
        ):
            environment.pop(name, None)
        environment.pop("RESTIC_PASSWORD_FILE", None)
        environment.pop("RESTIC_PASSWORD_COMMAND", None)
        environment["RESTIC_REPOSITORY"] = str(self.repository)
        environment["RESTIC_PASSWORD"] = self._password or ""
        if isinstance(self.repository, str):
            self._ensure_s3_credentials()
            credentials = self._s3_credentials
            if credentials is None:
                raise ResticCredentialError("S3 credentials are unavailable")
            for name in (
                "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY",
                "AWS_SESSION_TOKEN",
                "AWS_PROFILE",
                "AWS_SHARED_CREDENTIALS_FILE",
                "AWS_DEFAULT_REGION",
                "AWS_REGION",
                "AWS_ENDPOINT_URL",
                "AWS_ENDPOINT_URL_S3",
                "AWS_CONFIG_FILE",
            ):
                environment.pop(name, None)
            environment["AWS_ACCESS_KEY_ID"] = credentials.access_key_id
            environment["AWS_SECRET_ACCESS_KEY"] = credentials.secret_access_key
            environment["AWS_DEFAULT_REGION"] = self.s3_region
        if source is not None:
            source.ensure_password()
            environment["RESTIC_FROM_REPOSITORY"] = str(source.repository)
            environment["RESTIC_FROM_PASSWORD"] = source._password or ""
        command = [self.executable, *args]

        try:
            process = self.runner(
                command,
                capture_output=True,
                text=True,
                check=False,
                env=environment,
                timeout=self.timeout_seconds,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            raise ResticTimeoutError("Restic command timed out") from None
        except OSError, subprocess.SubprocessError:
            raise ResticUnavailableError("Restic command could not be started") from None

        summary = _safe_summary(process.stdout or "")
        snapshots = _safe_snapshots(process.stdout or "") if args[0] == "snapshots" else ()
        return ResticResult(
            returncode=process.returncode,
            summary=MappingProxyType(summary),
            snapshots=snapshots,
        )

    def _ensure_s3_credentials(self) -> None:
        if self._s3_credentials is not None:
            return
        try:
            credentials = self.s3_credentials_provider()
        except SecretStoreError:
            raise ResticCredentialError(
                "S3 credentials are unavailable", reason_code="s3_credentials_unavailable"
            ) from None
        except Exception:
            raise ResticCredentialError(
                "S3 credentials are unavailable", reason_code="s3_credentials_unavailable"
            ) from None
        if not isinstance(credentials, S3Credentials):
            raise ResticCredentialError(
                "S3 credentials are unavailable", reason_code="s3_credentials_unavailable"
            )
        self._s3_credentials = credentials
