from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from koreader_ecosystem.application_state import ApplicationState
from koreader_ecosystem.config import AppConfig, load_config
from koreader_ecosystem.profile_recovery import (
    ProfileRecoveryError,
    apply_profile_recovery,
    mark_verified_restore,
    preview_profile_recovery,
)


def _config(root: Path) -> AppConfig:
    return AppConfig(
        calibre_library=root / "Calibre",
        restic_repository=root / "Restic",
        state_root=root / "active-state",
    )


def _restored_profile(root: Path, *, snapshot_id: str = "a" * 64) -> Path:
    root.mkdir(parents=True)
    profile = root / "tree" / "application-state"
    profile.mkdir(parents=True)
    (profile / "config.json").write_text(
        json.dumps(
            {
                "calibre_library": str(root / "Recovered Calibre"),
                "restic_repository": str(root / "Recovered Restic"),
                "restic_remote_repository": None,
                "restic_remote_region": None,
                "state_root": "C:/old-profile",
            }
        ),
        encoding="utf-8",
    )
    state = ApplicationState(profile / "application-state.sqlite3")
    with state.connect() as connection:
        connection.execute(
            """
            INSERT INTO book_identities VALUES (
                ?, 'hardcover', 'user_book', ?,
                '2026-10-25T00:00:00Z', '2026-10-25T00:00:00Z'
            )
            """,
            ("restored-identity", "123"),
        )
        connection.execute(
            """
            INSERT INTO remote_cache VALUES (
                'hardcover', 'library_page', '10:0', '{}', '2026-10-25T00:00:00Z'
            )
            """
        )
    mark_verified_restore(root, snapshot_id=snapshot_id, file_count=2, total_bytes=100)
    return root


def test_preview_reads_verified_profile_and_redacts_config_paths(tmp_path: Path) -> None:
    source = _restored_profile(tmp_path / "verified-restore")
    source_config = (source / "tree" / "application-state" / "config.json").read_bytes()
    source_database = (
        source / "tree" / "application-state" / "application-state.sqlite3"
    ).read_bytes()

    preview = preview_profile_recovery(source)

    assert preview.schema_version == 7
    assert preview.row_counts["book_identities"] == 1
    assert preview.configured_calibre is True
    assert str(source) not in json.dumps(preview.to_dict())
    assert (source / "tree" / "application-state" / "config.json").read_bytes() == source_config
    assert (
        source / "tree" / "application-state" / "application-state.sqlite3"
    ).read_bytes() == source_database


def test_preview_rejects_unmarked_or_ambiguous_restore(tmp_path: Path) -> None:
    unmarked = _restored_profile(tmp_path / "unmarked")
    (unmarked / ".kre-verified-restore.json").unlink()
    with pytest.raises(ProfileRecoveryError, match="profile_restore_unverified"):
        preview_profile_recovery(unmarked)

    ambiguous = _restored_profile(tmp_path / "ambiguous")
    second = ambiguous / "other" / "application-state"
    second.mkdir(parents=True)
    (second / "config.json").write_text("{}", encoding="utf-8")
    (second / "application-state.sqlite3").write_bytes(
        (ambiguous / "tree" / "application-state" / "application-state.sqlite3").read_bytes()
    )
    with pytest.raises(ProfileRecoveryError, match="profile_recovery_ambiguous"):
        preview_profile_recovery(ambiguous)


def test_preview_rejects_corrupt_future_and_invalid_config(tmp_path: Path) -> None:
    future = _restored_profile(tmp_path / "future")
    database = future / "tree" / "application-state" / "application-state.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(ProfileRecoveryError, match="profile_recovery_schema_unsupported"):
        preview_profile_recovery(future)

    corrupt = _restored_profile(tmp_path / "corrupt")
    corrupt_db = corrupt / "tree" / "application-state" / "application-state.sqlite3"
    corrupt_db.write_bytes(b"not a sqlite database")
    with pytest.raises(ProfileRecoveryError, match="profile_recovery_database_invalid"):
        preview_profile_recovery(corrupt)

    invalid_config = _restored_profile(tmp_path / "invalid-config")
    config_path = invalid_config / "tree" / "application-state" / "config.json"
    config_path.write_text('{"secret_token":"should not be accepted"}', encoding="utf-8")
    with pytest.raises(ProfileRecoveryError, match="profile_recovery_config_invalid"):
        preview_profile_recovery(invalid_config)


def test_apply_checkpoints_current_profile_and_keeps_current_state_root(tmp_path: Path) -> None:
    source = _restored_profile(tmp_path / "verified-restore")
    source_config_path = source / "tree" / "application-state" / "config.json"
    source_database_path = source / "tree" / "application-state" / "application-state.sqlite3"
    original_library = tmp_path / "Calibre"
    original_library.mkdir()
    original_metadata = original_library / "metadata.db"
    original_metadata.write_bytes(b"original Calibre library sentinel")
    source_config = json.loads(source_config_path.read_text(encoding="utf-8"))
    source_config["calibre_library"] = str(original_library)
    source_config_path.write_text(json.dumps(source_config), encoding="utf-8")
    source_config_before = source_config_path.read_bytes()
    source_database_before = source_database_path.read_bytes()
    original_metadata_before = original_metadata.read_bytes()
    active_root = tmp_path / "active-state"
    active_root.mkdir()
    active_database = active_root / "application-state.sqlite3"
    active_state = ApplicationState(active_database)
    with active_state.connect() as connection:
        connection.execute(
            """
            INSERT INTO book_identities VALUES (
                ?, 'hardcover', 'user_book', ?,
                '2026-10-24T00:00:00Z', '2026-10-24T00:00:00Z'
            )
            """,
            ("previous-identity", "456"),
        )
    (active_root / "config.json").write_text(
        json.dumps(
            {
                "calibre_library": str(tmp_path / "Old Calibre"),
                "restic_repository": str(tmp_path / "Old Restic"),
                "state_root": str(active_root),
            }
        ),
        encoding="utf-8",
    )
    preview = preview_profile_recovery(source)

    result = apply_profile_recovery(
        source,
        active_root,
        preview.source_fingerprint,
        active_config=_config(tmp_path),
        confirmed=True,
    )

    with ApplicationState(active_database).connect() as connection:
        identities = {
            row[0] for row in connection.execute("SELECT identity_id FROM book_identities")
        }
    recovered_config = load_config(active_root / "config.json")
    checkpoint = active_root / "profile-recovery-checkpoints" / result.checkpoint_id
    assert identities == {"restored-identity"}
    assert recovered_config.state_root == active_root.resolve()
    assert recovered_config.calibre_library == original_library.resolve()
    assert (checkpoint / "application-state.sqlite3").is_file()
    assert (checkpoint / "config.json").is_file()
    assert result.requires_restart is True
    with ApplicationState(active_database).connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM remote_cache").fetchone()[0] == 0

    # A fresh config/database instance models the app reopening after restart.
    reopened_config = load_config(active_root / "config.json")
    with ApplicationState(
        reopened_config.state_root / "application-state.sqlite3"
    ).connect() as connection:
        reopened_identities = {
            row[0] for row in connection.execute("SELECT identity_id FROM book_identities")
        }
    assert reopened_config.calibre_library == original_library.resolve()
    assert reopened_identities == {"restored-identity"}
    assert source_config_path.read_bytes() == source_config_before
    assert source_database_path.read_bytes() == source_database_before
    assert original_metadata.read_bytes() == original_metadata_before


def test_apply_recovers_a_v5_profile_and_migrates_the_staged_copy(tmp_path: Path) -> None:
    source = _restored_profile(tmp_path / "verified-v5-restore")
    source_database = source / "tree" / "application-state" / "application-state.sqlite3"
    with sqlite3.connect(source_database) as connection:
        connection.execute("ALTER TABLE import_batches DROP COLUMN mapping_digest")
        connection.execute("DROP TABLE local_books")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 6")
        connection.execute("PRAGMA user_version = 5")

    active_root = tmp_path / "active-state"
    active_root.mkdir()
    ApplicationState(active_root / "application-state.sqlite3")
    active_config_path = active_root / "config.json"
    active_config_path.write_text(
        json.dumps(
            {
                "calibre_library": str(tmp_path / "Calibre"),
                "restic_repository": str(tmp_path / "Restic"),
                "state_root": str(active_root),
            }
        ),
        encoding="utf-8",
    )
    preview = preview_profile_recovery(source)
    assert preview.schema_version == 5

    result = apply_profile_recovery(
        source,
        active_root,
        preview.source_fingerprint,
        active_config=_config(tmp_path),
        confirmed=True,
    )

    with sqlite3.connect(active_root / "application-state.sqlite3") as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        columns = {row[1] for row in connection.execute("PRAGMA table_info(import_batches)")}
    assert result.schema_version == 7
    assert version == 7
    assert "mapping_digest" in columns


def test_apply_rejects_changed_source_without_touching_active_profile(tmp_path: Path) -> None:
    source = _restored_profile(tmp_path / "verified-restore")
    active_root = tmp_path / "active-state"
    active_root.mkdir()
    active_database = active_root / "application-state.sqlite3"
    ApplicationState(active_database)
    before = active_database.read_bytes()
    preview = preview_profile_recovery(source)
    (source / "tree" / "application-state" / "config.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ProfileRecoveryError, match="profile_recovery_source_changed"):
        apply_profile_recovery(
            source,
            active_root,
            preview.source_fingerprint,
            active_config=_config(tmp_path),
            confirmed=True,
        )
    assert active_database.read_bytes() == before


def test_apply_rolls_back_if_database_installation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.profile_recovery as recovery

    source = _restored_profile(tmp_path / "verified-restore")
    active_root = tmp_path / "active-state"
    active_root.mkdir()
    active_database = active_root / "application-state.sqlite3"
    active_state = ApplicationState(active_database)
    with active_state.connect() as connection:
        connection.execute(
            """
            INSERT INTO book_identities VALUES (
                ?, 'hardcover', 'user_book', ?,
                '2026-10-24T00:00:00Z', '2026-10-24T00:00:00Z'
            )
            """,
            ("old", "456"),
        )
    config_path = active_root / "config.json"
    old_config = json.dumps(
        {
            "calibre_library": str(tmp_path / "Old"),
            "restic_repository": str(tmp_path / "Restic"),
            "state_root": str(active_root),
        }
    )
    config_path.write_text(old_config, encoding="utf-8")
    preview = preview_profile_recovery(source)
    original_replace = recovery.os.replace
    failed = False

    def replace_once(source_path: str | Path, target_path: str | Path) -> None:
        nonlocal failed
        if not failed and Path(target_path) == active_database:
            failed = True
            raise OSError("injected database install failure")
        original_replace(source_path, target_path)

    monkeypatch.setattr(recovery.os, "replace", replace_once)

    with pytest.raises(ProfileRecoveryError, match="profile_recovery_install_failed"):
        apply_profile_recovery(
            source,
            active_root,
            preview.source_fingerprint,
            active_config=_config(tmp_path),
            confirmed=True,
        )

    assert config_path.read_text(encoding="utf-8") == old_config
    with ApplicationState(active_database).connect() as connection:
        identities = [
            row[0] for row in connection.execute("SELECT identity_id FROM book_identities")
        ]
    assert identities == ["old"]


def test_apply_requires_explicit_confirmation(tmp_path: Path) -> None:
    source = _restored_profile(tmp_path / "verified-restore")
    preview = preview_profile_recovery(source)
    with pytest.raises(ProfileRecoveryError, match="profile_recovery_confirmation_required"):
        apply_profile_recovery(
            source,
            tmp_path / "active-state",
            preview.source_fingerprint,
            active_config=_config(tmp_path),
            confirmed=False,
        )


def test_profile_recovery_preserves_local_reading_records(tmp_path: Path) -> None:
    from koreader_ecosystem.local_library import LocalLibrary

    source = _restored_profile(tmp_path / "verified-local-restore")
    database = source / "tree" / "application-state" / "application-state.sqlite3"
    library = LocalLibrary(ApplicationState(database))
    book = library.add_book(title="Fictional book", author="Fictional author")
    library.save_reading(
        book.book_id,
        status="reading",
        progress=37,
        rating=None,
        started_on="2026-10-01",
        finished_on=None,
        notes="Private fictional note",
    )
    preview = preview_profile_recovery(source)
    assert preview.row_counts["local_books"] == 1
    assert "Private fictional note" not in json.dumps(preview.to_dict())
    active_root = tmp_path / "active-state"
    apply_profile_recovery(
        source,
        active_root,
        preview.source_fingerprint,
        active_config=_config(tmp_path),
        confirmed=True,
    )
    restored = LocalLibrary(ApplicationState(active_root / "application-state.sqlite3"))
    assert restored.get_book(book.book_id).notes == "Private fictional note"
    assert restored.get_book(book.book_id).progress == 37
