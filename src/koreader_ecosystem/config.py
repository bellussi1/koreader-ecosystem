from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from koreader_ecosystem.backup_automation import AutomationError, BackupAutomationSettings


class ConfigError(RuntimeError):
    """Raised when local application settings are invalid."""


@dataclass(frozen=True, slots=True)
class AppConfig:
    calibre_library: Path
    restic_repository: Path
    state_root: Path
    restic_remote_repository: str | None = None
    restic_remote_region: str | None = None
    daily_backup_enabled: bool = False
    daily_backup_time: str = "20:00"
    backup_on_kindle_connect: bool = False


_CONFIG_KEYS = frozenset(
    {
        "calibre_library",
        "restic_repository",
        "restic_remote_repository",
        "restic_remote_region",
        "state_root",
        "daily_backup_enabled",
        "daily_backup_time",
        "backup_on_kindle_connect",
    }
)
_S3_REGION = re.compile(r"^(?:auto|[a-z0-9]+(?:-[a-z0-9]+)*)$")


def _default_state_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return (base / "KOReaderEcosystem").resolve()


def _legacy_state_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return (base / "KindleReadingEcosystem").resolve()


def _migrate_legacy_state_root(default_root: Path) -> Path:
    legacy_root = _legacy_state_root()
    if default_root.exists():
        if not (default_root / "config.json").is_file() and (legacy_root / "config.json").is_file():
            return legacy_root
        return default_root
    if not legacy_root.exists():
        return default_root
    try:
        os.replace(legacy_root, default_root)
    except OSError:
        # Keep using the existing profile if Windows has it locked or the move fails.
        return legacy_root
    return default_root


def _path_setting(value: Any, setting: str) -> Path:
    if isinstance(value, Path):
        expanded = value.expanduser()
    elif isinstance(value, str) and value.strip():
        expanded = Path(os.path.expandvars(value)).expanduser()
    else:
        raise ConfigError(f"Configuration setting {setting} must be a path")
    if not expanded.is_absolute():
        raise ConfigError(f"Configuration setting {setting} must be absolute")
    return expanded.resolve()


def validate_s3_repository(value: Any) -> str:
    """Accept credential-free HTTPS S3 repository URLs with a bucket path."""
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise ConfigError("Remote Restic repository must be a credential-free S3 URL")
    if not value.startswith("s3:"):
        raise ConfigError("Remote Restic repository must use the S3 backend")
    try:
        parsed = urlsplit(value[3:])
        port = parsed.port
    except ValueError:
        raise ConfigError("Remote Restic repository URL is invalid") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigError("Remote Restic repository must be a credential-free HTTPS S3 URL")
    if port is not None and not 1 <= port <= 65535:
        raise ConfigError("Remote Restic repository URL is invalid")
    components = parsed.path.split("/")[1:]
    if len(components) < 2 or any(component in {"", ".", ".."} for component in components):
        raise ConfigError("Remote Restic repository URL must include a bucket and repository path")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ConfigError("Remote Restic repository URL is invalid")
    return value


def validate_s3_region(value: Any) -> str:
    if not isinstance(value, str) or not _S3_REGION.fullmatch(value):
        raise ConfigError("Remote Restic region is invalid")
    return value


def load_config(path: Path | None = None) -> AppConfig:
    """Load local settings, environment overrides, or practical user defaults."""
    default_state_root = _default_state_root()
    if path is None and "READING_STATE_ROOT" not in os.environ:
        default_state_root = _migrate_legacy_state_root(default_state_root)
    config_path = (
        Path(path).expanduser() if path is not None else default_state_root / "config.json"
    )

    values: dict[str, Any] = {}
    if config_path.exists():
        try:
            decoded = json.loads(config_path.read_text(encoding="utf-8"))
        except OSError, UnicodeError, json.JSONDecodeError:
            raise ConfigError("Local configuration could not be read") from None
        if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
            raise ConfigError("Local configuration must be a JSON object")
        unknown = set(decoded) - _CONFIG_KEYS
        if unknown:
            raise ConfigError("Local configuration contains unknown settings")
        values = decoded

    configured_state_root = values.get("state_root", default_state_root)
    if "READING_STATE_ROOT" not in os.environ and default_state_root != _legacy_state_root():
        try:
            if Path(configured_state_root).expanduser().resolve() == _legacy_state_root():
                configured_state_root = default_state_root
        except OSError, TypeError, ValueError:
            pass
    state_value = os.environ.get("READING_STATE_ROOT", configured_state_root)
    library_value = os.environ.get(
        "READING_CALIBRE_LIBRARY",
        values.get("calibre_library", Path.home() / "Biblioteca do calibre"),
    )
    repository_value = os.environ.get(
        "READING_RESTIC_REPOSITORY",
        values.get(
            "restic_repository",
            Path.home() / "Documents" / "Calibre_Kindle_Backups" / "reading-ecosystem-restic",
        ),
    )

    remote_repository_value = os.environ.get(
        "READING_RESTIC_REMOTE_REPOSITORY", values.get("restic_remote_repository")
    )
    remote_region_value = os.environ.get(
        "READING_RESTIC_REMOTE_REGION",
        values.get("restic_remote_region", "auto" if remote_repository_value else None),
    )
    if remote_repository_value is None:
        if remote_region_value is not None:
            raise ConfigError("Remote Restic region requires a remote repository")
        remote_repository = None
        remote_region = None
    else:
        remote_repository = validate_s3_repository(remote_repository_value)
        remote_region = validate_s3_region(remote_region_value)

    try:
        automation = BackupAutomationSettings(
            daily_backup_enabled=values.get("daily_backup_enabled", False),
            daily_backup_time=values.get("daily_backup_time", "20:00"),
            backup_on_kindle_connect=values.get("backup_on_kindle_connect", False),
        )
    except AutomationError:
        raise ConfigError("Backup automation settings are invalid") from None

    return AppConfig(
        calibre_library=_path_setting(library_value, "calibre_library"),
        restic_repository=_path_setting(repository_value, "restic_repository"),
        state_root=_path_setting(state_value, "state_root"),
        restic_remote_repository=remote_repository,
        restic_remote_region=remote_region,
        daily_backup_enabled=automation.daily_backup_enabled,
        daily_backup_time=automation.daily_backup_time,
        backup_on_kindle_connect=automation.backup_on_kindle_connect,
    )


def save_config(config: AppConfig, path: Path | None = None) -> None:
    """Atomically persist local settings; provider credentials are never serialized."""
    remote_repository = config.restic_remote_repository
    remote_region = config.restic_remote_region
    if remote_repository is None:
        if remote_region is not None:
            raise ConfigError("Remote Restic region requires a remote repository")
    else:
        validate_s3_repository(remote_repository)
        validate_s3_region(remote_region)

    config_path = Path(path).expanduser() if path is not None else config.state_root / "config.json"
    payload = {
        "calibre_library": str(config.calibre_library),
        "restic_repository": str(config.restic_repository),
        "restic_remote_repository": remote_repository,
        "restic_remote_region": remote_region,
        "state_root": str(config.state_root),
        "daily_backup_enabled": config.daily_backup_enabled,
        "daily_backup_time": config.daily_backup_time,
        "backup_on_kindle_connect": config.backup_on_kindle_connect,
    }
    temporary_path: Path | None = None
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=config_path.parent,
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
        os.replace(temporary_path, config_path)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise ConfigError("Local configuration could not be saved") from None
