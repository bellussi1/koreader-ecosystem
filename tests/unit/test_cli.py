from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from koreader_ecosystem.backup import BackupResult, RestoreResult
from koreader_ecosystem.config import AppConfig
from koreader_ecosystem.mtp import MtpDeviceSummary
from koreader_ecosystem.restic import ResticResult, ResticSnapshot
from koreader_ecosystem.services import ApplicationServices


@pytest.fixture(autouse=True)
def _hardcover_credential_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ApplicationServices, "_hardcover_token_available", lambda _: True)


class FakeRestic:
    available = True

    def __init__(self, _: Path) -> None:
        pass

    def run(self, args: list[str]) -> ResticResult:
        return ResticResult(
            returncode=0,
            snapshots=(ResticSnapshot("d" * 64, "2026-10-23T12:00:00+00:00"),),
        )


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        calibre_library=tmp_path / "private-library",
        restic_repository=tmp_path / "private-repository",
        state_root=tmp_path / "private-state",
    )


def test_status_outputs_snapshot_ids_and_times_without_source_paths(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import koreader_ecosystem.cli as cli

    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path))
    monkeypatch.setattr(cli, "ResticClient", FakeRestic)
    monkeypatch.setattr("koreader_ecosystem.services.restic_password_is_stored", lambda: True)

    exit_code = cli.main(["status"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload == {
        "status": "ok",
        "snapshots": [{"id": "d" * 64, "time": "2026-10-23T12:00:00+00:00"}],
    }
    assert "private-library" not in json.dumps(payload)


def test_status_does_not_prompt_when_no_restic_password_is_stored(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import koreader_ecosystem.cli as cli

    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path))
    monkeypatch.setattr(cli, "ResticClient", FakeRestic)
    monkeypatch.setattr("koreader_ecosystem.services.restic_password_is_stored", lambda: False)

    exit_code = cli.main(["status"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload == {"status": "error", "reason_code": "restic_password_unavailable"}


def test_backup_requires_recovery_copy_confirmation_before_staging(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import koreader_ecosystem.cli as cli

    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path))
    monkeypatch.setattr(cli, "ResticClient", FakeRestic)
    monkeypatch.setattr("builtins.input", lambda: "n")
    monkeypatch.setattr(
        "koreader_ecosystem.services.preflight_before_device_copy",
        lambda *_: (_ for _ in ()).throw(AssertionError("preflight must not run")),
    )
    monkeypatch.setattr(
        "koreader_ecosystem.services.stage_kindle",
        lambda *_: (_ for _ in ()).throw(AssertionError("staging must not run")),
    )

    exit_code = cli.main(["backup"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 2
    assert payload == {"status": "error", "reason_code": "recovery_copy_unconfirmed"}
    assert "gerenciador de senhas fora deste computador" in captured.err


def test_backup_runs_preflight_staging_and_verified_backup_after_confirmation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import koreader_ecosystem.cli as cli

    config = _config(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "ResticClient", FakeRestic)
    monkeypatch.setattr("builtins.input", lambda: "s")
    order: list[str] = []
    monkeypatch.setattr(
        "koreader_ecosystem.services.preflight_before_device_copy",
        lambda *_: order.append("preflight"),
    )
    staged_paths: list[Path] = []

    def fake_stage(destination: Path) -> object:
        order.append("stage")
        destination.mkdir(parents=True)
        (destination / "private-book.txt").write_text("staged", encoding="utf-8")
        staged_paths.append(destination)
        return SimpleNamespace(cleanup_paths=())

    monkeypatch.setattr("koreader_ecosystem.services.stage_kindle", fake_stage)
    backup_options: dict[str, object] = {}

    def fake_backup(*args, **kwargs) -> BackupResult:
        order.append("backup")
        backup_options.update(kwargs)
        return BackupResult("f" * 64, 7, 99, 2, 5, restore_verified=True)

    monkeypatch.setattr("koreader_ecosystem.services.create_verified_backup", fake_backup)

    exit_code = cli.main(["backup", "--verify-restore"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert order == ["preflight", "stage", "backup"]
    assert backup_options == {"verify_restore": True}
    assert len(staged_paths) == 1
    assert not staged_paths[0].exists()
    assert payload == {
        "status": "ok",
        "snapshot_id": "f" * 64,
        "file_count": 7,
        "total_bytes": 99,
        "calibre_file_count": 2,
        "kindle_file_count": 5,
        "restore_verified": True,
    }


def test_backup_removes_partial_staging_when_backup_fails(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import koreader_ecosystem.cli as cli

    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path))
    monkeypatch.setattr(cli, "ResticClient", FakeRestic)
    monkeypatch.setattr("builtins.input", lambda: "s")
    monkeypatch.setattr("koreader_ecosystem.services.preflight_before_device_copy", lambda *_: None)
    staged_paths: list[Path] = []

    def fake_stage(destination: Path) -> object:
        destination.mkdir(parents=True)
        (destination / "partial.txt").write_text("staged", encoding="utf-8")
        staged_paths.append(destination)
        return SimpleNamespace(cleanup_paths=())

    monkeypatch.setattr("koreader_ecosystem.services.stage_kindle", fake_stage)
    monkeypatch.setattr(
        "koreader_ecosystem.services.create_verified_backup",
        lambda *_, **__: (_ for _ in ()).throw(cli.BackupError("backup failed")),
    )

    exit_code = cli.main(["backup"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload == {"status": "error", "reason_code": "backup_or_restore_failed"}
    assert len(staged_paths) == 1
    assert not staged_paths[0].exists()


def test_restore_output_contains_only_aggregate_result(tmp_path: Path, monkeypatch, capsys) -> None:
    import koreader_ecosystem.cli as cli

    config = _config(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "ResticClient", FakeRestic)
    captured: dict[str, object] = {}

    def fake_restore(snapshot: str, target: Path, restic: FakeRestic, sources=()) -> RestoreResult:
        target.mkdir(parents=True)
        captured.update({"snapshot": snapshot, "target": target, "sources": sources})
        return RestoreResult(snapshot, 2, 7, ())

    monkeypatch.setattr("koreader_ecosystem.services.validate_snapshot_restore", fake_restore)
    target = tmp_path / "new-restore"

    exit_code = cli.main(["restore", "--snapshot", "e" * 64, "--target", str(target)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured["snapshot"] == "e" * 64
    assert captured["target"] == target
    assert payload == {
        "status": "ok",
        "snapshot_id": "e" * 64,
        "file_count": 2,
        "total_bytes": 7,
    }
    assert str(config.calibre_library) not in json.dumps(payload)


def test_doctor_reports_aggregates_without_local_paths(tmp_path: Path, monkeypatch, capsys) -> None:
    import koreader_ecosystem.cli as cli

    config = _config(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "ResticClient", FakeRestic)
    monkeypatch.setattr("koreader_ecosystem.services._running_process_names", lambda: [])
    monkeypatch.setattr("koreader_ecosystem.services._check_calibre_integrity", lambda _: None)
    monkeypatch.setattr("koreader_ecosystem.services.restic_password_is_stored", lambda: True)
    monkeypatch.setattr(
        "koreader_ecosystem.services.shutil.disk_usage",
        lambda _: SimpleNamespace(free=10**9),
    )
    monkeypatch.setattr(
        "koreader_ecosystem.services.detect_kindle",
        lambda: MtpDeviceSummary(("documents", "koreader"), 5, 2, 4096),
    )

    exit_code = cli.main(["doctor"])
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["checks"]["kindle"]["file_count"] == 5
    assert str(config.calibre_library) not in rendered
    assert str(config.restic_repository) not in rendered


def test_hardcover_configure_hides_token_from_json_output(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import koreader_ecosystem.cli as cli

    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path))
    monkeypatch.setattr(cli, "ResticClient", FakeRestic)
    monkeypatch.setattr(cli, "store_hardcover_token", lambda: None)
    monkeypatch.setattr(cli, "load_hardcover_token", lambda: "private-token")
    monkeypatch.setattr("koreader_ecosystem.services.load_hardcover_token", lambda: "private-token")
    monkeypatch.setattr("koreader_ecosystem.services.save_hardcover_token", lambda _: None)

    class FakeHardcoverClient:
        def verify_token(self, token: str) -> None:
            assert token == "private-token"

    monkeypatch.setattr(cli, "HardcoverClient", FakeHardcoverClient)

    exit_code = cli.main(["hardcover", "configure"])
    rendered = capsys.readouterr().out

    assert exit_code == 0, rendered
    assert json.loads(rendered) == {"status": "ok", "authentication": "verified"}
    assert "private-token" not in rendered


def test_hardcover_configure_reports_credential_errors_as_hardcover(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import koreader_ecosystem.cli as cli

    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path))
    monkeypatch.setattr(cli, "ResticClient", FakeRestic)

    def fail_prompt() -> None:
        raise cli.SecretStoreError(
            "Hardcover token prompt was cancelled",
            reason_code="hardcover_token_unavailable",
        )

    monkeypatch.setattr(cli, "store_hardcover_token", fail_prompt)

    exit_code = cli.main(["hardcover", "configure"])
    rendered = capsys.readouterr().out

    assert exit_code == 2
    assert json.loads(rendered) == {
        "status": "error",
        "reason_code": "hardcover_token_unavailable",
    }


def test_hardcover_auth_error_is_sanitized(tmp_path: Path, monkeypatch, capsys) -> None:
    import koreader_ecosystem.cli as cli

    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path))
    monkeypatch.setattr(cli, "ResticClient", FakeRestic)
    monkeypatch.setattr("koreader_ecosystem.services.load_hardcover_token", lambda: "private-token")

    class FakeHardcoverClient:
        def verify_token(self, _: str) -> None:
            raise cli.HardcoverError("authentication_failed")

    monkeypatch.setattr(cli, "HardcoverClient", FakeHardcoverClient)

    exit_code = cli.main(["hardcover", "auth-check"])
    rendered = capsys.readouterr().out

    assert exit_code == 2
    assert json.loads(rendered) == {
        "status": "error",
        "reason_code": "hardcover_authentication_failed",
    }
    assert "private-token" not in rendered


def test_remote_configure_stores_credentials_and_saves_only_public_settings(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import koreader_ecosystem.cli as cli

    config = _config(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "ResticClient", FakeRestic)
    calls: list[str] = []
    monkeypatch.setattr(cli, "store_s3_credentials", lambda: calls.append("credentials"))
    monkeypatch.setattr(
        "koreader_ecosystem.services.load_s3_credentials",
        lambda: SimpleNamespace(access_key_id="access", secret_access_key="secret"),
    )
    monkeypatch.setattr("koreader_ecosystem.services.save_s3_credentials", lambda *_: None)
    saved: list[AppConfig] = []
    monkeypatch.setattr(
        "koreader_ecosystem.services.save_config", lambda value, _: saved.append(value)
    )

    exit_code = cli.main(
        [
            "remote",
            "configure",
            "--repository",
            "s3:https://account.r2.cloudflarestorage.com/books/reading-ecosystem",
            "--region",
            "auto",
        ]
    )
    rendered = capsys.readouterr().out

    assert exit_code == 0
    assert calls == ["credentials"]
    assert saved[0].restic_remote_region == "auto"
    assert saved[0].restic_remote_repository == (
        "s3:https://account.r2.cloudflarestorage.com/books/reading-ecosystem"
    )
    assert json.loads(rendered) == {
        "status": "ok",
        "remote_configured": True,
        "credentials_stored": True,
    }


def test_remote_sync_runs_only_after_explicit_command_and_reports_counts(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import koreader_ecosystem.cli as cli

    config = AppConfig(
        calibre_library=tmp_path / "private-library",
        restic_repository=tmp_path / "private-repository",
        state_root=tmp_path / "private-state",
        restic_remote_repository=(
            "s3:https://account.r2.cloudflarestorage.com/books/reading-ecosystem"
        ),
        restic_remote_region="auto",
    )
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    clients: list[tuple[object, ...]] = []
    instances: list[object] = []

    class RecordingRestic:
        def __init__(self, *args: object, **kwargs: object) -> None:
            clients.append((*args, kwargs))
            instances.append(self)

    monkeypatch.setattr(cli, "ResticClient", RecordingRestic)
    monkeypatch.setattr("koreader_ecosystem.services.ResticClient", RecordingRestic)
    captured: list[tuple[object, object]] = []

    def fake_sync(source: object, destination: object) -> object:
        captured.append((source, destination))
        return type("Result", (), {"source_snapshot_count": 3, "remote_snapshot_count": 4})()

    monkeypatch.setattr("koreader_ecosystem.services.mirror_remote", fake_sync)

    exit_code = cli.main(["remote", "sync"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert len(clients) == 2
    assert clients[1][0] == config.restic_remote_repository
    assert clients[1][1] == {"s3_region": "auto"}
    assert captured == [(instances[0], instances[1])]
    assert payload == {
        "status": "ok",
        "source_snapshot_count": 3,
        "remote_snapshot_count": 4,
    }


def test_remote_sync_reports_sanitized_failure(tmp_path: Path, monkeypatch, capsys) -> None:
    import koreader_ecosystem.cli as cli

    config = AppConfig(
        calibre_library=tmp_path / "private-library",
        restic_repository=tmp_path / "private-repository",
        state_root=tmp_path / "private-state",
        restic_remote_repository=(
            "s3:https://account.r2.cloudflarestorage.com/books/reading-ecosystem"
        ),
        restic_remote_region="auto",
    )
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "ResticClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        "koreader_ecosystem.services.ResticClient", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        "koreader_ecosystem.services.mirror_remote",
        lambda *_: (_ for _ in ()).throw(cli.RemoteSyncError("remote_snapshot_copy_failed")),
    )

    exit_code = cli.main(["remote", "sync"])
    rendered = capsys.readouterr().out

    assert exit_code == 2
    assert json.loads(rendered) == {
        "status": "error",
        "reason_code": "remote_snapshot_copy_failed",
    }
    assert "private-library" not in rendered


def test_koreader_preview_removes_staging_after_success_and_parse_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import koreader_ecosystem.cli as cli
    from koreader_ecosystem.calibre_catalog import CalibreBook

    config = _config(tmp_path)
    book = CalibreBook(7, "0f8fad5b-d9cb-469f-a165-70867728950e", "Livro", ("Autora",), {}, {})
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "ResticClient", FakeRestic)
    monkeypatch.setattr("koreader_ecosystem.services._running_process_names", lambda: [])
    monkeypatch.setattr("koreader_ecosystem.services._check_calibre_integrity", lambda _: None)
    monkeypatch.setattr("koreader_ecosystem.services.load_calibre_catalog", lambda _: (book,))
    staged: list[Path] = []

    def stage(destination: Path) -> object:
        destination.mkdir(parents=True)
        sidecar = destination / "Books" / "Exemplo.sdr" / "metadata.epub.lua"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(
            'return { ["stats"] = { ["identifiers"] = "calibre:'
            + book.uuid
            + '" }, ["summary"] = { ["status"] = "reading" } }'
            if not staged
            else 'return { ["bad"] = os.execute("whoami") }',
            encoding="utf-8",
        )
        staged.append(destination)
        return SimpleNamespace(cleanup_paths=())

    monkeypatch.setattr("koreader_ecosystem.services.stage_koreader_reading_data", stage)

    success = cli.main(["koreader-calibre", "preview"])
    success_payload = json.loads(capsys.readouterr().out)
    parse_error = cli.main(["koreader-calibre", "preview"])
    error_payload = json.loads(capsys.readouterr().out)

    assert success == 0
    assert success_payload["status"] == "ok"
    assert Path(success_payload["report_path"]).is_file()
    assert parse_error == 0
    assert error_payload["status"] == "ok"
    assert error_payload["pending_count"] == 1
    assert len(staged) == 2
    assert all(not path.exists() for path in staged)


def test_koreader_preview_removes_partial_staging_when_mtp_fails(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import koreader_ecosystem.cli as cli

    config = _config(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "ResticClient", FakeRestic)
    monkeypatch.setattr("koreader_ecosystem.services._running_process_names", lambda: [])
    monkeypatch.setattr("koreader_ecosystem.services._check_calibre_integrity", lambda _: None)
    staged: list[Path] = []

    def fail_after_partial_copy(destination: Path) -> object:
        destination.mkdir(parents=True)
        (destination / "partial.dat").write_text("private", encoding="utf-8")
        staged.append(destination)
        raise cli.MtpCopyError("copy_failed")

    monkeypatch.setattr(
        "koreader_ecosystem.services.stage_koreader_reading_data", fail_after_partial_copy
    )

    exit_code = cli.main(["koreader-calibre", "preview"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload == {"status": "error", "reason_code": "kindle_copy_failed"}
    assert len(staged) == 1
    assert not staged[0].exists()


def test_koreader_map_persists_mapping_and_rejects_duplicate_target_ids(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import koreader_ecosystem.cli as cli
    from koreader_ecosystem.calibre_catalog import CalibreBook
    from koreader_ecosystem.sync_state import SyncState

    config = _config(tmp_path)
    book = CalibreBook(7, "0f8fad5b-d9cb-469f-a165-70867728950e", "Livro", ("Autora",), {}, {})
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "ResticClient", FakeRestic)
    monkeypatch.setattr("koreader_ecosystem.services.load_calibre_catalog", lambda _: (book,))
    legacy_database = config.state_root / "koreader-calibre.sqlite3"
    state = SyncState(config.state_root / "application-state.sqlite3")
    keys = ("a" * 64, "b" * 64)
    state.save_preview(
        "c" * 64,
        "c" * 64,
        "2026-10-23T12:00:00+00:00",
        [
            {
                "sidecar_key": key,
                "relative_path": f"Books/{index}.sdr/metadata.epub.lua",
                "source_digest": str(index) * 64,
                "match_status": "unmatched",
                "reason_code": "calibre_uuid_missing",
                "book_id": None,
                "calibre_uuid": None,
                "title": None,
                "old_values": {},
                "new_values": {},
                "warnings": [],
                "applied_values": {},
                "applied_at": None,
            }
            for index, key in enumerate(keys, start=1)
        ],
    )

    first = cli.main(
        [
            "koreader-calibre",
            "map",
            "--preview-id",
            "c" * 64,
            "--sidecar-key",
            keys[0],
            "--book-id",
            "7",
        ]
    )
    first_payload = json.loads(capsys.readouterr().out)
    duplicate = cli.main(
        [
            "koreader-calibre",
            "map",
            "--preview-id",
            "c" * 64,
            "--sidecar-key",
            keys[1],
            "--book-id",
            "7",
        ]
    )
    duplicate_payload = json.loads(capsys.readouterr().out)

    assert first == 0
    assert first_payload["status"] == "ok"
    assert state.manual_mappings() == {keys[0]: 7}
    assert not legacy_database.exists()
    assert duplicate == 2
    assert duplicate_payload == {"status": "error", "reason_code": "duplicate_manual_target"}


def test_calibre_only_backup_does_not_stage_the_kindle(tmp_path: Path, monkeypatch, capsys) -> None:
    import koreader_ecosystem.cli as cli

    config = _config(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "ResticClient", FakeRestic)
    monkeypatch.setattr("builtins.input", lambda: "sim")
    monkeypatch.setattr(
        "koreader_ecosystem.services.stage_kindle",
        lambda *_: (_ for _ in ()).throw(AssertionError("Kindle must not be staged")),
    )
    monkeypatch.setattr(
        "koreader_ecosystem.services.create_library_backup",
        lambda *_: BackupResult("c" * 64, 9, 1234, 9, 0),
    )

    exit_code = cli.main(["backup", "--calibre-only"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload == {
        "status": "ok",
        "snapshot_id": "c" * 64,
        "file_count": 9,
        "total_bytes": 1234,
        "calibre_file_count": 9,
        "kindle_file_count": 0,
        "restore_verified": False,
    }


def test_koreader_apply_removes_staging_after_application(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import koreader_ecosystem.cli as cli

    config = _config(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "ResticClient", FakeRestic)
    monkeypatch.setattr("koreader_ecosystem.services._running_process_names", lambda: [])
    monkeypatch.setattr("koreader_ecosystem.services._check_calibre_integrity", lambda _: None)
    staged: list[Path] = []

    def stage(destination: Path) -> object:
        destination.mkdir(parents=True)
        (destination / "fixture").write_text("private", encoding="utf-8")
        staged.append(destination)
        return SimpleNamespace(cleanup_paths=())

    monkeypatch.setattr("koreader_ecosystem.services.stage_koreader_reading_data", stage)
    monkeypatch.setattr(
        "koreader_ecosystem.services.apply_preview",
        lambda *_args: __import__(
            "koreader_ecosystem.calibre_apply", fromlist=["ApplyResult"]
        ).ApplyResult("f" * 64, 2, 5),
    )

    exit_code = cli.main(["koreader-calibre", "apply", "--preview-id", "d" * 64])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload == {
        "status": "ok",
        "snapshot_id": "f" * 64,
        "applied_book_count": 2,
        "applied_field_count": 5,
    }
    assert len(staged) == 1
    assert not staged[0].exists()


def test_doctor_outputs_only_credential_presence_not_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import koreader_ecosystem.cli as cli
    import koreader_ecosystem.secrets as secrets

    config = _config(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "ResticClient", FakeRestic)
    monkeypatch.setattr("koreader_ecosystem.services._running_process_names", lambda: [])
    monkeypatch.setattr("koreader_ecosystem.services._check_calibre_integrity", lambda _: None)
    monkeypatch.setattr(
        "koreader_ecosystem.services.detect_kindle",
        lambda: MtpDeviceSummary(("documents",), 1, 1, 10),
    )
    monkeypatch.setattr(secrets, "_windows_credential_backend", lambda: object())
    monkeypatch.setattr(
        secrets, "_get_password", lambda _backend, _username: "synthetic-private-value"
    )
    monkeypatch.setattr(
        ApplicationServices,
        "_hardcover_token_available",
        lambda _: secrets.hardcover_token_is_stored(),
    )

    cli.main(["doctor"])
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    credentials = result["checks"]["credentials"]
    assert credentials["restic_password_stored"] is True
    assert credentials["hardcover_token_stored"] is True
    assert "synthetic-private-value" not in captured.out + captured.err
