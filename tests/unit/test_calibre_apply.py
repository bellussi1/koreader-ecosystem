from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from koreader_ecosystem.calibre_catalog import CalibreBook
from koreader_ecosystem.config import AppConfig
from koreader_ecosystem.koreader_calibre import KoreaderCalibreError
from koreader_ecosystem.sync_state import SyncState

UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"


class FakeRestic:
    available = True

    def __init__(self, events: list[str], *, fail_check: bool = False) -> None:
        self.events = events
        self.fail_check = fail_check

    def ensure_password(self) -> None:
        self.events.append("restic:password")

    def run(self, args: list[str], *, cwd: Path | None = None):
        del cwd
        self.events.append(f"restic:{args[0]}")
        from koreader_ecosystem.restic import ResticResult

        if args[0] == "backup":
            return ResticResult(returncode=0, summary={"snapshot_id": "c" * 64})
        if args[0] == "check" and self.fail_check:
            return ResticResult(returncode=1)
        return ResticResult(returncode=0)


def _prepare(
    tmp_path: Path,
    *,
    contradictory_completion: bool = False,
) -> tuple[AppConfig, Path, str, str, str]:
    library = tmp_path / "calibre-library"
    library.mkdir()
    (library / "metadata.db").write_bytes(b"fixture database")
    (library / "book.txt").write_text("book format", encoding="utf-8")
    config = AppConfig(library, tmp_path / "restic", tmp_path / "state")
    stage = tmp_path / "stage"
    sidecar = stage / "Books" / "Example.sdr" / "metadata.epub.lua"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(
        'return { ["stats"] = { ["identifiers"] = "calibre:' + UUID + '" } }', encoding="utf-8"
    )
    source_digest = hashlib.sha256(sidecar.read_bytes()).hexdigest()
    sidecar_key = hashlib.sha256(b"Books/Example.sdr/metadata.epub.lua").hexdigest()
    state = SyncState(config.state_root / "koreader-calibre.sqlite3")
    item = {
        "sidecar_key": sidecar_key,
        "relative_path": "Books/Example.sdr/metadata.epub.lua",
        "source_digest": source_digest,
        "match_status": "matched",
        "reason_code": "exact_calibre_uuid",
        "book_id": 7,
        "calibre_uuid": UUID,
        "title": "Livro",
        "old_values": {
            "koreader_progress": None,
            "koreader_note": None,
            "koreader_annotations": None,
        },
        "new_values": {
            "koreader_progress": 0.127 if contradictory_completion else 0.75,
            "koreader_note": 'Linha 1\n"ação"',
            "koreader_annotations": json.dumps(
                [{"id": "a" * 64, "annotation": {"text": "ação <trecho>"}}],
                ensure_ascii=False,
            ),
        },
        "warnings": [],
        "applied_values": {},
        "applied_at": None,
    }
    if contradictory_completion:
        item["old_values"]["koreader_status"] = None
        item["new_values"]["koreader_status"] = "complete"
    plan_fields = (
        "sidecar_key",
        "relative_path",
        "source_digest",
        "match_status",
        "reason_code",
        "book_id",
        "calibre_uuid",
        "old_values",
        "new_values",
        "warnings",
    )
    plan_material = [{field: item[field] for field in plan_fields}]
    preview_id = hashlib.sha256(
        json.dumps(
            plan_material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    state.save_preview(
        preview_id,
        preview_id,
        "2026-10-23T12:00:00+00:00",
        [item],
    )
    return config, stage, sidecar_key, source_digest, preview_id


def _book(values: dict[str, object]) -> CalibreBook:
    return CalibreBook(7, UUID, "Livro", ("Autora",), {}, values)


def _install_fakes(monkeypatch, config: AppConfig, events: list[str]):
    import koreader_ecosystem.calibre_apply as apply_module

    current = {
        "koreader_progress": None,
        "koreader_note": None,
        "koreader_annotations": None,
    }
    columns: dict[str, str] = {}
    monkeypatch.setattr(apply_module, "_running_process_names", lambda: [])
    monkeypatch.setattr(apply_module, "_check_calibre_integrity", lambda _: None)
    monkeypatch.setattr(apply_module.shutil, "which", lambda _: "calibredb.exe")
    monkeypatch.setattr("koreader_ecosystem.backup._check_calibre_integrity", lambda _: None)
    monkeypatch.setattr(
        apply_module, "load_calibre_catalog", lambda *_args, **_kwargs: (_book(current),)
    )
    monkeypatch.setattr(
        apply_module, "load_calibre_custom_columns", lambda *_args, **_kwargs: dict(columns)
    )

    def runner(args, **kwargs):
        del kwargs
        command = args[args.index(str(config.calibre_library)) + 1]
        events.append(f"calibre:{command}")
        if command == "add_custom_column":
            columns[args[-3]] = args[-1]
            current[args[-3]] = None
        elif command == "set_custom":
            label, book_id, value = args[-3:]
            assert book_id == "7"
            if label in {"koreader_progress", "koreader_rating"}:
                current[label] = float(value)
            elif label == "koreader_annotations":
                current[label] = value
            else:
                current[label] = value
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(apply_module, "_running_process_names", lambda: [])
    monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])
    return current, columns, runner


def test_apply_snapshots_before_writing_and_verifies_unicode_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from koreader_ecosystem.calibre_apply import apply_preview

    config, stage, sidecar_key, source_digest, preview_id = _prepare(tmp_path)
    del sidecar_key, source_digest
    events: list[str] = []
    current, _, runner = _install_fakes(monkeypatch, config, events)
    monkeypatch.setattr(
        "koreader_ecosystem.backup.shutil.disk_usage",
        lambda _: type("Usage", (), {"free": 10**12})(),
    )

    result = apply_preview(
        config,
        config.state_root / "koreader-calibre.sqlite3",
        stage,
        preview_id,
        FakeRestic(events),
        runner=runner,
    )

    assert result.snapshot_id == "c" * 64
    assert result.applied_book_count == 1
    assert result.applied_field_count == 3
    assert current["koreader_progress"] == 0.75
    assert current["koreader_note"] == 'Linha 1\n"ação"'
    assert any(event == "calibre:set_custom" for event in events)
    assert events.index("restic:check") < events.index("calibre:add_custom_column")
    assert all("ação" not in event for event in events if event.startswith("restic:"))
    checkpoint = SyncState(config.state_root / "koreader-calibre.sqlite3").load_checkpoint(UUID)
    assert checkpoint is not None
    assert checkpoint["values"]["koreader_progress"] == 0.75

    events_before_retry = len(events)
    retry = apply_preview(
        config,
        config.state_root / "koreader-calibre.sqlite3",
        stage,
        preview_id,
        FakeRestic(events),
        runner=runner,
    )
    assert retry.snapshot_id is None
    assert retry.applied_field_count == 3
    assert len(events) == events_before_retry


def test_persisted_projection_requires_confirmation_before_calibre_or_backup_access(
    tmp_path: Path,
) -> None:
    from koreader_ecosystem.calibre_apply import CalibreApplyError, apply_persisted_projection

    config, _, _, _, preview_id = _prepare(tmp_path)

    with pytest.raises(CalibreApplyError) as error:
        apply_persisted_projection(
            config,
            config.state_root / "koreader-calibre.sqlite3",
            preview_id,
            FakeRestic([]),
            confirmed=False,
        )

    assert error.value.reason_code == "apply_unconfirmed"


def test_persisted_projection_uses_saved_history_without_reopening_device_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from koreader_ecosystem.calibre_apply import apply_persisted_projection

    config, stage, _, _, preview_id = _prepare(tmp_path)
    (stage / "Books" / "Example.sdr" / "metadata.epub.lua").write_text(
        "return {}", encoding="utf-8"
    )
    events: list[str] = []
    current, _, runner = _install_fakes(monkeypatch, config, events)
    monkeypatch.setattr(
        "koreader_ecosystem.backup.shutil.disk_usage",
        lambda _: type("Usage", (), {"free": 10**12})(),
    )

    result = apply_persisted_projection(
        config,
        config.state_root / "koreader-calibre.sqlite3",
        preview_id,
        FakeRestic(events),
        confirmed=True,
        runner=runner,
    )

    assert result.snapshot_id == "c" * 64
    assert result.applied_field_count == 3
    assert current["koreader_progress"] == 0.75
    assert any(event == "calibre:set_custom" for event in events)


def test_persisted_conflicting_completion_is_rejected_before_backup_or_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from koreader_ecosystem.calibre_apply import CalibreApplyError, apply_persisted_projection

    config, _, _, _, preview_id = _prepare(tmp_path, contradictory_completion=True)
    events: list[str] = []
    _install_fakes(monkeypatch, config, events)
    monkeypatch.setattr(
        "koreader_ecosystem.backup.shutil.disk_usage",
        lambda _: type("Usage", (), {"free": 10**12})(),
    )

    with pytest.raises(CalibreApplyError) as error:
        apply_persisted_projection(
            config,
            config.state_root / "koreader-calibre.sqlite3",
            preview_id,
            FakeRestic(events),
            confirmed=True,
            runner=lambda *_args, **_kwargs: pytest.fail("Calibre write ran"),
        )

    assert error.value.reason_code == "status_progress_conflict"
    assert not any(event.startswith("restic:") for event in events)
    assert not any(event.startswith("calibre:") for event in events)


def test_apply_resumes_after_partial_field_write_without_duplicating_annotations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from koreader_ecosystem.calibre_apply import CalibreApplyError, apply_preview

    config, stage, _, _, preview_id = _prepare(tmp_path)
    events: list[str] = []
    current, _, runner = _install_fakes(monkeypatch, config, events)
    monkeypatch.setattr(
        "koreader_ecosystem.backup.shutil.disk_usage",
        lambda _: type("Usage", (), {"free": 10**12})(),
    )
    writes: list[str] = []
    fail_second_write = True

    def fail_once_runner(args, **kwargs):
        nonlocal fail_second_write
        command = args[args.index(str(config.calibre_library)) + 1]
        if command == "set_custom":
            writes.append(args[-3])
            if fail_second_write and len(writes) == 2:
                fail_second_write = False
                raise subprocess.SubprocessError("private process detail")
        return runner(args, **kwargs)

    with pytest.raises(CalibreApplyError) as error:
        apply_preview(
            config,
            config.state_root / "koreader-calibre.sqlite3",
            stage,
            preview_id,
            FakeRestic(events),
            runner=fail_once_runner,
        )
    assert error.value.reason_code == "calibredb_command_failed"
    assert current["koreader_annotations"] is not None

    resumed = apply_preview(
        config,
        config.state_root / "koreader-calibre.sqlite3",
        stage,
        preview_id,
        FakeRestic(events),
        runner=fail_once_runner,
    )

    assert resumed.applied_book_count == 1
    assert resumed.applied_field_count == 3
    assert writes.count("koreader_annotations") == 1
    assert current["koreader_note"] == 'Linha 1\n"ação"'


def test_apply_aborts_before_backup_or_write_when_sidecar_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from koreader_ecosystem.calibre_apply import apply_preview

    config, stage, _, _, preview_id = _prepare(tmp_path)
    sidecar = stage / "Books" / "Example.sdr" / "metadata.epub.lua"
    sidecar.write_text("return {}", encoding="utf-8")
    events: list[str] = []
    _install_fakes(monkeypatch, config, events)

    with pytest.raises(KoreaderCalibreError) as error:
        apply_preview(
            config,
            config.state_root / "koreader-calibre.sqlite3",
            stage,
            preview_id,
            FakeRestic(events),
            runner=lambda *_args, **_kwargs: pytest.fail("Calibre write ran"),
        )

    assert error.value.reason_code == "sidecar_changed"
    assert not any(event.startswith("restic:") for event in events)


def test_apply_aborts_when_saved_plan_contents_do_not_match_its_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from koreader_ecosystem.calibre_apply import CalibreApplyError, apply_preview

    config, stage, _, _, preview_id = _prepare(tmp_path)
    database = config.state_root / "koreader-calibre.sqlite3"
    with sqlite3.connect(database) as connection:
        item_json = connection.execute("SELECT item_json FROM preview_items").fetchone()[0]
        item = json.loads(item_json)
        item["new_values"]["koreader_progress"] = 0.25
        connection.execute(
            "UPDATE preview_items SET item_json = ?", (json.dumps(item, ensure_ascii=False),)
        )
    events: list[str] = []
    _install_fakes(monkeypatch, config, events)

    with pytest.raises(CalibreApplyError) as error:
        apply_preview(
            config,
            database,
            stage,
            preview_id,
            FakeRestic(events),
            runner=lambda *_args, **_kwargs: pytest.fail("Calibre write ran"),
        )

    assert error.value.reason_code == "preview_changed"
    assert not any(event.startswith("restic:") for event in events)


def test_apply_aborts_before_writes_when_calibre_is_open_or_backup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.calibre_apply as apply_module
    from koreader_ecosystem.calibre_apply import apply_preview

    config, stage, _, _, preview_id = _prepare(tmp_path)
    events: list[str] = []
    _, _, runner = _install_fakes(monkeypatch, config, events)
    monkeypatch.setattr(apply_module, "_running_process_names", lambda: ["calibre.exe"])

    with pytest.raises(KoreaderCalibreError) as open_error:
        apply_preview(
            config,
            config.state_root / "koreader-calibre.sqlite3",
            stage,
            preview_id,
            FakeRestic(events),
            runner=runner,
        )
    assert open_error.value.reason_code == "calibre_open"
    assert not any(event.startswith("restic:") for event in events)
    assert not any(event.startswith("calibre:") for event in events)

    monkeypatch.setattr(apply_module, "_running_process_names", lambda: [])
    monkeypatch.setattr(
        "koreader_ecosystem.backup.shutil.disk_usage",
        lambda _: type("Usage", (), {"free": 10**12})(),
    )
    with pytest.raises(KoreaderCalibreError) as backup_error:
        apply_preview(
            config,
            config.state_root / "koreader-calibre.sqlite3",
            stage,
            preview_id,
            FakeRestic(events, fail_check=True),
            runner=runner,
        )
    assert backup_error.value.reason_code == "backup_failed"
    assert not any(event.startswith("calibre:") for event in events)


def test_apply_aborts_on_integrity_failure_and_concurrent_calibre_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.calibre_apply as apply_module
    from koreader_ecosystem.backup import BackupError
    from koreader_ecosystem.calibre_apply import CalibreApplyError, apply_preview

    config, stage, _, _, preview_id = _prepare(tmp_path)
    events: list[str] = []
    current, _, runner = _install_fakes(monkeypatch, config, events)

    monkeypatch.setattr(
        apply_module,
        "_check_calibre_integrity",
        lambda _: (_ for _ in ()).throw(BackupError("invalid metadata")),
    )
    with pytest.raises(CalibreApplyError) as integrity_error:
        apply_preview(
            config,
            config.state_root / "koreader-calibre.sqlite3",
            stage,
            preview_id,
            FakeRestic(events),
            runner=runner,
        )
    assert integrity_error.value.reason_code == "calibre_integrity_failed"
    assert not any(event.startswith("restic:") for event in events)

    monkeypatch.setattr(apply_module, "_check_calibre_integrity", lambda _: None)
    current["koreader_progress"] = 0.2
    with pytest.raises(CalibreApplyError) as concurrent_error:
        apply_preview(
            config,
            config.state_root / "koreader-calibre.sqlite3",
            stage,
            preview_id,
            FakeRestic(events),
            runner=runner,
        )
    assert concurrent_error.value.reason_code == "concurrent_change"
    assert not any(event.startswith("restic:") for event in events)
    assert not any(event.startswith("calibre:") for event in events)
