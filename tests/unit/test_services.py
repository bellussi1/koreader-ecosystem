from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from koreader_ecosystem.application_state import ApplicationState
from koreader_ecosystem.backup_automation import ProcessOperationLock
from koreader_ecosystem.config import AppConfig
from koreader_ecosystem.hardcover import HardcoverRequestBudget
from koreader_ecosystem.restic import ResticResult, ResticSnapshot
from koreader_ecosystem.services import ApplicationServices, ServiceError


class FakeRestic:
    available = True

    def __init__(self, repository: Path | str = Path("repository"), **_: object) -> None:
        self.repository = repository
        self.calls: list[tuple[str, ...]] = []

    def run(self, args: list[str] | tuple[str, ...], **_: object) -> ResticResult:
        self.calls.append(tuple(args))
        return ResticResult(
            returncode=0,
            snapshots=(ResticSnapshot("a" * 64, "2026-10-23T12:00:00+00:00"),),
        )

    def ensure_password(self) -> None:
        return None


def _config(tmp_path: Path, *, remote: bool = False) -> AppConfig:
    return AppConfig(
        calibre_library=tmp_path / "private-library",
        restic_repository=tmp_path / "private-repository",
        state_root=tmp_path / "private-state",
        restic_remote_repository=(
            "s3:https://account.example.test/bucket/reading-ecosystem" if remote else None
        ),
        restic_remote_region="auto" if remote else None,
    )


@contextmanager
def _another_thread_holds_operation_lock(state_root: Path) -> Iterator[None]:
    entered = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def hold_lock() -> None:
        try:
            with ProcessOperationLock(state_root):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test_lock_release_timeout")
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    if not entered.wait(2):
        release.set()
        thread.join(5)
        raise AssertionError("test_lock_acquire_timeout")
    try:
        yield
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert errors == []


def test_status_returns_only_snapshot_summaries(tmp_path: Path, monkeypatch) -> None:
    import koreader_ecosystem.services as services

    restic = FakeRestic()
    monkeypatch.setattr(services, "restic_password_is_stored", lambda: True)

    result = ApplicationServices(_config(tmp_path), restic=restic).status()

    assert result == ({"id": "a" * 64, "time": "2026-10-23T12:00:00+00:00"},)
    assert restic.calls == [("snapshots", "--json")]
    assert str(tmp_path) not in repr(result)


def test_backup_list_keeps_legacy_and_current_product_snapshots_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.services as services

    restic = FakeRestic()
    restic.run = lambda *_args, **_kwargs: ResticResult(
        0,
        snapshots=(
            ResticSnapshot("a" * 64, "2026-10-25T12:00:00Z", tags=("koreader-ecosystem",)),
            ResticSnapshot("b" * 64, "2026-10-24T12:00:00Z", tags=("kindle-reading-ecosystem",)),
        ),
    )
    monkeypatch.setattr(services, "restic_password_is_stored", lambda: True)

    config = _config(tmp_path)
    config.restic_repository.mkdir()
    snapshots = ApplicationServices(config, restic=restic).backup_snapshots()

    assert [item["scope"] for item in snapshots] == [
        "calibre_and_kindle",
        "calibre_and_kindle",
    ]


def test_services_migrate_hardcover_quota_to_the_shared_state_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.hardcover as hardcover

    now = 1_800_000_000
    monkeypatch.setattr(hardcover.time, "time", lambda: now)
    config = _config(tmp_path)
    legacy_path = config.state_root / "hardcover-requests.sqlite3"
    legacy_budget = HardcoverRequestBudget(legacy_path)
    legacy_budget.reserve()
    original = legacy_path.read_bytes()

    service = ApplicationServices(config, restic=FakeRestic())

    assert service.hardcover.request_usage()["requests_used"] == 1
    with sqlite3.connect(config.state_root / "application-state.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM hardcover_requests WHERE requested_at = ?", (now,)
        ).fetchone() == (1,)
    assert legacy_path.read_bytes() == original


def test_status_does_not_run_restic_when_credential_is_missing(tmp_path: Path, monkeypatch) -> None:
    import koreader_ecosystem.services as services

    restic = FakeRestic()
    monkeypatch.setattr(services, "restic_password_is_stored", lambda: False)

    with pytest.raises(ServiceError, match="restic_password_unavailable"):
        ApplicationServices(_config(tmp_path), restic=restic).status()

    assert restic.calls == []


def test_backup_requires_recovery_confirmation_before_device_access(
    tmp_path: Path, monkeypatch
) -> None:
    import koreader_ecosystem.services as services

    monkeypatch.setattr(
        services,
        "preflight_before_device_copy",
        lambda *_: (_ for _ in ()).throw(AssertionError("preflight must not run")),
    )
    monkeypatch.setattr(
        services,
        "stage_kindle",
        lambda *_: (_ for _ in ()).throw(AssertionError("device access must not run")),
    )

    with pytest.raises(ServiceError, match="recovery_copy_unconfirmed"):
        ApplicationServices(_config(tmp_path), restic=FakeRestic()).create_backup(
            recovery_copy_confirmed=False
        )


def test_backup_automation_is_disabled_for_existing_profiles(tmp_path: Path) -> None:
    status = ApplicationServices(_config(tmp_path), restic=FakeRestic()).backup_automation_status()

    assert status["daily_backup_enabled"] is False
    assert status["backup_on_kindle_connect"] is False
    assert status["daily_backup_time"] == "20:00"
    assert status["last_backup"] is None


def test_manual_backup_updates_the_automation_status_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from koreader_ecosystem.backup import BackupResult

    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    expected = BackupResult("d" * 64, 3, 123, 3, 0, restore_verified=True)
    monkeypatch.setattr(service, "_create_backup_locked", lambda **_: expected)

    result = service.create_backup(recovery_copy_confirmed=True)

    assert result == expected
    status = service.backup_automation_status()
    assert status["last_backup"]["status"] == "success"
    assert status["last_backup"]["last_success_at"] == status["last_backup"]["updated_at"]


def test_calibre_edit_does_not_overlap_an_active_backup_or_reach_the_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.services as services

    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    backup_calls: list[bool] = []
    write_calls: list[bool] = []
    monkeypatch.setattr(service, "_backup_calibre_before_write", lambda: backup_calls.append(True))
    monkeypatch.setattr(
        services, "edit_library_metadata", lambda *_args, **_kwargs: write_calls.append(True)
    )

    with _another_thread_holds_operation_lock(service.config.state_root):
        with pytest.raises(ServiceError, match="backup_in_progress"):
            service.edit_calibre_metadata(
                book_id=1,
                changes={"title": "Updated"},
                recovery_copy_confirmed=True,
            )

    assert backup_calls == []
    assert write_calls == []


def test_koreader_apply_does_not_start_while_backup_is_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.services as services

    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    work_calls: list[str] = []
    monkeypatch.setattr(services, "require_calibre_closed", lambda *_: work_calls.append("calibre"))
    monkeypatch.setattr(services, "_check_calibre_integrity", lambda *_: None)
    monkeypatch.setattr(
        services, "stage_koreader_reading_data", lambda *_: work_calls.append("mtp")
    )
    monkeypatch.setattr(service, "_remove_stage_residuals", lambda *_: None)
    monkeypatch.setattr(
        services, "apply_preview", lambda *_args, **_kwargs: work_calls.append("apply")
    )

    with _another_thread_holds_operation_lock(service.config.state_root):
        with pytest.raises(ServiceError, match="backup_in_progress"):
            service.apply_koreader_preview("a" * 64, confirmed=True)

    assert work_calls == []


def test_calibre_reading_projection_does_not_overlap_an_active_backup(
    tmp_path: Path,
) -> None:
    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    with _another_thread_holds_operation_lock(service.config.state_root):
        with pytest.raises(ServiceError, match="backup_in_progress"):
            service.apply_calibre_reading_projection(
                "a" * 64,
                "b" * 64,
                confirmed=True,
            )


def test_profile_recovery_does_not_overlap_an_active_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    import koreader_ecosystem.services as services

    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    recovery_calls: list[bool] = []
    monkeypatch.setattr(
        services,
        "install_recovered_profile",
        lambda *_args, **_kwargs: (
            recovery_calls.append(True)
            or SimpleNamespace(checkpoint_id="checkpoint", schema_version=3, requires_restart=True)
        ),
    )

    with _another_thread_holds_operation_lock(service.config.state_root):
        with pytest.raises(ServiceError, match="backup_in_progress"):
            service.apply_profile_recovery(
                tmp_path / "restored-profile",
                "a" * 64,
                confirmed=True,
            )
    assert recovery_calls == []


def test_configuring_daily_backup_registers_interactive_task_and_persists_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.services as services

    calls: list[tuple[str, str]] = []

    class FakeTaskScheduler:
        def __init__(self, *, executable: Path) -> None:
            assert executable == Path("C:/Apps/KOReaderEcosystem.exe")

        def register_daily_backup(self, start_time: str) -> None:
            calls.append(("register", start_time))

        def remove_daily_backup(self) -> None:
            calls.append(("remove", ""))

    monkeypatch.setattr(services, "TaskScheduler", FakeTaskScheduler)
    monkeypatch.setattr(services, "restic_password_is_stored", lambda: True)
    monkeypatch.setenv("KOREADER_ECOSYSTEM_SCHEDULER_EXECUTABLE", "C:/Apps/KOReaderEcosystem.exe")
    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())

    status = service.configure_backup_automation(
        daily_backup_enabled=True,
        daily_backup_time="06:30",
        backup_on_kindle_connect=False,
    )

    assert calls == [("register", "06:30")]
    assert status["daily_backup_enabled"] is True
    assert (
        json.loads((service.config.state_root / "config.json").read_text())["daily_backup_time"]
        == "06:30"
    )


def test_disabling_future_backup_task_does_not_kill_an_active_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.services as services

    calls: list[str] = []

    class FakeTaskScheduler:
        def __init__(self, *, executable: Path) -> None:
            del executable

        def register_daily_backup(self, start_time: str) -> None:
            calls.append("register")

        def remove_daily_backup(self) -> None:
            calls.append("remove_future_runs")

    monkeypatch.setattr(services, "TaskScheduler", FakeTaskScheduler)
    monkeypatch.setattr(services, "restic_password_is_stored", lambda: True)
    monkeypatch.setenv("KOREADER_ECOSYSTEM_SCHEDULER_EXECUTABLE", "C:/Apps/KOReaderEcosystem.exe")
    config = _config(tmp_path)
    config = replace(config, daily_backup_enabled=True, daily_backup_time="06:30")
    service = ApplicationServices(config, restic=FakeRestic())

    service.configure_backup_automation(
        daily_backup_enabled=False,
        daily_backup_time="06:30",
        backup_on_kindle_connect=False,
    )

    assert calls == ["remove_future_runs"]


def test_scheduled_backup_records_success_and_rejects_disabled_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from koreader_ecosystem.backup import BackupResult

    disabled = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    with pytest.raises(ServiceError, match="scheduled_backup_disabled"):
        disabled.run_scheduled_backup()

    config = replace(_config(tmp_path), daily_backup_enabled=True)
    service = ApplicationServices(config, restic=FakeRestic())
    monkeypatch.setattr(
        service,
        "_create_backup_locked",
        lambda **kwargs: BackupResult("d" * 64, 3, 123, 3, 0, restore_verified=False),
    )

    result = service.run_scheduled_backup()

    record = service.operation_history.get("local_backup")
    assert result.snapshot_id == "d" * 64
    assert record is not None and record.status == "success"
    assert record.last_success_at == record.updated_at


def test_scheduled_backup_records_failure_reason_for_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = replace(_config(tmp_path), daily_backup_enabled=True)
    service = ApplicationServices(config, restic=FakeRestic())
    monkeypatch.setattr(
        service,
        "_create_backup_locked",
        lambda **_: (_ for _ in ()).throw(ServiceError("calibre_open")),
    )

    with pytest.raises(ServiceError, match="calibre_open"):
        service.run_scheduled_backup()

    record = service.operation_history.get("local_backup")
    assert record is not None
    assert record.status == "failed"
    assert record.reason_code == "calibre_open"


def test_kindle_send_backup_validation_failure_is_not_reported_as_device_disconnection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from koreader_ecosystem.mtp import MtpCopyError

    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    monkeypatch.setattr(
        service,
        "create_backup",
        lambda **_: (_ for _ in ()).throw(MtpCopyError("source_changed")),
    )

    with pytest.raises(ServiceError, match="backup_unavailable"):
        service.send_calibre_book_to_kindle(
            book_id=1,
            format_name="EPUB",
            filename="test.epub",
            recovery_copy_confirmed=True,
        )


def test_calibre_backup_reuses_existing_validated_operation(tmp_path: Path, monkeypatch) -> None:
    import koreader_ecosystem.services as services
    from koreader_ecosystem.backup import BackupResult

    calls: list[tuple[object, ...]] = []

    def fake_backup(config: AppConfig, restic: FakeRestic) -> BackupResult:
        calls.append((config, restic))
        return BackupResult("b" * 64, 4, 99, 4, 0, restore_verified=False)

    monkeypatch.setattr(services, "create_library_backup", fake_backup)
    restic = FakeRestic()

    result = ApplicationServices(_config(tmp_path), restic=restic).create_backup(
        calibre_only=True, recovery_copy_confirmed=True
    )

    assert result.snapshot_id == "b" * 64
    assert calls == [(_config(tmp_path), restic)]


def test_remove_calibre_book_requires_a_verified_restoration_before_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.services as services
    from koreader_ecosystem.backup import BackupResult

    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    backup_calls: list[dict[str, object]] = []

    def make_backup(config: AppConfig, restic: FakeRestic, **kwargs: object) -> BackupResult:
        backup_calls.append(kwargs)
        return BackupResult("f" * 64, 4, 100, 4, 0, restore_verified=False)

    monkeypatch.setattr(services, "create_library_backup", make_backup)
    monkeypatch.setattr(
        services,
        "remove_calibre_library_book",
        lambda *_args: (_ for _ in ()).throw(AssertionError("deletion must not run")),
        raising=False,
    )

    with pytest.raises(ServiceError, match="calibre_remove_backup_unverified"):
        service.remove_calibre_book(7, recovery_copy_confirmed=True)

    assert backup_calls == [{"verify_restore": True}]


def test_bulk_tag_edit_takes_one_backup_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.services as services
    from koreader_ecosystem.calibre_catalog import CalibreBook

    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    backup_calls: list[bool] = []
    write_calls: list[dict[str, object]] = []
    expected = (CalibreBook(7, "book-uuid", "Livro", ("Autora",), {}, {}),)
    monkeypatch.setattr(service, "_backup_calibre_before_write", lambda: backup_calls.append(True))

    def edit_batch(*_args: object, **kwargs: object) -> tuple[CalibreBook, ...]:
        write_calls.append(kwargs)
        return expected

    monkeypatch.setattr(services, "edit_library_tags_batch", edit_batch)

    result = service.edit_calibre_tags_batch(
        book_ids=(7,),
        add_tags=("Fantasia",),
        remove_tags=(),
        recovery_copy_confirmed=True,
    )

    assert result == expected
    assert backup_calls == [True]
    assert write_calls == [{"add_tags": ("Fantasia",), "remove_tags": ()}]


def test_plugin_update_stops_before_mtp_when_backup_restore_is_not_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.services as services
    from koreader_ecosystem.backup import BackupResult

    app_services = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    backup_calls: list[dict[str, object]] = []

    def create_backup(**kwargs: object) -> BackupResult:
        backup_calls.append(kwargs)
        return BackupResult("d" * 64, 7, 4096, 5, 2, restore_verified=False)

    monkeypatch.setattr(app_services, "create_backup", create_backup)
    monkeypatch.setattr(
        services,
        "prepare_hardcover_plugin",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("plugin preparation must not happen before verified backup")
        ),
        raising=False,
    )

    with pytest.raises(ServiceError, match="plugin_update_backup_unverified"):
        app_services.update_hardcover_plugin(recovery_copy_confirmed=True)

    assert backup_calls == [{"verify_restore": True, "recovery_copy_confirmed": True}]


def test_plugin_update_returns_snapshot_and_rollback_ids_after_verified_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.services as services
    from koreader_ecosystem.backup import BackupResult
    from koreader_ecosystem.koreader import PluginUpdateResult

    app_services = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    calls: list[str] = []
    source = tmp_path / "patched-plugin"
    source.mkdir()
    monkeypatch.setattr(
        app_services,
        "create_backup",
        lambda **kwargs: (
            calls.append("backup") or BackupResult("e" * 64, 7, 4096, 5, 2, restore_verified=True)
        ),
    )
    monkeypatch.setattr(
        services,
        "prepare_hardcover_plugin",
        lambda *_args, **_kwargs: calls.append("prepare") or source,
        raising=False,
    )
    monkeypatch.setattr(
        services,
        "update_plugin_mtp",
        lambda _source, _rollback: (
            calls.append("mtp-update") or PluginUpdateResult(1, 128, True, "hardcover-abc")
        ),
        raising=False,
    )

    result = app_services.update_hardcover_plugin(recovery_copy_confirmed=True)

    assert calls == ["backup", "prepare", "mtp-update"]
    assert result.restic_snapshot_id == "e" * 64
    assert result.rollback_backup_id == "hardcover-abc"
    assert result.updated is True


def test_plugin_update_requires_explicit_confirmation_before_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_services = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    monkeypatch.setattr(
        app_services,
        "create_backup",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("no backup is allowed before explicit confirmation")
        ),
    )

    with pytest.raises(ServiceError, match="recovery_copy_unconfirmed"):
        app_services.update_hardcover_plugin(recovery_copy_confirmed=False)


def test_restore_validation_passes_live_sources_to_existing_guard(
    tmp_path: Path, monkeypatch
) -> None:
    import koreader_ecosystem.services as services
    from koreader_ecosystem.backup import RestoreResult

    config = _config(tmp_path)
    config.calibre_library.mkdir()
    config.restic_repository.mkdir()
    captured: dict[str, object] = {}

    def fake_validate(snapshot: str, target: Path, restic: FakeRestic, sources=()) -> RestoreResult:
        target.mkdir(parents=True)
        captured.update(snapshot=snapshot, target=target, restic=restic, sources=sources)
        return RestoreResult(snapshot, 3, 123, ())

    monkeypatch.setattr(services, "validate_snapshot_restore", fake_validate)
    restic = FakeRestic()
    target = tmp_path / "restore"

    result = ApplicationServices(config, restic=restic).validate_restore("c" * 64, target)

    assert result.file_count == 3
    assert (target / ".kre-verified-restore.json").is_file()
    assert captured == {
        "snapshot": "c" * 64,
        "target": target,
        "restic": restic,
        "sources": (config.calibre_library, config.restic_repository),
    }


def test_koreader_apply_requires_confirmation_before_mtp_or_calibre_write(
    tmp_path: Path, monkeypatch
) -> None:
    import koreader_ecosystem.services as services

    monkeypatch.setattr(
        services,
        "stage_kindle",
        lambda *_: (_ for _ in ()).throw(AssertionError("device access must not run")),
    )

    with pytest.raises(ServiceError, match="apply_unconfirmed"):
        ApplicationServices(_config(tmp_path), restic=FakeRestic()).apply_koreader_preview(
            "preview-id", confirmed=False
        )


def test_koreader_calibre_preview_stages_only_reading_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    import koreader_ecosystem.services as services
    from koreader_ecosystem.mtp import MtpReadingStageSummary

    staged_paths: list[Path] = []

    def stage_reading_data(destination: Path) -> MtpReadingStageSummary:
        destination.mkdir(parents=True)
        staged_paths.append(destination)
        return MtpReadingStageSummary(destination, 1, 16, (), ())

    monkeypatch.setattr(services, "stage_koreader_reading_data", stage_reading_data)
    monkeypatch.setattr(
        services,
        "stage_kindle",
        lambda *_: (_ for _ in ()).throw(AssertionError("full device staging is unnecessary")),
    )
    monkeypatch.setattr(services, "require_calibre_closed", lambda *_: None)
    monkeypatch.setattr(services, "_check_calibre_integrity", lambda *_: None)
    monkeypatch.setattr(services, "load_calibre_catalog", lambda *_: ())
    monkeypatch.setattr(
        services, "create_preview", lambda *_: SimpleNamespace(preview_id="preview-id")
    )
    monkeypatch.setattr(services, "render_preview", lambda *_: None)

    preview, _ = ApplicationServices(
        _config(tmp_path), restic=FakeRestic()
    ).preview_koreader_calibre()

    assert preview.preview_id == "preview-id"
    assert len(staged_paths) == 1
    assert not staged_paths[0].exists()


def test_koreader_calibre_apply_stages_only_reading_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.services as services
    from koreader_ecosystem.mtp import MtpReadingStageSummary

    staged_paths: list[Path] = []

    def stage_reading_data(destination: Path) -> MtpReadingStageSummary:
        destination.mkdir(parents=True)
        staged_paths.append(destination)
        return MtpReadingStageSummary(destination, 1, 16, (), ())

    def apply(*_args: object) -> object:
        assert staged_paths[0].is_dir()
        return object()

    monkeypatch.setattr(services, "stage_koreader_reading_data", stage_reading_data)
    monkeypatch.setattr(
        services,
        "stage_kindle",
        lambda *_: (_ for _ in ()).throw(AssertionError("full device staging is unnecessary")),
    )
    monkeypatch.setattr(services, "require_calibre_closed", lambda *_: None)
    monkeypatch.setattr(services, "_check_calibre_integrity", lambda *_: None)
    monkeypatch.setattr(services, "apply_preview", apply)

    ApplicationServices(_config(tmp_path), restic=FakeRestic()).apply_koreader_preview(
        "preview-id", confirmed=True
    )

    assert len(staged_paths) == 1
    assert not staged_paths[0].exists()


def test_remote_sync_rejects_unconfigured_destination(tmp_path: Path) -> None:
    with pytest.raises(ServiceError, match="remote_repository_unconfigured"):
        ApplicationServices(_config(tmp_path), restic=FakeRestic()).sync_remote()


def test_previously_stored_hardcover_token_is_not_used_until_reentered(
    tmp_path: Path, monkeypatch
) -> None:
    import koreader_ecosystem.services as services

    monkeypatch.setattr(services, "hardcover_token_is_stored", lambda: True)
    monkeypatch.setattr(
        services,
        "load_hardcover_token",
        lambda: (_ for _ in ()).throw(AssertionError("token must not be read before setup")),
    )
    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())

    assert not service._hardcover_token_available()
    with pytest.raises(ServiceError, match="hardcover_token_unavailable"):
        service.list_hardcover_books()


def test_hardcover_setup_verifies_new_token_before_marking_it_ready(
    tmp_path: Path, monkeypatch
) -> None:
    import koreader_ecosystem.services as services

    class FakeHardcover:
        def verify_token(self, token: str) -> None:
            assert token == "replacement-token"

    monkeypatch.setattr(services, "save_hardcover_token", lambda _: None)
    monkeypatch.setattr(services, "load_hardcover_token", lambda: "replacement-token")
    monkeypatch.setattr(services, "hardcover_token_is_stored", lambda: True)
    service = ApplicationServices(
        _config(tmp_path),
        restic=FakeRestic(),
        hardcover=FakeHardcover(),  # type: ignore[arg-type]
    )

    service.configure_hardcover("replacement-token")

    assert service._hardcover_token_available()
    assert service._hardcover_ready_marker.read_text(encoding="ascii") == "verified\n"


def test_hardcover_library_cache_is_written_and_used_only_for_transient_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from koreader_ecosystem.application_state import ApplicationState
    from koreader_ecosystem.hardcover import HardcoverError
    from koreader_ecosystem.hardcover_models import HardcoverLibraryEntry
    from koreader_ecosystem.sync_persistence import RemoteCacheStore

    class FakeHardcover:
        def __init__(self) -> None:
            self.responses: list[object] = []

        def list_user_books(
            self, *_args: object, **_kwargs: object
        ) -> tuple[HardcoverLibraryEntry, ...]:
            result = self.responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result  # type: ignore[return-value]

    entry = HardcoverLibraryEntry(1, 2, "Book", ("Author",), 2, None, 3, 300, ())
    fake = FakeHardcover()
    fake.responses = [(entry,), HardcoverError("remote_unavailable")]
    service = ApplicationServices(_config(tmp_path), restic=FakeRestic(), hardcover=fake)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_hardcover_token_available", lambda: True)
    monkeypatch.setattr("koreader_ecosystem.services.load_hardcover_token", lambda: "local-token")
    observed = datetime(2026, 10, 25, 16, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "koreader_ecosystem.services.datetime",
        type("Clock", (), {"now": staticmethod(lambda *_args, **_kwargs: observed)}),
    )

    live = service.list_hardcover_books(limit=10, offset=0)
    cached = service.list_hardcover_books(limit=10, offset=0)

    stored = RemoteCacheStore(ApplicationState(service.application_state_database)).get(
        "library_page", "10:0"
    )
    assert live.source == "live" and live.books == (entry,)
    assert cached.source == "cache" and cached.books == (entry,)
    assert cached.fetched_at == observed
    assert stored is not None and stored.fetched_at == observed


def test_hardcover_authentication_failure_does_not_use_cached_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from koreader_ecosystem.hardcover import HardcoverError
    from koreader_ecosystem.hardcover_models import HardcoverLibraryEntry
    from koreader_ecosystem.sync_persistence import RemoteCacheStore

    class FakeHardcover:
        def list_user_books(
            self, *_args: object, **_kwargs: object
        ) -> tuple[HardcoverLibraryEntry, ...]:
            raise HardcoverError("authentication_failed")

    service = ApplicationServices(
        _config(tmp_path),
        restic=FakeRestic(),
        hardcover=FakeHardcover(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(service, "_hardcover_token_available", lambda: True)
    monkeypatch.setattr("koreader_ecosystem.services.load_hardcover_token", lambda: "local-token")
    RemoteCacheStore(ApplicationState(service.application_state_database)).put(
        "library_page", "10:0", {"books": []}
    )

    with pytest.raises(HardcoverError, match="authentication_failed"):
        service.list_hardcover_books(limit=10, offset=0)


def test_hardcover_current_reading_cache_survives_transient_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from koreader_ecosystem.hardcover import HardcoverError
    from koreader_ecosystem.hardcover_models import HardcoverLibraryEntry

    class FakeHardcover:
        responses: list[object]

        def current_reading(self, *_args: object) -> HardcoverLibraryEntry | None:
            result = self.responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result  # type: ignore[return-value]

    entry = HardcoverLibraryEntry(1, 2, "Book", ("Author",), 2, None, 3, 300, ())
    fake = FakeHardcover()
    fake.responses = [entry, HardcoverError("remote_unavailable")]
    service = ApplicationServices(
        _config(tmp_path),
        restic=FakeRestic(),
        hardcover=fake,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(service, "_hardcover_token_available", lambda: True)
    monkeypatch.setattr("koreader_ecosystem.services.load_hardcover_token", lambda: "local-token")

    live = service.current_hardcover_reading()
    cached = service.current_hardcover_reading()

    assert live.book == entry and live.source == "live"
    assert cached.book == entry and cached.source == "cache"
    assert cached.fetched_at == live.fetched_at


def test_hardcover_cache_is_cleared_only_after_new_token_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from koreader_ecosystem.hardcover import HardcoverError
    from koreader_ecosystem.sync_persistence import RemoteCacheStore

    class FakeHardcover:
        def verify_token(self, token: str) -> None:
            if token == "invalid":
                raise HardcoverError("authentication_failed")

    service = ApplicationServices(
        _config(tmp_path),
        restic=FakeRestic(),
        hardcover=FakeHardcover(),  # type: ignore[arg-type]
    )
    cache = RemoteCacheStore(ApplicationState(service.application_state_database))
    cache.put("library_page", "10:0", {"books": []})
    saved_tokens: list[str] = []
    monkeypatch.setattr("koreader_ecosystem.services.save_hardcover_token", saved_tokens.append)

    with pytest.raises(HardcoverError, match="authentication_failed"):
        service.configure_hardcover("invalid")
    assert cache.get("library_page", "10:0") is not None
    assert saved_tokens == []

    service.configure_hardcover("valid")
    assert cache.get("library_page", "10:0") is None
    assert saved_tokens == ["valid"]


def test_hardcover_mutation_is_queued_before_send_and_marked_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date

    from koreader_ecosystem.hardcover_models import HardcoverRead

    class FakeHardcover:
        def start_read(self, *_args: object, **kwargs: object) -> HardcoverRead:
            assert observed_outbox_status(service) == "sending"
            return HardcoverRead(
                9, kwargs["started_at"], None, kwargs["page"], kwargs["edition_id"]
            )

    service = ApplicationServices(
        _config(tmp_path),
        restic=FakeRestic(),
        hardcover=FakeHardcover(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(service, "_hardcover_token_available", lambda: True)
    monkeypatch.setattr("koreader_ecosystem.services.load_hardcover_token", lambda: "local-token")

    result = service.start_read(
        user_book_id=12, edition_id=34, page=7, started_at=date(2026, 10, 25)
    )

    assert result.id == 9
    assert observed_outbox_status(service) == "complete"


def test_hardcover_ambiguous_mutation_failure_is_reconciled_not_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date

    from koreader_ecosystem.hardcover import HardcoverError
    from koreader_ecosystem.sync_persistence import SyncOutboxStore

    class FakeHardcover:
        calls = 0

        def start_read(self, *_args: object, **_kwargs: object) -> object:
            self.calls += 1
            raise HardcoverError("remote_unavailable")

    fake = FakeHardcover()
    service = ApplicationServices(_config(tmp_path), restic=FakeRestic(), hardcover=fake)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_hardcover_token_available", lambda: True)
    monkeypatch.setattr("koreader_ecosystem.services.load_hardcover_token", lambda: "local-token")

    with pytest.raises(HardcoverError, match="remote_unavailable"):
        service.start_read(user_book_id=12, edition_id=34, started_at=date(2026, 10, 25))

    pending = SyncOutboxStore(ApplicationState(service.application_state_database)).list_pending()
    assert len(pending) == 1
    assert pending[0].status == "reconcile"
    assert pending[0].reason_code == "remote_unavailable"
    restarted = ApplicationServices(_config(tmp_path), restic=FakeRestic(), hardcover=fake)  # type: ignore[arg-type]
    assert fake.calls == 1
    assert (
        SyncOutboxStore(ApplicationState(restarted.application_state_database))
        .list_pending()[0]
        .status
        == "reconcile"
    )


def test_hardcover_manual_reconciliation_retries_safe_operations_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from koreader_ecosystem.sync_persistence import SyncOutboxStore

    class FakeHardcover:
        rating_calls = 0
        journal_calls: list[tuple[int, str, int]] = []

        def update_rating(self, token: str, *, user_book_id: int, rating: float | None) -> object:
            assert token == "local-token"
            assert user_book_id == 17
            assert rating == 4.5
            assert observed_outbox_status(service, "rating-ambiguous") == "sending"
            self.rating_calls += 1
            return object()

        def add_journal_entry(
            self,
            token: str,
            *,
            book_id: int,
            entry: str,
            privacy_setting_id: int,
        ) -> int:
            assert token == "local-token"
            self.journal_calls.append((book_id, entry, privacy_setting_id))
            return 92

    fake = FakeHardcover()
    service = ApplicationServices(
        _config(tmp_path),
        restic=FakeRestic(),
        hardcover=fake,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(service, "_hardcover_token_available", lambda: True)
    monkeypatch.setattr("koreader_ecosystem.services.load_hardcover_token", lambda: "local-token")
    store = SyncOutboxStore(ApplicationState(service.application_state_database))
    ambiguous_rating = store.enqueue(
        provider="hardcover",
        operation="update_rating",
        idempotency_key="rating-ambiguous",
        payload={"user_book_id": 17, "rating": 4.5},
    )
    store.transition(ambiguous_rating.operation_id, "sending")
    store.transition(ambiguous_rating.operation_id, "reconcile", reason_code="remote_unavailable")
    ambiguous_note = store.enqueue(
        provider="hardcover",
        operation="add_journal_entry",
        idempotency_key="note-ambiguous",
        payload={"book_id": 28, "entry": "Keep this once", "privacy_setting_id": 3},
    )
    store.transition(ambiguous_note.operation_id, "sending")
    store.transition(ambiguous_note.operation_id, "reconcile", reason_code="remote_unavailable")
    store.enqueue(
        provider="hardcover",
        operation="add_journal_entry",
        idempotency_key="note-unsent",
        payload={"book_id": 29, "entry": "Never sent yet", "privacy_setting_id": 3},
    )

    assert service.hardcover_sync_summary() == {
        "pending_mutations": 3,
        "reconciliation_required": 2,
        "safe_to_retry": 2,
        "manual_review_required": 1,
    }

    with pytest.raises(ServiceError, match="hardcover_confirmation_required"):
        service.reconcile_hardcover_outbox(confirmed=False)

    result = service.reconcile_hardcover_outbox(confirmed=True)

    assert result == {
        "attempted": 2,
        "completed": 2,
        "failed": 0,
        "reconciliation_required": 1,
        "safe_to_retry": 0,
        "manual_review_required": 1,
    }
    assert fake.rating_calls == 1
    assert fake.journal_calls == [(29, "Never sent yet", 3)]
    pending = store.list_pending()
    assert {item.operation_id: item.status for item in pending} == {
        ambiguous_note.operation_id: "reconcile"
    }


@pytest.mark.parametrize(
    ("operation", "payload", "expected_call"),
    [
        (
            "finish_read",
            {"read_id": 31, "finished_at": "2026-10-26"},
            ("finish_read", "local-token", 31, "2026-10-26"),
        ),
    ],
)
def test_hardcover_manual_reconciliation_restores_read_dates_and_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    payload: dict[str, object],
    expected_call: tuple[object, ...],
) -> None:
    from koreader_ecosystem.sync_persistence import SyncOutboxStore

    class FakeHardcover:
        calls: list[tuple[object, ...]] = []

        def start_read(
            self,
            token: str,
            *,
            user_book_id: int,
            edition_id: int,
            page: int,
            started_at: object,
        ) -> object:
            self.calls.append(
                ("start_read", token, user_book_id, edition_id, page, started_at.isoformat())
            )
            return object()

        def finish_read(self, token: str, *, read_id: int, finished_at: object) -> object:
            self.calls.append(("finish_read", token, read_id, finished_at.isoformat()))
            return object()

    fake = FakeHardcover()
    service = ApplicationServices(
        _config(tmp_path),
        restic=FakeRestic(),
        hardcover=fake,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(service, "_hardcover_token_available", lambda: True)
    monkeypatch.setattr("koreader_ecosystem.services.load_hardcover_token", lambda: "local-token")
    store = SyncOutboxStore(ApplicationState(service.application_state_database))
    queued = store.enqueue(
        provider="hardcover",
        operation=operation,
        idempotency_key=f"{operation}-ambiguous",
        payload=payload,
    )
    store.transition(queued.operation_id, "sending")
    store.transition(queued.operation_id, "reconcile", reason_code="remote_unavailable")

    result = service.reconcile_hardcover_outbox(confirmed=True)

    assert fake.calls == [expected_call]
    assert result["completed"] == 1
    assert store.list_pending() == ()


def test_hardcover_ambiguous_start_read_is_not_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from koreader_ecosystem.sync_persistence import SyncOutboxStore

    class FakeHardcover:
        calls = 0

        def start_read(self, *_args: object, **_kwargs: object) -> object:
            self.calls += 1
            return object()

    fake = FakeHardcover()
    service = ApplicationServices(
        _config(tmp_path),
        restic=FakeRestic(),
        hardcover=fake,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(service, "_hardcover_token_available", lambda: True)
    monkeypatch.setattr("koreader_ecosystem.services.load_hardcover_token", lambda: "local-token")
    store = SyncOutboxStore(ApplicationState(service.application_state_database))
    queued = store.enqueue(
        provider="hardcover",
        operation="start_read",
        idempotency_key="ambiguous-start-read",
        payload={
            "user_book_id": 17,
            "edition_id": 27,
            "page": 8,
            "started_at": "2026-10-25",
        },
    )
    store.transition(queued.operation_id, "sending")
    store.transition(queued.operation_id, "reconcile", reason_code="remote_unavailable")

    assert service.hardcover_sync_summary()["safe_to_retry"] == 0
    result = service.reconcile_hardcover_outbox(confirmed=True)

    assert fake.calls == 0
    assert result["attempted"] == 0
    assert result["manual_review_required"] == 1
    assert store.list_pending()[0].status == "reconcile"


def test_hardcover_manual_review_is_local_and_never_exposes_journal_text(
    tmp_path: Path,
) -> None:
    from koreader_ecosystem.hardcover_models import HardcoverLibraryEntry, serialize_library_entry
    from koreader_ecosystem.sync_persistence import RemoteCacheStore

    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    cached_entry = HardcoverLibraryEntry(
        user_book_id=17,
        book_id=28,
        title="Example Novel",
        authors=("Example Author",),
        status_id=2,
        rating=None,
        edition_id=39,
        edition_pages=300,
        reads=(),
    )
    RemoteCacheStore(ApplicationState(service.application_state_database)).put(
        "library_page",
        "100:0",
        {"books": [serialize_library_entry(cached_entry)]},
    )
    queued = service.sync_outbox.enqueue(
        provider="hardcover",
        operation="add_journal_entry",
        idempotency_key="ambiguous-private-note",
        payload={"book_id": 28, "entry": "A private journal sentence", "privacy_setting_id": 3},
    )
    service.sync_outbox.transition(queued.operation_id, "sending")
    service.sync_outbox.transition(
        queued.operation_id, "reconcile", reason_code="remote_unavailable"
    )

    reviews = service.list_hardcover_manual_reviews()

    assert reviews == [
        {
            "operation_id": queued.operation_id,
            "operation": "add_journal_entry",
            "status": "reconcile",
            "reason_code": "remote_unavailable",
            "attempts": 1,
            "created_at": reviews[0]["created_at"],
            "updated_at": reviews[0]["updated_at"],
            "book_title": "Example Novel",
            "authors": ["Example Author"],
            "book_reference": "book_id:28",
            "rating": None,
            "page": None,
        }
    ]
    assert "A private journal sentence" not in repr(reviews)
    assert "entry" not in reviews[0]


def test_hardcover_manual_review_closes_locally_only_after_confirmation(tmp_path: Path) -> None:
    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    queued = service.sync_outbox.enqueue(
        provider="hardcover",
        operation="start_read",
        idempotency_key="ambiguous-start-review",
        payload={"user_book_id": 17, "edition_id": 39, "page": 12},
    )
    service.sync_outbox.transition(queued.operation_id, "sending")
    service.sync_outbox.transition(
        queued.operation_id, "reconcile", reason_code="remote_unavailable"
    )

    with pytest.raises(ServiceError, match="hardcover_confirmation_required"):
        service.resolve_hardcover_manual_review(
            queued.operation_id, resolution="closed_locally", confirmed=False
        )

    result = service.resolve_hardcover_manual_review(
        queued.operation_id, resolution="closed_locally", confirmed=True
    )

    assert result["resolution"] == "closed_locally"
    assert result["operation_id"] == queued.operation_id
    assert service.hardcover_sync_summary()["pending_mutations"] == 0
    with pytest.raises(ServiceError, match="hardcover_review_not_allowed"):
        service.resolve_hardcover_manual_review(
            queued.operation_id, resolution="closed_locally", confirmed=True
        )


def test_hardcover_manual_review_rejects_safe_actions(tmp_path: Path) -> None:
    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    queued = service.sync_outbox.enqueue(
        provider="hardcover",
        operation="update_rating",
        idempotency_key="safe-rating-action",
        payload={"user_book_id": 17, "rating": 4.5},
    )

    with pytest.raises(ServiceError, match="hardcover_review_not_allowed"):
        service.resolve_hardcover_manual_review(
            queued.operation_id, resolution="confirmed_remote", confirmed=True
        )


def test_hardcover_reconciliation_processes_at_most_four_actions_per_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from koreader_ecosystem.sync_persistence import SyncOutboxStore

    class FakeHardcover:
        ratings: list[tuple[int, float | None]] = []

        def update_rating(self, _token: str, *, user_book_id: int, rating: float | None) -> object:
            self.ratings.append((user_book_id, rating))
            return object()

    fake = FakeHardcover()
    service = ApplicationServices(
        _config(tmp_path),
        restic=FakeRestic(),
        hardcover=fake,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(service, "_hardcover_token_available", lambda: True)
    monkeypatch.setattr("koreader_ecosystem.services.load_hardcover_token", lambda: "local-token")
    store = SyncOutboxStore(ApplicationState(service.application_state_database))
    for index in range(5):
        store.enqueue(
            provider="hardcover",
            operation="update_rating",
            idempotency_key=f"rating-pending-{index}",
            payload={"user_book_id": index + 1, "rating": 4.5},
        )

    result = service.reconcile_hardcover_outbox(confirmed=True)

    assert len(fake.ratings) == 4
    assert result["attempted"] == 4
    assert result["completed"] == 4
    assert result["safe_to_retry"] == 1
    assert len(store.list_pending()) == 1


def test_hardcover_reconciliation_keeps_network_failures_retryable_but_manual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from koreader_ecosystem.hardcover import HardcoverError
    from koreader_ecosystem.sync_persistence import SyncOutboxStore

    class FakeHardcover:
        calls = 0

        def update_rating(self, *_args: object, **_kwargs: object) -> object:
            self.calls += 1
            raise HardcoverError("remote_unavailable")

    fake = FakeHardcover()
    service = ApplicationServices(
        _config(tmp_path),
        restic=FakeRestic(),
        hardcover=fake,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(service, "_hardcover_token_available", lambda: True)
    monkeypatch.setattr("koreader_ecosystem.services.load_hardcover_token", lambda: "local-token")
    store = SyncOutboxStore(ApplicationState(service.application_state_database))
    queued = store.enqueue(
        provider="hardcover",
        operation="update_rating",
        idempotency_key="rating-network-error",
        payload={"user_book_id": 17, "rating": 4.5},
    )
    store.transition(queued.operation_id, "sending")
    store.transition(queued.operation_id, "reconcile", reason_code="remote_unavailable")

    result = service.reconcile_hardcover_outbox(confirmed=True)
    reopened = ApplicationServices(_config(tmp_path), restic=FakeRestic(), hardcover=fake)  # type: ignore[arg-type]

    assert fake.calls == 1
    assert result["reconciliation_required"] == 1
    assert result["safe_to_retry"] == 1
    assert reopened.hardcover_sync_summary()["safe_to_retry"] == 1


def observed_outbox_status(
    service: ApplicationServices, idempotency_key: str | None = None
) -> str | None:
    with ApplicationState(service.application_state_database).connect() as connection:
        if idempotency_key is None:
            row = connection.execute(
                "SELECT status FROM sync_outbox ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT status FROM sync_outbox WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
    return str(row[0]) if row is not None else None


def test_koreader_reading_preview_stages_and_records_an_inactive_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    from datetime import UTC, datetime
    from types import SimpleNamespace

    import koreader_ecosystem.services as services
    from koreader_ecosystem.application_state import ApplicationState
    from koreader_ecosystem.koreader_reading_history import (
        PageEvent,
        ReadingBook,
        ReadingHistoryPreview,
    )
    from koreader_ecosystem.reading_history_state import KoreaderReadingHistoryStore

    observed = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    started = datetime(2026, 10, 25, 11, 57, tzinfo=UTC)
    event_id = hashlib.sha256(f"42\x001\x00{int(started.timestamp())}".encode()).hexdigest()
    preview = ReadingHistoryPreview(
        source_digest="a" * 64,
        books=(ReadingBook(42, "Fixture", "Author", 0, started, 0, 100, None, "eng", None, 60, 1),),
        events=(PageEvent(event_id, 42, 1, started, 60, 100, "a" * 64),),
        sessions=(),
        warnings=(),
        sidecars=(),
        observed_at=observed,
    )
    staged: list[Path] = []

    def stage(destination: Path) -> object:
        destination.mkdir(parents=True)
        staged.append(destination)
        return SimpleNamespace(destination=destination, cleanup_paths=())

    monkeypatch.setattr(services, "stage_koreader_reading_data", stage)
    monkeypatch.setattr(services, "preview_reading_history", lambda *_args, **_kwargs: preview)
    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())

    result = service.preview_koreader_reading_history()

    assert result.preview == preview
    assert result.batch.status == "preview"
    assert (
        KoreaderReadingHistoryStore(
            ApplicationState(service.application_state_database)
        ).get_events()
        == ()
    )
    assert len(staged) == 1
    assert not staged[0].exists()


def test_koreader_reading_import_requires_explicit_confirmation(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from koreader_ecosystem.application_state import ApplicationState
    from koreader_ecosystem.koreader_reading_history import ReadingHistoryPreview
    from koreader_ecosystem.reading_history_state import KoreaderReadingHistoryStore

    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    store = KoreaderReadingHistoryStore(ApplicationState(service.application_state_database))
    batch = store.stage_preview(
        ReadingHistoryPreview("a" * 64, (), (), (), (), (), datetime.now(UTC))
    )

    with pytest.raises(ServiceError, match="import_unconfirmed"):
        service.confirm_koreader_reading_history(
            batch.import_id,
            batch.source_digest,
            confirmed=False,
        )


def test_koreader_manual_mapping_requires_confirmation_and_confirms_edition_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    import koreader_ecosystem.services as services
    from koreader_ecosystem.application_state import ApplicationState
    from koreader_ecosystem.calibre_catalog import CalibreBook
    from koreader_ecosystem.koreader_reading_history import (
        ReadingBook,
        ReadingHistoryPreview,
    )
    from koreader_ecosystem.reading_history_state import KoreaderReadingHistoryStore

    config = _config(tmp_path)
    service = ApplicationServices(config, restic=FakeRestic())
    preview = ReadingHistoryPreview(
        "a" * 64,
        (
            ReadingBook(
                42,
                "Same Title",
                "Author",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            ),
        ),
        (),
        (),
        (),
        (),
        datetime.now(UTC),
    )
    batch = KoreaderReadingHistoryStore(
        ApplicationState(service.application_state_database)
    ).stage_preview(preview)
    book = CalibreBook(7, "0f8fad5b-d9cb-469f-a165-70867728950e", "Same Title", ("Author",), {}, {})
    monkeypatch.setattr(services, "_running_process_names", lambda: [])
    monkeypatch.setattr(services, "_check_calibre_integrity", lambda *_: None)
    monkeypatch.setattr(services, "load_calibre_catalog", lambda *_: (book,))

    with pytest.raises(ServiceError, match="mapping_unconfirmed"):
        service.map_koreader_reading_book(batch.import_id, 42, 7, confirmed=False)
    service.map_koreader_reading_book(batch.import_id, 42, 7, confirmed=True)

    with sqlite3.connect(service.application_state_database) as connection:
        link = connection.execute(
            """
            SELECT relation, confidence FROM identity_links AS link
            JOIN book_identities AS source ON source.identity_id=link.left_identity_id
            JOIN book_identities AS target ON target.identity_id=link.right_identity_id
            WHERE source.provider='koreader' AND source.external_id='42'
              AND target.provider='calibre' AND target.external_id=?
            """,
            (book.uuid,),
        ).fetchone()
    assert link == ("same_edition", "confirmed")


def test_calibre_reading_projection_requires_explicit_confirmation(
    tmp_path: Path,
) -> None:
    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())

    with pytest.raises(ServiceError, match="projection_unconfirmed"):
        service.apply_calibre_reading_projection(
            "a" * 64,
            "b" * 64,
            confirmed=False,
        )


def test_calibre_reading_projection_rejects_preview_from_another_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    import koreader_ecosystem.services as services
    from koreader_ecosystem.application_state import ApplicationState
    from koreader_ecosystem.koreader_reading_history import ReadingHistoryPreview
    from koreader_ecosystem.reading_history_state import KoreaderReadingHistoryStore
    from koreader_ecosystem.sync_state import SyncState

    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    store = KoreaderReadingHistoryStore(ApplicationState(service.application_state_database))
    batch = store.stage_preview(
        ReadingHistoryPreview("a" * 64, (), (), (), (), (), datetime.now(UTC))
    )
    store.confirm_import(batch.import_id, batch.source_digest)
    SyncState(service.application_state_database).save_preview(
        "b" * 64,
        "b" * 64,
        datetime.now(UTC).isoformat(),
        [
            {
                "sidecar_key": "c" * 64,
                "relative_path": "KOReader reading history",
                "source_digest": "f" * 64,
                "match_status": "manual",
                "reason_code": "confirmed_identity",
                "book_id": 7,
                "calibre_uuid": "0f8fad5b-d9cb-469f-a165-70867728950e",
                "title": "Fixture",
                "old_values": {"koreader_progress": None},
                "new_values": {"koreader_progress": 0.25},
                "warnings": [],
                "applied_values": {},
                "applied_at": None,
            }
        ],
    )
    monkeypatch.setattr(
        services,
        "apply_persisted_projection",
        lambda *_args, **_kwargs: pytest.fail("stale projection must not write"),
    )

    with pytest.raises(ServiceError, match="projection_import_mismatch"):
        service.apply_calibre_reading_projection(
            batch.import_id,
            "b" * 64,
            confirmed=True,
        )


def test_platform_csv_mapping_inspects_headers_then_previews_mapped_rows_locally(
    tmp_path: Path,
) -> None:
    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    source = tmp_path / "reading.csv"
    source.write_text(
        "Título,Autores,Estado,Nota\r\nO Hobbit,J. R. R. Tolkien,read,4.5\r\n",
        encoding="utf-8",
    )

    draft = service.inspect_platform_csv(source, source_name="goodreads")

    assert draft["headers"] == ["Título", "Autores", "Estado", "Nota"]
    assert draft["suggested_mapping"]["title"] == "Título"
    assert "O Hobbit" not in repr(draft)
    preview = service.preview_platform_csv(
        source,
        source_name="goodreads",
        column_mapping=draft["suggested_mapping"],
        expected_digest=draft["source_digest"],
    )
    page = service.get_platform_csv_preview_page(preview.batch.import_id, limit=8)

    assert preview.record_count == 1
    assert page.entries[0].record is not None
    assert page.entries[0].record["title"] == "O Hobbit"
    assert page.entries[0].record["reading_status"] == "read"


def test_platform_csv_mapping_rejects_changed_source_without_staging_a_batch(
    tmp_path: Path,
) -> None:
    service = ApplicationServices(_config(tmp_path), restic=FakeRestic())
    source = tmp_path / "reading.csv"
    source.write_text("Title,Author\r\nFirst Book,Writer\r\n", encoding="utf-8")
    draft = service.inspect_platform_csv(source, source_name="goodreads")
    source.write_text("Title,Author\r\nChanged Book,Writer\r\n", encoding="utf-8")

    with pytest.raises(ServiceError, match="platform_csv_source_changed"):
        service.preview_platform_csv(
            source,
            source_name="goodreads",
            column_mapping=draft["suggested_mapping"],
            expected_digest=draft["source_digest"],
        )

    assert service.list_platform_csv_imports(limit=10).batches == ()


def test_restored_daily_backup_reconciles_task_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.services as services

    calls: list[str] = []

    class Scheduler:
        def __init__(self, *, executable: Path) -> None:
            pass

        def register_daily_backup(self, start_time: str) -> None:
            calls.append(start_time)

    monkeypatch.setattr(services, "TaskScheduler", Scheduler)
    monkeypatch.setenv("KOREADER_ECOSYSTEM_SCHEDULER_EXECUTABLE", "C:/Apps/KOReaderEcosystem.exe")
    monkeypatch.setattr(services, "restic_password_is_stored", lambda: True)
    service = ApplicationServices(
        replace(_config(tmp_path), daily_backup_enabled=True, daily_backup_time="06:30"),
        restic=FakeRestic(),
    )
    assert service.backup_automation_status()["daily_backup_needs_reactivation"] is False
    assert service.backup_automation_status()["daily_backup_needs_reactivation"] is False
    assert calls == ["06:30"]


def test_daily_backup_reactivation_failure_is_visible_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.services as services

    calls: list[str] = []

    class Scheduler:
        def __init__(self, *, executable: Path) -> None:
            pass

        def register_daily_backup(self, start_time: str) -> None:
            calls.append(start_time)
            if len(calls) == 1:
                raise services.AutomationError("scheduled_task_unavailable")

    monkeypatch.setattr(services, "TaskScheduler", Scheduler)
    monkeypatch.setenv("KOREADER_ECOSYSTEM_SCHEDULER_EXECUTABLE", "C:/Apps/KOReaderEcosystem.exe")
    monkeypatch.setattr(services, "restic_password_is_stored", lambda: True)
    service = ApplicationServices(
        replace(_config(tmp_path), daily_backup_enabled=True), restic=FakeRestic()
    )
    assert service.backup_automation_status()["daily_backup_needs_reactivation"] is True
    status = service.configure_backup_automation(
        daily_backup_enabled=True, daily_backup_time="20:00", backup_on_kindle_connect=False
    )
    assert status["daily_backup_needs_reactivation"] is False
    assert calls == ["20:00", "20:00"]


def test_automation_status_requests_do_not_reregister_daily_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.services as services
    from koreader_ecosystem.ipc_service import build_dispatcher

    calls: list[str] = []

    class Scheduler:
        def __init__(self, *, executable: Path) -> None:
            pass

        def register_daily_backup(self, start_time: str) -> None:
            calls.append(start_time)

    monkeypatch.setattr(services, "TaskScheduler", Scheduler)
    monkeypatch.setattr(services, "restic_password_is_stored", lambda: True)
    monkeypatch.setenv("KOREADER_ECOSYSTEM_SCHEDULER_EXECUTABLE", "C:/Apps/KOReaderEcosystem.exe")
    config = replace(_config(tmp_path), daily_backup_enabled=True)
    dispatch = build_dispatcher(
        config_loader=lambda: config,
        services_factory=lambda current: ApplicationServices(current, restic=FakeRestic()),
    )
    dispatch("backup.automation.status", {})
    dispatch("backup.automation.status", {})
    assert calls == ["20:00"]
