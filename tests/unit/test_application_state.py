from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from koreader_ecosystem.application_state import (
    ApplicationState,
    ApplicationStateError,
)


def test_initializes_versioned_application_state_schema(tmp_path: Path) -> None:
    database = tmp_path / "private" / "application-state.sqlite3"

    ApplicationState(database)

    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        import_batch_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(import_batches)")
        }
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        migrations = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()

    assert version == 7
    assert migrations == [(1,), (2,), (3,), (4,), (5,), (6,), (7,)]
    assert "mapping_digest" in import_batch_columns
    assert {
        "previews",
        "preview_items",
        "manual_maps",
        "book_checkpoints",
        "book_identities",
        "identity_links",
        "remote_cache",
        "import_batches",
        "reading_events",
        "annotation_versions",
        "sync_outbox",
        "operations",
        "hardcover_requests",
        "schema_migrations",
        "csv_import_rows",
        "sync_outbox_reviews",
    }.issubset(tables)


def test_upgrades_v1_and_preserves_existing_reading_events(tmp_path: Path) -> None:
    database = tmp_path / "application-state.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE import_batches (
                import_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                source_digest TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                record_count INTEGER NOT NULL DEFAULT 0,
                reason_code TEXT,
                UNIQUE (source, source_digest)
            );
            CREATE TABLE reading_events (
                event_id TEXT PRIMARY KEY,
                identity_id TEXT,
                source TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT,
                observed_at TEXT NOT NULL,
                source_digest TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE (source, source_event_id)
            );
            CREATE TABLE annotation_versions (
                annotation_version_id TEXT PRIMARY KEY,
                identity_id TEXT,
                source TEXT NOT NULL,
                source_annotation_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                source_digest TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE (source, source_annotation_id, version)
            );
            INSERT INTO schema_migrations(version, applied_at)
            VALUES (1, '2026-10-24T00:00:00Z');
            INSERT INTO reading_events VALUES (
                'event-1', NULL, 'koreader', 'sidecar-1', 'progress', NULL,
                '2026-10-24T00:00:00Z', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
                || 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', '{"progress":0.25}'
            );
            PRAGMA user_version = 1;
            """
        )

    ApplicationState(database)

    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        event = connection.execute(
            "SELECT event_id, source_event_id, payload_json FROM reading_events"
        ).fetchone()
        import_id_column = connection.execute("PRAGMA table_info(reading_events)").fetchall()
        migration_count = connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]

    assert version == 7
    assert migration_count == 7
    assert {column[1] for column in import_id_column} >= {"import_id"}
    assert event == ("event-1", "sidecar-1", '{"progress":0.25}')


def test_upgrades_v2_to_v3_and_preserves_annotation_versions(tmp_path: Path) -> None:
    database = tmp_path / "application-state.sqlite3"
    from koreader_ecosystem.application_state import _MIGRATIONS

    with sqlite3.connect(database) as connection:
        for statement in _MIGRATIONS[1] + _MIGRATIONS[2]:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (1, '2026-10-25T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (2, '2026-10-25T00:00:00Z')"
        )
        connection.execute(
            """
            INSERT INTO annotation_versions(
                annotation_version_id, identity_id, source, source_annotation_id,
                version, source_digest, observed_at, payload_json
            ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                "annotation-version-1",
                "koreader",
                "source-annotation-1",
                1,
                "a" * 64,
                "2026-10-25T00:00:00Z",
                '{"text":"preserve"}',
            ),
        )
        connection.execute("PRAGMA user_version = 2")

    ApplicationState(database)

    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        migration_versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        annotation = connection.execute(
            "SELECT annotation_version_id, payload_json FROM annotation_versions"
        ).fetchone()
        columns = {row[1] for row in connection.execute("PRAGMA table_info(annotation_versions)")}
        foreign_keys = connection.execute("PRAGMA foreign_key_list(annotation_versions)").fetchall()
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(annotation_versions)")}

    assert version == 7
    assert migration_versions == [(1,), (2,), (3,), (4,), (5,), (6,), (7,)]
    assert annotation == ("annotation-version-1", '{"text":"preserve"}')
    assert "import_id" in columns
    assert any(row[2] == "import_batches" and row[3] == "import_id" for row in foreign_keys)
    assert "annotation_versions_by_import" in indexes


def test_v3_migration_rolls_back_if_index_creation_fails(tmp_path: Path) -> None:
    database = tmp_path / "application-state.sqlite3"
    with sqlite3.connect(database) as connection:
        from koreader_ecosystem.application_state import _MIGRATIONS

        for statement in _MIGRATIONS[1] + _MIGRATIONS[2]:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (1, '2026-10-25T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (2, '2026-10-25T00:00:00Z')"
        )
        connection.execute(
            "CREATE INDEX annotation_versions_by_import ON annotation_versions(source)"
        )
        connection.execute("PRAGMA user_version = 2")

    with pytest.raises(ApplicationStateError):
        ApplicationState(database)

    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        columns = {row[1] for row in connection.execute("PRAGMA table_info(annotation_versions)")}
        migrations = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()

    assert version == 2
    assert "import_id" not in columns
    assert migrations == [(1,), (2,)]


def test_upgrades_v3_to_v4_and_preserves_existing_import_data(tmp_path: Path) -> None:
    database = tmp_path / "application-state.sqlite3"
    with sqlite3.connect(database) as connection:
        from koreader_ecosystem.application_state import _MIGRATIONS

        for version in (1, 2, 3):
            for statement in _MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, "2026-10-25T00:00:00Z"),
            )
        connection.execute(
            """
            INSERT INTO import_batches(
                import_id, source, source_digest, status, created_at, record_count
            ) VALUES (?, ?, ?, 'applied', ?, ?)
            """,
            ("legacy-import", "koreader", "a" * 64, "2026-10-25T00:00:00Z", 2),
        )
        connection.execute("PRAGMA user_version = 3")

    ApplicationState(database)

    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        existing = connection.execute(
            "SELECT import_id, source, status, record_count, mapping_digest FROM import_batches"
        ).fetchone()
        migration_versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        columns = {row[1] for row in connection.execute("PRAGMA table_info(csv_import_rows)")}
        foreign_keys = connection.execute("PRAGMA foreign_key_list(csv_import_rows)").fetchall()

    assert version == 7
    assert migration_versions == [(1,), (2,), (3,), (4,), (5,), (6,), (7,)]
    assert existing == ("legacy-import", "koreader", "applied", 2, "")
    assert columns == {"import_id", "row_number", "row_type", "record_json", "reason_code"}
    assert any(row[2] == "import_batches" and row[3] == "import_id" for row in foreign_keys)


def test_v4_migration_rolls_back_when_csv_index_creation_fails(tmp_path: Path) -> None:
    database = tmp_path / "application-state.sqlite3"
    from koreader_ecosystem.application_state import _MIGRATIONS

    with sqlite3.connect(database) as connection:
        for version in (1, 2, 3):
            for statement in _MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, "2026-10-25T00:00:00Z"),
            )
        connection.execute("CREATE INDEX csv_import_rows_by_batch ON import_batches(status)")
        connection.execute("PRAGMA user_version = 3")

    with pytest.raises(ApplicationStateError):
        ApplicationState(database)

    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        table_names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        migrations = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()

    assert version == 3
    assert "csv_import_rows" not in table_names
    assert migrations == [(1,), (2,), (3,)]


def test_imports_legacy_sync_state_once_without_changing_its_source(tmp_path: Path) -> None:
    database = tmp_path / "application-state.sqlite3"
    legacy = tmp_path / "koreader-calibre.sqlite3"
    preview_id = "b" * 64
    sidecar_key = "a" * 64
    with sqlite3.connect(legacy) as connection:
        connection.executescript(
            """
            CREATE TABLE previews (
                preview_id TEXT PRIMARY KEY,
                plan_digest TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            CREATE TABLE preview_items (
                preview_id TEXT NOT NULL REFERENCES previews(preview_id) ON DELETE CASCADE,
                sidecar_key TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                source_digest TEXT NOT NULL,
                book_id INTEGER,
                calibre_uuid TEXT,
                item_json TEXT NOT NULL,
                PRIMARY KEY (preview_id, sidecar_key)
            );
            CREATE TABLE manual_maps (
                sidecar_key TEXT PRIMARY KEY,
                book_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE book_checkpoints (
                calibre_uuid TEXT PRIMARY KEY,
                book_id INTEGER NOT NULL,
                sidecar_key TEXT NOT NULL,
                source_digest TEXT NOT NULL,
                values_json TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
            CREATE INDEX preview_items_by_book ON preview_items(calibre_uuid, preview_id);
            """
        )
        connection.execute(
            "INSERT INTO previews VALUES (?, ?, ?)",
            (preview_id, preview_id, "2026-10-23T12:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO preview_items VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                preview_id,
                sidecar_key,
                "Books/Example.sdr/metadata.epub.lua",
                "c" * 64,
                7,
                "0f8fad5b-d9cb-469f-a165-70867728950e",
                '{"title":"Example"}',
            ),
        )
        connection.execute("INSERT INTO manual_maps VALUES (?, ?, ?)", (sidecar_key, 7, "now"))
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
    original = legacy.read_bytes()

    state = ApplicationState(database)
    state.migrate_legacy_sync_database(legacy)

    with sqlite3.connect(database) as connection:
        preview = connection.execute("SELECT preview_id, created_at FROM previews").fetchone()
        item = connection.execute("SELECT relative_path, book_id FROM preview_items").fetchone()
        mapping = connection.execute("SELECT book_id FROM manual_maps").fetchone()
    assert preview == (preview_id, "2026-10-23T12:00:00+00:00")
    assert item == ("Books/Example.sdr/metadata.epub.lua", 7)
    assert mapping == (7,)
    assert legacy.read_bytes() == original

    state.migrate_legacy_sync_database(legacy)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM preview_items").fetchone() == (1,)


def test_imports_legacy_hardcover_request_ledger_once_without_changing_source(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application-state.sqlite3"
    legacy = tmp_path / "hardcover-requests.sqlite3"
    with sqlite3.connect(legacy) as connection:
        connection.execute("CREATE TABLE hardcover_requests (requested_at INTEGER NOT NULL)")
        connection.executemany(
            "INSERT INTO hardcover_requests (requested_at) VALUES (?)",
            [(1_800_000_000,), (1_800_000_030,)],
        )
    original = legacy.read_bytes()
    state = ApplicationState(database)

    assert state.migrate_legacy_hardcover_requests(legacy) == 2
    assert state.migrate_legacy_hardcover_requests(legacy) == 0

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT requested_at FROM hardcover_requests ORDER BY requested_at"
        ).fetchall() == [(1_800_000_000,), (1_800_000_030,)]
    assert legacy.read_bytes() == original


def test_rejects_unknown_legacy_request_ledger_schema_without_modifying_source(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application-state.sqlite3"
    legacy = tmp_path / "hardcover-requests.sqlite3"
    with sqlite3.connect(legacy) as connection:
        connection.execute("CREATE TABLE hardcover_requests (requested_at INTEGER NOT NULL)")
        connection.execute("INSERT INTO hardcover_requests VALUES (1800000000)")
        connection.execute("PRAGMA user_version = 99")
    original = legacy.read_bytes()
    state = ApplicationState(database)

    with pytest.raises(ApplicationStateError):
        state.migrate_legacy_hardcover_requests(legacy)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM hardcover_requests").fetchone() == (0,)
    assert legacy.read_bytes() == original


def test_rejects_future_schema_without_modifying_database(tmp_path: Path) -> None:
    database = tmp_path / "application-state.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sentinel VALUES ('preserve')")
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(ApplicationStateError):
        ApplicationState(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99
        assert connection.execute("SELECT value FROM sentinel").fetchone() == ("preserve",)
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'book_identities'"
            ).fetchone()
            is None
        )


def test_rejects_relative_database_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ApplicationStateError):
        ApplicationState(Path("relative.sqlite3"))
