from __future__ import annotations

import json
from pathlib import Path

import pytest

from koreader_ecosystem.config import AppConfig, ConfigError, load_config, save_config


def test_load_config_uses_user_defaults_without_writing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "user"
    local_app_data = tmp_path / "local"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    config = load_config()

    assert config == AppConfig(
        calibre_library=home / "Biblioteca do calibre",
        restic_repository=home
        / "Documents"
        / "Calibre_Kindle_Backups"
        / "reading-ecosystem-restic",
        state_root=local_app_data / "KOReaderEcosystem",
    )
    assert not local_app_data.exists()


def test_load_config_migrates_legacy_profile_without_losing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_app_data = tmp_path / "local"
    legacy_root = local_app_data / "KindleReadingEcosystem"
    legacy_root.mkdir(parents=True)
    legacy_config = {
        "calibre_library": str(tmp_path / "library"),
        "restic_repository": str(tmp_path / "restic"),
        "state_root": str(legacy_root),
    }
    (legacy_root / "config.json").write_text(json.dumps(legacy_config), encoding="utf-8")
    (legacy_root / "profile-sentinel.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    config = load_config()

    new_root = local_app_data / "KOReaderEcosystem"
    assert config.state_root == new_root
    assert (new_root / "profile-sentinel.txt").read_text(encoding="utf-8") == "keep"
    assert not legacy_root.exists()


def test_load_config_keeps_legacy_profile_when_new_folder_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_app_data = tmp_path / "local"
    new_root = local_app_data / "KOReaderEcosystem"
    new_root.mkdir(parents=True)
    (new_root / "partial-migration.txt").write_text("keep", encoding="utf-8")
    legacy_root = local_app_data / "KindleReadingEcosystem"
    legacy_root.mkdir()
    legacy_config = {
        "calibre_library": str(tmp_path / "library"),
        "restic_repository": str(tmp_path / "restic"),
        "state_root": str(legacy_root),
    }
    (legacy_root / "config.json").write_text(json.dumps(legacy_config), encoding="utf-8")
    (legacy_root / "profile-sentinel.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    config = load_config()

    assert config.state_root == legacy_root
    assert (new_root / "partial-migration.txt").read_text(encoding="utf-8") == "keep"
    assert (legacy_root / "profile-sentinel.txt").read_text(encoding="utf-8") == "keep"


def test_load_config_reads_absolute_paths_and_environment_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "calibre_library": str(tmp_path / "configured-library"),
                "restic_repository": str(tmp_path / "configured-repo"),
            }
        ),
        encoding="utf-8",
    )
    env_library = tmp_path / "environment-library"
    monkeypatch.setenv("READING_CALIBRE_LIBRARY", str(env_library))

    config = load_config(config_path)

    assert config.calibre_library == env_library
    assert config.restic_repository == tmp_path / "configured-repo"


def test_load_config_reads_optional_s3_repository_without_changing_local_repository(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    remote = "s3:https://account.r2.cloudflarestorage.com/books/reading-ecosystem"
    config_path.write_text(
        json.dumps(
            {
                "calibre_library": str(tmp_path / "library"),
                "restic_repository": str(tmp_path / "local-repo"),
                "restic_remote_repository": remote,
                "restic_remote_region": "auto",
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.restic_repository == tmp_path / "local-repo"
    assert config.restic_remote_repository == remote
    assert config.restic_remote_region == "auto"


@pytest.mark.parametrize(
    "remote",
    [
        "s3:http://storage.example.test/books/repo",
        "s3:https://user:password@storage.example.test/books/repo",
        "s3:https://storage.example.test",
        "s3:https://storage.example.test/books/repo?access_key=private",
        "https://storage.example.test/books/repo",
    ],
)
def test_load_config_rejects_invalid_s3_repository_urls(tmp_path: Path, remote: str) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "calibre_library": str(tmp_path / "library"),
                "restic_repository": str(tmp_path / "local-repo"),
                "restic_remote_repository": remote,
                "restic_remote_region": "auto",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(config_path)


@pytest.mark.parametrize("region", ["", "has whitespace", "bad\nregion", "UPPER"])
def test_load_config_rejects_invalid_s3_region(tmp_path: Path, region: str) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "calibre_library": str(tmp_path / "library"),
                "restic_repository": str(tmp_path / "local-repo"),
                "restic_remote_repository": (
                    "s3:https://account.r2.cloudflarestorage.com/books/reading-ecosystem"
                ),
                "restic_remote_region": region,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_save_config_persists_remote_settings_without_credentials(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config = AppConfig(
        calibre_library=tmp_path / "library",
        restic_repository=tmp_path / "local-repo",
        state_root=tmp_path / "state",
        restic_remote_repository=(
            "s3:https://account.r2.cloudflarestorage.com/books/reading-ecosystem"
        ),
        restic_remote_region="auto",
    )

    save_config(config, config_path)

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["restic_remote_repository"] == config.restic_remote_repository
    assert payload["restic_remote_region"] == "auto"
    assert "access_key" not in config_path.read_text(encoding="utf-8")
    assert load_config(config_path) == config


def test_backup_automation_preferences_round_trip_and_default_to_disabled(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config = AppConfig(
        calibre_library=tmp_path / "library",
        restic_repository=tmp_path / "local-repo",
        state_root=tmp_path / "state",
        daily_backup_enabled=True,
        daily_backup_time="06:30",
        backup_on_kindle_connect=True,
    )

    save_config(config, config_path)

    assert load_config(config_path) == config
    defaults_path = tmp_path / "empty" / "config.json"
    defaults_path.parent.mkdir()
    defaults_path.write_text(
        json.dumps(
            {
                "calibre_library": str(tmp_path / "library"),
                "restic_repository": str(tmp_path / "repo"),
                "state_root": str(tmp_path / "state"),
            }
        ),
        encoding="utf-8",
    )
    defaults = load_config(defaults_path)
    assert defaults.daily_backup_enabled is False
    assert defaults.backup_on_kindle_connect is False
    assert defaults.daily_backup_time == "20:00"


@pytest.mark.parametrize(
    "contents",
    [
        "not-json",
        "[]",
        '{"calibre_library": 42}',
        '{"unexpected": "value"}',
        '{"calibre_library": "relative/path"}',
    ],
)
def test_load_config_rejects_invalid_files(tmp_path: Path, contents: str) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(contents, encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(config_path)
