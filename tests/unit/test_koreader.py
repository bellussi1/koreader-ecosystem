from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from koreader_ecosystem.koreader import (
    PluginInstallError,
    _extract_plugin_archive,
    _plugin_config,
    _validate_plugin_archive,
    build_plugin_install_tree,
    install_hardcover_plugin,
    install_hardcover_with_secret,
    prepare_hardcover_plugin,
    update_hardcover_plugin,
)


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)


def _patch_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, bytes]:
    import koreader_ecosystem.koreader as koreader

    patch_path = tmp_path / "0.4.0.patch"
    patch_bytes = json.dumps(
        {
            "schema_version": 1,
            "upstream_version": "0.4.0",
            "operations": [
                {
                    "path": "main.lua",
                    "old": "local release = true\n",
                    "new": "local release = true\nlocal automatic_reads = true\n",
                }
            ],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    patch_path.write_bytes(patch_bytes)
    monkeypatch.setattr(koreader, "PLUGIN_PATCH_FILE", patch_path, raising=False)
    monkeypatch.setattr(
        koreader, "PLUGIN_PATCH_SHA256", hashlib.sha256(patch_bytes).hexdigest(), raising=False
    )
    return patch_path, patch_bytes


def test_plugin_config_escapes_token_as_a_lua_string() -> None:
    assert _plugin_config('a"b\\c') == 'return {\n  token = "a\\"b\\\\c"\n}\n'


def test_build_install_tree_cleans_staged_secret_on_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.koreader as koreader

    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "main.lua").write_text("return true\n", encoding="utf-8")

    def interrupted_copy(_source: Path, destination: Path) -> None:
        destination.mkdir(parents=True)
        (destination / "partial.lua").write_text("partial", encoding="utf-8")
        raise KeyboardInterrupt

    monkeypatch.setattr(koreader.shutil, "copytree", interrupted_copy)

    with pytest.raises(KeyboardInterrupt):
        build_plugin_install_tree(plugin, tmp_path / "state", "private-token")

    staging = tmp_path / "state" / "staging"
    assert not staging.exists() or list(staging.iterdir()) == []


def test_plugin_archive_accepts_only_expected_tree_and_extracts(tmp_path: Path) -> None:
    archive_path = tmp_path / "plugin.zip"
    _write_zip(
        archive_path,
        {
            "hardcoverapp.koplugin/main.lua": b"return true\n",
            "hardcoverapp.koplugin/hardcover_config.example.lua": b"return { token = '' }\n",
            "hardcoverapp.koplugin/LICENSE": b"MIT\n",
            "hardcoverapp.koplugin/lib/api.lua": b"return {}\n",
        },
    )

    entries = _validate_plugin_archive(archive_path)
    extracted = _extract_plugin_archive(archive_path, tmp_path / "extract")

    assert len(entries) == 4
    assert (extracted / "main.lua").read_bytes() == b"return true\n"
    assert hashlib.sha256((extracted / "lib" / "api.lua").read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "entry_name",
    [
        "../outside.lua",
        "/hardcoverapp.koplugin/main.lua",
        "hardcoverapp.koplugin/../../outside.lua",
        "other-plugin/main.lua",
        "hardcoverapp.koplugin/a.lua",
        "hardcoverapp.koplugin/A.lua",
    ],
)
def test_plugin_archive_rejects_unsafe_or_colliding_paths(tmp_path: Path, entry_name: str) -> None:
    archive_path = tmp_path / "plugin.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("hardcoverapp.koplugin/main.lua", b"ok")
        archive.writestr(entry_name, b"unsafe")

    with pytest.raises(PluginInstallError):
        _validate_plugin_archive(archive_path)


def test_plugin_archive_rejects_symlink_entries(tmp_path: Path) -> None:
    archive_path = tmp_path / "plugin.zip"
    info = zipfile.ZipInfo("hardcoverapp.koplugin/link.lua")
    info.create_system = 3
    info.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(info, "../../outside.lua")

    with pytest.raises(PluginInstallError):
        _validate_plugin_archive(archive_path)


def test_prepare_plugin_rejects_cached_archive_with_wrong_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.koreader as koreader

    download_root = tmp_path / "downloads"
    download_root.mkdir()
    archive_path = download_root / f"hardcoverapp.koplugin-{koreader.PLUGIN_VERSION}.zip"
    archive_path.write_bytes(b"not the pinned release")
    monkeypatch.setattr(koreader, "PLUGIN_SHA256", "0" * 64)

    with pytest.raises(PluginInstallError) as error:
        prepare_hardcover_plugin(tmp_path)

    assert error.value.reason_code == "package_digest_mismatch"
    assert not (download_root / f"hardcoverapp.koplugin-{koreader.PLUGIN_VERSION}").exists()


def test_verified_plugin_release_receives_pinned_lifecycle_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.koreader as koreader

    _patch_fixture(tmp_path, monkeypatch)
    archive_path = tmp_path / "plugin.zip"
    _write_zip(
        archive_path,
        {
            "hardcoverapp.koplugin/main.lua": b"local release = true\n",
            "hardcoverapp.koplugin/hardcover_config.example.lua": b"return { token = '' }\n",
            "hardcoverapp.koplugin/LICENSE": b"MIT\n",
        },
    )
    archive_bytes = archive_path.read_bytes()
    download_root = tmp_path / "state" / "downloads"
    download_root.mkdir(parents=True)
    cached_archive = download_root / "hardcoverapp.koplugin-0.4.0.zip"
    cached_archive.write_bytes(archive_bytes)
    monkeypatch.setattr(koreader, "PLUGIN_SHA256", hashlib.sha256(archive_bytes).hexdigest())

    prepared = prepare_hardcover_plugin(tmp_path / "state")

    assert (prepared / "main.lua").read_text(encoding="utf-8") == (
        "local release = true\nlocal automatic_reads = true\n"
    )
    assert prepared.name == "hardcoverapp.koplugin-0.4.0-patched"


def test_plugin_patch_rejects_unexpected_upstream_source_without_changing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.koreader as koreader

    _patch_fixture(tmp_path, monkeypatch)
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    main = plugin / "main.lua"
    main.write_text("local release = changed\n", encoding="utf-8")

    with pytest.raises(PluginInstallError) as error:
        koreader.apply_hardcover_plugin_patch(plugin)

    assert error.value.reason_code == "source_changed"
    assert main.read_text(encoding="utf-8") == "local release = changed\n"


def test_pinned_lifecycle_patch_covers_start_progress_finish_and_offline_conflicts() -> None:
    import koreader_ecosystem.koreader as koreader

    operations = koreader._load_plugin_patch()
    sources = {operation["path"]: operation["new"] for operation in operations}
    main = "\n".join(
        operation["new"] for operation in operations if operation["path"] == "main.lua"
    )
    cache = sources["hardcover/lib/cache.lua"]
    api = sources["hardcover/lib/hardcover_api.lua"]

    assert "ensureReadingRecord" in cache
    assert "Api:createRead" in cache
    assert "hardcover_started_at" in cache
    assert "Api:finishRead" in cache
    assert "finished_at" in api
    assert "syncLatestPage" in main
    assert "hardcover_last_synced_page" in main
    assert "Progress changed in both places while offline" in main
    assert 'os.date("%Y-%m-%d")' in main


def test_progress_retry_records_confirmation_and_keeps_menu_status_visible() -> None:
    import koreader_ecosystem.koreader as koreader

    operations = koreader._load_plugin_patch()
    main = "\n".join(
        operation["new"] for operation in operations if operation["path"] == "main.lua"
    )

    assert "hardcover_last_synced_at" in main
    assert "hardcover_sync_pending" in main
    assert "_scheduleProgressRetry" in main
    assert "Progress changed in both places while offline" in main
    assert 'pending == "conflict"' in main
    assert "Last Hardcover sync: " in main
    assert "Progress sync pending" in main


def test_plugin_patch_caps_requests_per_rolling_minute() -> None:
    import koreader_ecosystem.koreader as koreader

    main = "\n".join(
        operation["new"]
        for operation in koreader._load_plugin_patch()
        if operation["path"] == "main.lua"
    )

    assert "local HARDCOVER_APP_PER_MINUTE_LIMIT = 25" in main
    assert "timestamp > now - 60" in main
    assert "minute_count >= HARDCOVER_APP_PER_MINUTE_LIMIT" in main
    assert (
        main.count("return nil, HARDCOVER_APP_DAILY_LIMIT, HARDCOVER_APP_PER_MINUTE_LIMIT, false")
        == 2
    )
    assert "Hardcover sync paused: local request limit reached" in main
    assert "24-hour request limit reached" not in main


def test_plugin_preparation_preserves_an_existing_patched_cache_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.koreader as koreader

    _patch_fixture(tmp_path, monkeypatch)
    archive_path = tmp_path / "plugin.zip"
    _write_zip(
        archive_path,
        {
            "hardcoverapp.koplugin/main.lua": b"local release = true\n",
            "hardcoverapp.koplugin/hardcover_config.example.lua": b"return { token = '' }\n",
            "hardcoverapp.koplugin/LICENSE": b"MIT\n",
        },
    )
    archive_bytes = archive_path.read_bytes()
    download_root = tmp_path / "state" / "downloads"
    download_root.mkdir(parents=True)
    (download_root / "hardcoverapp.koplugin-0.4.0.zip").write_bytes(archive_bytes)
    monkeypatch.setattr(koreader, "PLUGIN_SHA256", hashlib.sha256(archive_bytes).hexdigest())
    existing = download_root / "hardcoverapp.koplugin-0.4.0-patched"
    existing.mkdir()
    sentinel = existing / "main.lua"
    sentinel.write_text("user data\n", encoding="utf-8")

    with pytest.raises(PluginInstallError) as error:
        prepare_hardcover_plugin(tmp_path / "state")

    assert error.value.reason_code == "destination_exists"
    assert sentinel.read_text(encoding="utf-8") == "user data\n"


def test_mtp_installer_checks_destination_before_creating_plugin_directory() -> None:
    from koreader_ecosystem.koreader import INSTALL_SCRIPT

    script = INSTALL_SCRIPT.read_text(encoding="utf-8")
    guard = '$existing = Get-SingleItem -Folder $pluginsFolder -Name "hardcoverapp.koplugin"'
    create = '[void]$pluginsFolder.NewFolder("hardcoverapp.koplugin")'

    assert script.index(guard) < script.index(
        'if ($null -ne $existing) { throw "destination_exists" }'
    )
    assert script.index('if ($null -ne $existing) { throw "destination_exists" }') < script.index(
        create
    )


def test_mtp_plugin_update_moves_existing_file_before_copy_and_restores_by_rename() -> None:
    from koreader_ecosystem.koreader import INSTALL_SCRIPT

    script = INSTALL_SCRIPT.read_text(encoding="utf-8")
    update = script[
        script.index("function Invoke-SafePluginUpdate") : script.index("$reasonCode =")
    ]

    move_original = "$record.Item.Name = $record.RemoteBackupName"
    copy_updated = "$record.Folder.CopyHere($sourceItem, 0x414)"
    restore_original = "$remoteBackup.Name = $record.Leaf"

    assert move_original in update
    assert copy_updated in update
    assert restore_original in update
    assert update.index(move_original) < update.index(copy_updated)
    assert update.index(copy_updated) < update.index(restore_original)
    assert ".kre-backup-" in update
    assert ".kre-failed-" in update
    assert "[IO.Path]::GetExtension($leaf)" in update
    assert "UpdateManifestJson" in script
    assert "legacy_sha256" in script
    assert "$acceptedHashes -ccontains $currentHash" in update
    assert "for ($index = $records.Count - 1" in update


def test_install_wrapper_uses_no_secret_arguments_and_checks_aggregate_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.koreader as koreader

    source = tmp_path / "source"
    source.mkdir()
    (source / "main.lua").write_text("return true\n", encoding="utf-8")
    (source / "hardcover_config.lua").write_text(
        'return { token = "private-token" }\n', encoding="utf-8"
    )
    command_seen: list[str] = []

    def runner(command, **_kwargs):
        command_seen.extend(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "schema_version": 1,
                    "status": "ok",
                    "reason_code": None,
                    "file_count": 2,
                    "total_bytes": sum(path.stat().st_size for path in source.iterdir()),
                    "content_verified": True,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(koreader, "_powershell_executable", lambda: "pwsh")
    result = install_hardcover_plugin(source, runner=runner)

    assert result.file_count == 2
    assert "private-token" not in " ".join(command_seen)
    assert str(source) in command_seen


def test_install_wrapper_rejects_mismatched_readback(tmp_path: Path, monkeypatch) -> None:
    import koreader_ecosystem.koreader as koreader

    source = tmp_path / "source"
    source.mkdir()
    (source / "main.lua").write_text("return true\n", encoding="utf-8")
    monkeypatch.setattr(koreader, "_powershell_executable", lambda: "pwsh")

    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "schema_version": 1,
                    "status": "ok",
                    "reason_code": None,
                    "file_count": 1,
                    "total_bytes": 999,
                    "content_verified": True,
                }
            ),
            stderr="",
        )

    with pytest.raises(PluginInstallError) as error:
        install_hardcover_plugin(source, runner=runner)

    assert error.value.reason_code == "source_changed"


def test_plugin_update_keeps_verified_local_rollback_copies_for_all_patched_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.koreader as koreader

    source = tmp_path / "patched-plugin"
    source.mkdir()
    patch_operations = [
        ("main.lua", b"original lifecycle\n", b"patched lifecycle\n"),
        ("hardcover/lib/cache.lua", b"original cache\n", b"patched cache\n"),
        ("hardcover/lib/hardcover_api.lua", b"original api\n", b"patched api\n"),
    ]
    patch_path = tmp_path / "0.4.0.patch"
    patch_bytes = json.dumps(
        {
            "schema_version": 1,
            "upstream_version": "0.4.0",
            "operations": [
                {
                    "path": path,
                    "old": old.decode("utf-8"),
                    "new": new.decode("utf-8"),
                }
                for path, old, new in patch_operations
            ],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    patch_path.write_bytes(patch_bytes)
    monkeypatch.setattr(koreader, "PLUGIN_PATCH_FILE", patch_path)
    monkeypatch.setattr(koreader, "PLUGIN_PATCH_SHA256", hashlib.sha256(patch_bytes).hexdigest())
    for relative, _original, patched in patch_operations:
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(patched)
    (source / "reader.lua").write_bytes(b"preserved upstream file\n")
    archive_path = source.parent / "hardcoverapp.koplugin-0.4.0.zip"
    _write_zip(
        archive_path,
        {
            **{
                f"hardcoverapp.koplugin/{relative}": original
                for relative, original, _patched in patch_operations
            },
            "hardcoverapp.koplugin/hardcover_config.example.lua": b"return {}\n",
            "hardcoverapp.koplugin/LICENSE": b"license\n",
        },
    )
    monkeypatch.setattr(
        koreader,
        "PLUGIN_SHA256",
        hashlib.sha256(archive_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(koreader, "_tree_matches_archive", lambda *_args, **_kwargs: True)
    rollback_root = tmp_path / "plugin-backups"
    command_seen: list[str] = []
    patched_bytes = sum(len(patched) for _path, _original, patched in patch_operations)

    monkeypatch.setattr(koreader, "_powershell_executable", lambda: "pwsh")

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        command_seen.extend(command)
        rollback_directory = Path(command[command.index("-RollbackDirectory") + 1])
        manifest_json = command[command.index("-UpdateManifestJson") + 1]
        manifest = json.loads(manifest_json)
        for item, (_relative, original, _patched) in zip(manifest, patch_operations, strict=True):
            backup_path = rollback_directory / item["path"]
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_bytes(original)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "schema_version": 2,
                    "status": "ok",
                    "reason_code": None,
                    "action": "updated",
                    "file_count": len(patch_operations),
                    "total_bytes": patched_bytes,
                    "content_verified": True,
                    "rollback_backup_id": rollback_directory.name,
                    "rollback_backup_hashes": [
                        {
                            "path": relative,
                            "sha256": hashlib.sha256(original).hexdigest(),
                        }
                        for relative, original, _patched in patch_operations
                    ],
                }
            ),
            stderr="",
        )

    result = update_hardcover_plugin(
        source,
        rollback_root,
        runner=runner,
    )

    assert result.updated
    assert result.file_count == 3
    assert result.total_bytes == patched_bytes
    assert result.rollback_backup_id is not None
    for relative, original, _patched in patch_operations:
        saved_file = rollback_root / result.rollback_backup_id / relative
        assert saved_file.read_bytes() == original
    assert command_seen[command_seen.index("-Mode") + 1] == "update"
    update_manifest = json.loads(command_seen[command_seen.index("-UpdateManifestJson") + 1])
    assert [item["path"] for item in update_manifest] == [
        relative for relative, _original, _patched in patch_operations
    ]
    for item, (relative, original, patched) in zip(update_manifest, patch_operations, strict=True):
        assert item["path"] == relative
        assert item["original_sha256"] == hashlib.sha256(original).hexdigest()
        assert item["patched_sha256"] == hashlib.sha256(patched).hexdigest()
        assert item["legacy_sha256"] == koreader._PLUGIN_PREVIOUS_PATCH_SHA256[relative]
    assert "reader.lua" not in json.dumps(update_manifest)


def test_plugin_update_rejects_a_source_without_main_lua_before_mtp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.koreader as koreader

    source = tmp_path / "plugin"
    source.mkdir()
    (source / "reader.lua").write_text("no lifecycle file", encoding="utf-8")
    monkeypatch.setattr(
        koreader,
        "_powershell_executable",
        lambda: (_ for _ in ()).throw(AssertionError("MTP must not start")),
    )

    with pytest.raises(PluginInstallError) as error:
        update_hardcover_plugin(
            source,
            tmp_path / "backups",
        )

    assert error.value.reason_code == "package_invalid"


def test_install_flow_removes_temporary_plaintext_configuration_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.hardcover as hardcover
    import koreader_ecosystem.koreader as koreader

    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "main.lua").write_text("return true\n", encoding="utf-8")
    monkeypatch.setattr(koreader, "prepare_hardcover_plugin", lambda *_args, **_kwargs: plugin)

    class FakeClient:
        def verify_token(self, token: str) -> None:
            assert token == "private-token"

    monkeypatch.setattr(hardcover, "HardcoverClient", FakeClient)
    created_staging: list[Path] = []

    def installer(source: Path):
        config_path = source / "hardcover_config.lua"
        assert "private-token" in config_path.read_text(encoding="utf-8")
        created_staging.append(source.parent)
        raise PluginInstallError("copy_failed")

    with pytest.raises(PluginInstallError):
        install_hardcover_with_secret(tmp_path / "state", "private-token", installer=installer)

    assert len(created_staging) == 1
    assert not created_staging[0].exists()
