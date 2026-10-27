from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from koreader_ecosystem.application_state import ApplicationState
from koreader_ecosystem.backup import (
    BackupError,
    BackupResult,
    RestoreResult,
    _copy_application_state,
    create_backup,
    create_calibre_backup,
    preflight_before_device_copy,
    validate_restore,
)
from koreader_ecosystem.config import AppConfig
from koreader_ecosystem.hardcover import HardcoverRequestBudget
from koreader_ecosystem.manifest import ManifestError, build_manifest
from koreader_ecosystem.mtp import MtpStageSummary
from koreader_ecosystem.restic import ResticResult

_SNAPSHOT_ID = "c" * 64


class FakeRestic:
    def __init__(
        self, *, password_error: Exception | None = None, snapshots_ok: bool = False
    ) -> None:
        self.password_error = password_error
        self.snapshots_ok = snapshots_ok
        self.password_checks = 0
        self.calls: list[list[str]] = []
        self.working_dirs: list[Path | None] = []
        self.fail_command: str | None = None

    def ensure_password(self) -> None:
        self.password_checks += 1
        if self.password_error:
            raise self.password_error

    def run(self, args: list[str], *, cwd: Path | None = None) -> ResticResult:
        self.calls.append(args)
        self.working_dirs.append(cwd)
        if args[0] == "snapshots":
            return ResticResult(returncode=0 if self.snapshots_ok else 10)
        if args[0] == self.fail_command:
            return ResticResult(returncode=1)
        if args[0] == "restore":
            target = Path(args[args.index("--target") + 1])
            target.mkdir(parents=True, exist_ok=True)
            (target / "restored.txt").write_text("restored", encoding="utf-8")
            return ResticResult(returncode=0)
        if args[0] == "backup":
            return ResticResult(returncode=0, summary={"snapshot_id": _SNAPSHOT_ID})
        return ResticResult(returncode=0)


class PartialRestoreRestic(FakeRestic):
    def run(self, args: list[str], *, cwd: Path | None = None) -> ResticResult:
        self.calls.append(args)
        self.working_dirs.append(cwd)
        if args[0] == "restore":
            target = Path(args[args.index("--target") + 1])
            target.mkdir(parents=True, exist_ok=True)
            (target / "partial.txt").write_text("partial", encoding="utf-8")
            return ResticResult(returncode=1)
        return super().run(args, cwd=cwd)


def _sources(tmp_path: Path) -> tuple[AppConfig, MtpStageSummary]:
    library = tmp_path / "calibre-library"
    library.mkdir()
    connection = sqlite3.connect(library / "metadata.db")
    connection.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)")
    connection.execute("INSERT INTO books (title) VALUES ('fixture')")
    connection.commit()
    connection.close()
    (library / "book.txt").write_text("calibre source", encoding="utf-8")

    stage = tmp_path / "stage"
    (stage / "koreader").mkdir(parents=True)
    (stage / "koreader" / "config.lua").write_bytes(b"abc")
    manifest = build_manifest(stage)
    device = MtpStageSummary(
        destination=stage,
        roots=("koreader",),
        file_count=1,
        directory_count=1,
        total_bytes=3,
        manifest=manifest,
    )
    config = AppConfig(
        calibre_library=library,
        restic_repository=tmp_path / "backups" / "reading-ecosystem-restic",
        state_root=tmp_path / "state",
    )
    return config, device


def _make_disk_available(monkeypatch: pytest.MonkeyPatch, free: int = 10**12) -> None:
    monkeypatch.setattr(
        "koreader_ecosystem.backup.shutil.disk_usage",
        lambda _: SimpleNamespace(total=free, used=0, free=free),
    )


def test_application_state_backup_preserves_hardcover_request_budget(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    request_db = state_root / "hardcover-requests.sqlite3"
    budget = HardcoverRequestBudget(request_db, daily_limit=5)
    budget.reserve()
    budget.reserve()
    destination = tmp_path / "snapshot-state"
    destination.mkdir()

    assert _copy_application_state(state_root, destination)

    with sqlite3.connect(destination / request_db.name) as restored:
        count = restored.execute("SELECT COUNT(*) FROM hardcover_requests").fetchone()[0]
    assert count == 2


def test_application_state_backup_preserves_versioned_database_records(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    state = ApplicationState(state_root / "application-state.sqlite3")
    with state.connect() as connection:
        connection.execute(
            """
            INSERT INTO book_identities (
                identity_id, provider, external_type, external_id, created_at, updated_at
            ) VALUES ('identity-1', 'hardcover', 'book', 'book-42', '2026-10-25', '2026-10-25')
            """
        )
    destination = tmp_path / "snapshot-state"
    destination.mkdir()

    assert _copy_application_state(state_root, destination)

    with sqlite3.connect(destination / "application-state.sqlite3") as restored:
        assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert restored.execute("PRAGMA user_version").fetchone() == (6,)
        assert restored.execute(
            "SELECT provider, external_type, external_id FROM book_identities"
        ).fetchone() == ("hardcover", "book", "book-42")


def test_relative_backup_sources_refuses_different_volumes() -> None:
    import koreader_ecosystem.backup as backup_module

    with pytest.raises(BackupError):
        backup_module._relative_backup_sources(
            (Path("C:/calibre-library"), Path("D:/kindle-stage"))
        )


def test_relative_backup_sources_uses_parent_for_single_source(tmp_path: Path) -> None:
    import koreader_ecosystem.backup as backup_module

    library = tmp_path / "calibre-library"

    root, relative_paths = backup_module._relative_backup_sources((library,))

    assert root == tmp_path
    assert relative_paths == ("calibre-library",)


def test_create_backup_initializes_checks_and_returns_sanitized_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, device = _sources(tmp_path)
    restic = FakeRestic()
    monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])
    _make_disk_available(monkeypatch)

    result = create_backup(config, device, restic)

    assert isinstance(result, BackupResult)
    assert result.snapshot_id == _SNAPSHOT_ID
    assert result.file_count == 3
    assert result.total_bytes == (
        3 + len("calibre source") + config.calibre_library.joinpath("metadata.db").stat().st_size
    )
    assert restic.password_checks == 1
    assert [call[0] for call in restic.calls] == ["init", "backup", "check"]
    assert "calibre-library" in restic.calls[1]
    assert "stage" in restic.calls[1]
    assert str(config.calibre_library) not in restic.calls[1]
    assert str(device.destination) not in restic.calls[1]
    assert restic.working_dirs[1] == tmp_path


def test_create_backup_aborts_when_calibre_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, device = _sources(tmp_path)
    restic = FakeRestic()
    monkeypatch.setattr(
        "koreader_ecosystem.backup._running_process_names",
        lambda: ["calibre.exe"],
    )
    _make_disk_available(monkeypatch)

    with pytest.raises(BackupError):
        create_backup(config, device, restic)

    assert restic.password_checks == 0
    assert restic.calls == []


def test_create_backup_aborts_on_sqlite_integrity_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, device = _sources(tmp_path)
    restic = FakeRestic()
    monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])
    monkeypatch.setattr(
        "koreader_ecosystem.backup._check_calibre_integrity",
        lambda _: (_ for _ in ()).throw(BackupError("database invalid")),
    )
    _make_disk_available(monkeypatch)

    with pytest.raises(BackupError):
        create_backup(config, device, restic)

    assert restic.calls == []


def test_create_backup_aborts_on_incomplete_mtp_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, device = _sources(tmp_path)
    incomplete = MtpStageSummary(
        destination=device.destination,
        roots=device.roots,
        file_count=device.file_count + 1,
        directory_count=device.directory_count,
        total_bytes=device.total_bytes,
        manifest=device.manifest,
    )
    restic = FakeRestic()
    monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])
    _make_disk_available(monkeypatch)

    with pytest.raises(BackupError):
        create_backup(config, incomplete, restic)

    assert restic.password_checks == 0
    assert restic.calls == []


def test_create_backup_aborts_if_staging_changes_after_manifest_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, device = _sources(tmp_path)
    (device.destination / "koreader" / "config.lua").write_bytes(b"changed")
    restic = FakeRestic()
    monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])
    _make_disk_available(monkeypatch)

    with pytest.raises(BackupError):
        create_backup(config, device, restic)

    assert restic.password_checks == 0
    assert restic.calls == []


def test_create_backup_aborts_when_disk_space_is_insufficient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, device = _sources(tmp_path)
    restic = FakeRestic()
    monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])
    _make_disk_available(monkeypatch, free=0)

    with pytest.raises(BackupError):
        create_backup(config, device, restic)

    assert restic.password_checks == 0
    assert restic.calls == []


def test_create_backup_does_not_initialize_without_a_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, device = _sources(tmp_path)
    restic = FakeRestic(password_error=RuntimeError("private password text"))
    monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])
    _make_disk_available(monkeypatch)

    with pytest.raises(BackupError) as error:
        create_backup(config, device, restic)

    assert "private password text" not in str(error.value)
    assert restic.calls == []


def test_create_calibre_backup_snapshots_only_the_library_and_checks_restic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _ = _sources(tmp_path)
    restic = FakeRestic()
    monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])
    monkeypatch.setattr("koreader_ecosystem.backup._check_calibre_integrity", lambda _: None)
    _make_disk_available(monkeypatch)

    result = create_calibre_backup(config, restic)

    assert result.snapshot_id == _SNAPSHOT_ID
    assert result.calibre_file_count == result.file_count
    assert result.kindle_file_count == 0
    assert [call[0] for call in restic.calls] == ["init", "backup", "check"]
    backup_call = restic.calls[1]
    assert backup_call[-1] == "calibre-library"
    assert backup_call.count("--tag") == 2
    assert "calibre-only" in backup_call
    assert restic.working_dirs[1] == config.calibre_library.parent


def test_calibre_backup_passes_application_state_into_restore_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.backup as backup_module

    config, _ = _sources(tmp_path)
    state = ApplicationState(config.state_root / "application-state.sqlite3")
    with state.connect() as connection:
        connection.execute(
            "INSERT INTO operations(operation_key, status, updated_at) "
            "VALUES ('local_backup', 'success', '2026-10-25T12:00:00+00:00')"
        )
    restic = FakeRestic()
    monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])
    monkeypatch.setattr("koreader_ecosystem.backup._check_calibre_integrity", lambda _: None)
    _make_disk_available(monkeypatch)
    restore_inputs: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        backup_module,
        "_verify_snapshot_restore",
        lambda *args: restore_inputs.append(args),
    )

    result = create_calibre_backup(config, restic, verify_restore=True)

    assert result.restore_verified is True
    assert result.application_state_file_count >= 1
    assert len(restore_inputs) == 1
    manifests = restore_inputs[0][4]
    assert any(
        entry.path == "application-state.sqlite3" for entry in manifests[-1] if entry.kind == "file"
    )
    assert any("application-state" in argument for argument in restic.calls[1])


def test_create_calibre_backup_does_not_report_success_when_restic_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _ = _sources(tmp_path)
    restic = FakeRestic()
    restic.fail_command = "check"
    monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])
    monkeypatch.setattr("koreader_ecosystem.backup._check_calibre_integrity", lambda _: None)
    _make_disk_available(monkeypatch)

    with pytest.raises(BackupError):
        create_calibre_backup(config, restic)

    assert [call[0] for call in restic.calls] == ["init", "backup", "check"]


def test_create_backup_does_not_report_success_when_restic_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, device = _sources(tmp_path)
    restic = FakeRestic()
    restic.fail_command = "check"
    monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])
    _make_disk_available(monkeypatch)

    with pytest.raises(BackupError):
        create_backup(config, device, restic)

    assert [call[0] for call in restic.calls] == ["init", "backup", "check"]


def test_create_backup_can_verify_the_snapshot_by_restoring_and_comparing_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.backup as backup_module

    config, device = _sources(tmp_path)
    restic = FakeRestic()
    monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])
    _make_disk_available(monkeypatch)
    verified: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        backup_module,
        "_verify_snapshot_restore",
        lambda *args: verified.append(args),
    )

    result = create_backup(config, device, restic, verify_restore=True)

    assert result.restore_verified is True
    assert len(verified) == 1
    assert [call[0] for call in restic.calls] == ["init", "backup", "check"]


def test_create_backup_uses_a_valid_existing_repository_without_initializing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, device = _sources(tmp_path)
    config.restic_repository.mkdir(parents=True)
    (config.restic_repository / "config").write_text("initialized", encoding="utf-8")
    restic = FakeRestic(snapshots_ok=True)
    monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])
    _make_disk_available(monkeypatch)

    create_backup(config, device, restic)

    assert [call[0] for call in restic.calls] == ["snapshots", "backup", "check"]


def test_create_backup_refuses_to_initialize_an_existing_unrecognized_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, device = _sources(tmp_path)
    config.restic_repository.mkdir(parents=True)
    (config.restic_repository / "unrelated.txt").write_text("keep", encoding="utf-8")
    restic = FakeRestic()
    monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])
    _make_disk_available(monkeypatch)

    with pytest.raises(BackupError):
        create_backup(config, device, restic)

    assert [call[0] for call in restic.calls] == ["snapshots"]


def test_preflight_before_device_copy_rejects_open_calibre_and_missing_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _ = _sources(tmp_path)
    restic = FakeRestic()
    monkeypatch.setattr(
        "koreader_ecosystem.backup._running_process_names",
        lambda: ["calibre.exe"],
    )

    with pytest.raises(BackupError):
        preflight_before_device_copy(config, restic)
    assert restic.password_checks == 0

    monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])
    restic.password_error = RuntimeError("private password text")
    with pytest.raises(BackupError) as error:
        preflight_before_device_copy(config, restic)
    assert "private password text" not in str(error.value)


def test_preflight_refuses_restic_repository_inside_local_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _ = _sources(tmp_path)
    unsafe_config = AppConfig(
        calibre_library=config.calibre_library,
        restic_repository=config.state_root / "repository",
        state_root=config.state_root,
    )
    restic = FakeRestic()
    monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])

    with pytest.raises(BackupError):
        preflight_before_device_copy(unsafe_config, restic)

    assert restic.password_checks == 0
    assert restic.calls == []


def test_validate_restore_restores_only_to_a_new_separate_directory(
    tmp_path: Path,
) -> None:
    config, _ = _sources(tmp_path)
    target = tmp_path / "new-parent" / "restore-output"
    restic = FakeRestic()

    result = validate_restore(
        _SNAPSHOT_ID,
        target,
        restic,
        sources=(config.calibre_library,),
    )

    assert isinstance(result, RestoreResult)
    assert result.snapshot_id == _SNAPSHOT_ID
    assert result.file_count == 1
    assert result.total_bytes == len("restored")
    assert tuple(entry.path for entry in result.manifest) == ("restored.txt",)
    assert restic.calls == [["restore", _SNAPSHOT_ID, "--target", str(target), "--json"]]


def test_validate_restore_refuses_existing_or_overlapping_targets(tmp_path: Path) -> None:
    config, _ = _sources(tmp_path)
    existing = tmp_path / "existing"
    existing.mkdir()
    restic = FakeRestic()

    with pytest.raises(BackupError):
        validate_restore(_SNAPSHOT_ID, existing, restic)
    with pytest.raises(BackupError):
        validate_restore(
            _SNAPSHOT_ID,
            config.calibre_library / "restore",
            restic,
            sources=(config.calibre_library,),
        )

    assert restic.calls == []


def test_validate_restore_removes_only_its_partial_target_on_restic_failure(
    tmp_path: Path,
) -> None:
    target = tmp_path / "new-parent" / "partial-restore"
    restic = PartialRestoreRestic()

    with pytest.raises(BackupError):
        validate_restore(_SNAPSHOT_ID, target, restic)

    assert not target.exists()
    assert not target.parent.exists()


def test_validate_restore_removes_target_when_manifest_validation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "invalid-restore"
    restic = FakeRestic()

    def fail_manifest(_: Path):
        raise ManifestError("synthetic invalid restore")

    monkeypatch.setattr("koreader_ecosystem.backup.build_manifest", fail_manifest)
    with pytest.raises(BackupError):
        validate_restore(_SNAPSHOT_ID, target, restic)

    assert not target.exists()


def test_validate_restore_rejects_invalid_snapshot_id_before_restic(tmp_path: Path) -> None:
    target = tmp_path / "restore"
    restic = FakeRestic()

    with pytest.raises(BackupError):
        validate_restore("latest;bad", target, restic)

    assert not target.exists()
    assert restic.calls == []


def test_application_state_backup_preserves_saved_automation_preferences(tmp_path: Path) -> None:
    import json

    from koreader_ecosystem.config import save_config

    state = tmp_path / "state"
    state.mkdir()
    destination = tmp_path / "snapshot"
    destination.mkdir()
    config = AppConfig(
        calibre_library=tmp_path / "library",
        restic_repository=tmp_path / "backup",
        state_root=state,
        daily_backup_enabled=True,
        daily_backup_time="21:30",
        backup_on_kindle_connect=True,
    )
    save_config(config, state / "config.json")
    assert _copy_application_state(state, destination)
    saved = json.loads((destination / "config.json").read_text(encoding="utf-8"))
    assert saved["daily_backup_enabled"] is True
    assert saved["daily_backup_time"] == "21:30"
    assert saved["backup_on_kindle_connect"] is True
