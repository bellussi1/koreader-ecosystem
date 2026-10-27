from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path


class ApplicationStateError(RuntimeError):
    """Raised when the private application database cannot be opened safely."""


_SCHEMA_VERSION = 6
_MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE book_identities (
            identity_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL CHECK (provider IN ('calibre', 'hardcover', 'koreader')),
            external_type TEXT NOT NULL CHECK (length(external_type) BETWEEN 1 AND 64),
            external_id TEXT NOT NULL CHECK (length(external_id) BETWEEN 1 AND 512),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (provider, external_type, external_id)
        )
        """,
        """
        CREATE TABLE previews (
            preview_id TEXT PRIMARY KEY,
            plan_digest TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE preview_items (
            preview_id TEXT NOT NULL REFERENCES previews(preview_id) ON DELETE CASCADE,
            sidecar_key TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            source_digest TEXT NOT NULL,
            book_id INTEGER,
            calibre_uuid TEXT,
            item_json TEXT NOT NULL,
            PRIMARY KEY (preview_id, sidecar_key)
        )
        """,
        """
        CREATE TABLE manual_maps (
            sidecar_key TEXT PRIMARY KEY,
            book_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE book_checkpoints (
            calibre_uuid TEXT PRIMARY KEY,
            book_id INTEGER NOT NULL,
            sidecar_key TEXT NOT NULL,
            source_digest TEXT NOT NULL,
            values_json TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE identity_links (
            left_identity_id TEXT NOT NULL REFERENCES book_identities(identity_id),
            right_identity_id TEXT NOT NULL REFERENCES book_identities(identity_id),
            relation TEXT NOT NULL CHECK (length(relation) BETWEEN 1 AND 64),
            confidence TEXT NOT NULL CHECK (confidence IN ('exact', 'confirmed')),
            created_at TEXT NOT NULL,
            CHECK (left_identity_id <> right_identity_id),
            PRIMARY KEY (left_identity_id, right_identity_id)
        )
        """,
        """
        CREATE TABLE remote_cache (
            provider TEXT NOT NULL CHECK (provider = 'hardcover'),
            resource_type TEXT NOT NULL CHECK (length(resource_type) BETWEEN 1 AND 64),
            resource_id TEXT NOT NULL CHECK (length(resource_id) BETWEEN 1 AND 512),
            payload_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (provider, resource_type, resource_id)
        )
        """,
        """
        CREATE TABLE import_batches (
            import_id TEXT PRIMARY KEY,
            source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 64),
            source_digest TEXT NOT NULL CHECK (length(source_digest) = 64),
            status TEXT NOT NULL CHECK (status IN ('preview', 'applied', 'failed')),
            created_at TEXT NOT NULL,
            completed_at TEXT,
            record_count INTEGER NOT NULL DEFAULT 0 CHECK (record_count >= 0),
            reason_code TEXT,
            UNIQUE (source, source_digest)
        )
        """,
        """
        CREATE TABLE reading_events (
            event_id TEXT PRIMARY KEY,
            identity_id TEXT REFERENCES book_identities(identity_id),
            source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 64),
            source_event_id TEXT NOT NULL CHECK (length(source_event_id) BETWEEN 1 AND 512),
            event_type TEXT NOT NULL CHECK (length(event_type) BETWEEN 1 AND 64),
            occurred_at TEXT,
            observed_at TEXT NOT NULL,
            source_digest TEXT NOT NULL CHECK (length(source_digest) = 64),
            payload_json TEXT NOT NULL,
            UNIQUE (source, source_event_id)
        )
        """,
        """
        CREATE TABLE annotation_versions (
            annotation_version_id TEXT PRIMARY KEY,
            identity_id TEXT REFERENCES book_identities(identity_id),
            source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 64),
            source_annotation_id TEXT NOT NULL CHECK (
                length(source_annotation_id) BETWEEN 1 AND 512
            ),
            version INTEGER NOT NULL CHECK (version > 0),
            source_digest TEXT NOT NULL CHECK (length(source_digest) = 64),
            observed_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE (source, source_annotation_id, version)
        )
        """,
        """
        CREATE TABLE sync_outbox (
            operation_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL CHECK (length(provider) BETWEEN 1 AND 64),
            operation TEXT NOT NULL CHECK (length(operation) BETWEEN 1 AND 64),
            identity_id TEXT REFERENCES book_identities(identity_id),
            idempotency_key TEXT NOT NULL UNIQUE CHECK (length(idempotency_key) BETWEEN 1 AND 512),
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'sending', 'reconcile', 'complete', 'failed')
            ),
            attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            reason_code TEXT
        )
        """,
        """
        CREATE TABLE operations (
            operation_key TEXT PRIMARY KEY,
            status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed', 'cancelled')),
            updated_at TEXT NOT NULL,
            last_success_at TEXT,
            reason_code TEXT
        )
        """,
        "CREATE TABLE hardcover_requests (requested_at INTEGER NOT NULL)",
        """
        CREATE TABLE legacy_imports (
            source_key TEXT PRIMARY KEY,
            source_digest TEXT NOT NULL CHECK (length(source_digest) = 64),
            imported_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX reading_events_by_identity ON reading_events(identity_id, occurred_at)",
        "CREATE INDEX preview_items_by_book ON preview_items(calibre_uuid, preview_id)",
        "CREATE INDEX cache_by_fetched_at ON remote_cache(fetched_at)",
        "CREATE INDEX outbox_by_status ON sync_outbox(status, created_at)",
    ),
    2: (
        "ALTER TABLE reading_events ADD COLUMN import_id TEXT "
        "REFERENCES import_batches(import_id) ON DELETE SET NULL",
        "CREATE INDEX reading_events_by_import ON reading_events(import_id, occurred_at)",
    ),
    3: (
        "ALTER TABLE annotation_versions ADD COLUMN import_id TEXT "
        "REFERENCES import_batches(import_id) ON DELETE SET NULL",
        "CREATE INDEX annotation_versions_by_import ON annotation_versions(import_id, observed_at)",
    ),
    4: (
        """
        CREATE TABLE csv_import_rows (
            import_id TEXT NOT NULL REFERENCES import_batches(import_id) ON DELETE CASCADE,
            row_number INTEGER NOT NULL CHECK (row_number >= 2),
            row_type TEXT NOT NULL CHECK (row_type IN ('record', 'issue')),
            record_json TEXT,
            reason_code TEXT CHECK (
                reason_code IS NULL OR length(reason_code) BETWEEN 1 AND 64
            ),
            PRIMARY KEY (import_id, row_number),
            CHECK (
                (row_type = 'record' AND record_json IS NOT NULL AND reason_code IS NULL)
                OR (row_type = 'issue' AND record_json IS NULL AND reason_code IS NOT NULL)
            )
        )
        """,
        "CREATE INDEX csv_import_rows_by_batch ON csv_import_rows(import_id, row_number)",
    ),
    5: (
        """
        CREATE TABLE sync_outbox_reviews (
            operation_id TEXT PRIMARY KEY
                REFERENCES sync_outbox(operation_id) ON DELETE CASCADE,
            resolution TEXT NOT NULL CHECK (
                resolution IN ('confirmed_remote', 'closed_locally')
            ),
            reviewed_at TEXT NOT NULL
        )
        """,
    ),
    6: (
        "ALTER TABLE import_batches ADD COLUMN mapping_digest TEXT NOT NULL DEFAULT '' "
        "CHECK (mapping_digest = '' OR length(mapping_digest) = 64)",
    ),
}
_LEGACY_SYNC_TABLES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("previews", ("preview_id", "plan_digest", "created_at"), ("preview_id",)),
    (
        "preview_items",
        (
            "preview_id",
            "sidecar_key",
            "relative_path",
            "source_digest",
            "book_id",
            "calibre_uuid",
            "item_json",
        ),
        ("preview_id", "sidecar_key"),
    ),
    ("manual_maps", ("sidecar_key", "book_id", "updated_at"), ("sidecar_key",)),
    (
        "book_checkpoints",
        (
            "calibre_uuid",
            "book_id",
            "sidecar_key",
            "source_digest",
            "values_json",
            "applied_at",
        ),
        ("calibre_uuid",),
    ),
)


def _safe_database_path(database: Path) -> Path:
    candidate = Path(database).expanduser()
    if not candidate.is_absolute():
        raise ApplicationStateError("Application database path must be absolute")
    current = candidate
    while current != current.parent:
        try:
            if current.is_symlink() or current.is_junction():
                raise ApplicationStateError("Application database path cannot use a link")
        except OSError:
            raise ApplicationStateError("Application database path cannot be checked") from None
        current = current.parent
    if candidate.exists() and not candidate.is_file():
        raise ApplicationStateError("Application database path is not a file")
    return candidate


class ApplicationState:
    """Versioned SQLite database for private, durable application state."""

    def __init__(self, database: Path) -> None:
        self.database = _safe_database_path(database)
        self._initialize()

    def _initialize(self) -> None:
        try:
            self.database.parent.mkdir(parents=True, exist_ok=True)
            if self.database.is_symlink() or self.database.is_junction():
                raise ApplicationStateError("Application database path cannot use a link")
            with closing(sqlite3.connect(self.database, timeout=10)) as connection:
                connection.execute("PRAGMA busy_timeout = 10000")
                connection.execute("PRAGMA foreign_keys = ON")
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version > _SCHEMA_VERSION:
                    raise ApplicationStateError(
                        "Application database schema is newer than this app"
                    )
                for target_version in range(version + 1, _SCHEMA_VERSION + 1):
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        for statement in _MIGRATIONS[target_version]:
                            connection.execute(statement)
                        connection.execute(
                            "INSERT INTO schema_migrations(version, applied_at) "
                            "VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                            (target_version,),
                        )
                        connection.execute(f"PRAGMA user_version = {target_version}")
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
        except ApplicationStateError:
            raise
        except OSError, sqlite3.Error:
            raise ApplicationStateError("Application database could not be initialized") from None

    def migrate_legacy_sync_database(self, legacy_database: Path) -> int:
        """Copy known KOReader–Calibre records without modifying the source."""
        source_path = _safe_database_path(Path(legacy_database))
        if source_path == self.database:
            raise ApplicationStateError("Legacy database cannot be the application database")
        if not source_path.is_file():
            return 0

        try:
            source_uri = f"{source_path.resolve().as_uri()}?mode=ro"
            with closing(sqlite3.connect(source_uri, uri=True, timeout=10)) as source:
                source.execute("PRAGMA busy_timeout = 10000")
                version = int(source.execute("PRAGMA user_version").fetchone()[0])
                integrity = source.execute("PRAGMA integrity_check").fetchone()
                table_names = {
                    row[0]
                    for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
                if version not in {1, 2} or integrity != ("ok",):
                    raise ApplicationStateError("Legacy sync database is invalid or unsupported")

                records: dict[str, tuple[tuple[str, ...], list[tuple[object, ...]]]] = {}
                for table, columns, _ in _LEGACY_SYNC_TABLES:
                    if table not in table_names:
                        raise ApplicationStateError("Legacy sync database is missing required data")
                    actual_columns = tuple(
                        row[1] for row in source.execute(f"PRAGMA table_info({table})")
                    )
                    if actual_columns != columns:
                        raise ApplicationStateError("Legacy sync database schema is unsupported")
                    selected = ", ".join(columns)
                    rows = [tuple(row) for row in source.execute(f"SELECT {selected} FROM {table}")]
                    records[table] = (columns, rows)

            migration_key = "koreader-calibre-sqlite-v1"
            migration_digest = hashlib.sha256(migration_key.encode("ascii")).hexdigest()
            copied = 0
            with self.connect() as target:
                existing = target.execute(
                    "SELECT 1 FROM legacy_imports WHERE source_key = ?", (migration_key,)
                ).fetchone()
                if existing is not None:
                    return 0

                for table, columns, primary_key in _LEGACY_SYNC_TABLES:
                    rows = records[table][1]
                    if not rows:
                        continue
                    column_names = ", ".join(columns)
                    placeholders = ", ".join("?" for _ in columns)
                    insert = (
                        f"INSERT OR IGNORE INTO {table} ({column_names}) VALUES ({placeholders})"
                    )
                    key_positions = tuple(columns.index(key) for key in primary_key)
                    where = " AND ".join(f"{key} = ?" for key in primary_key)
                    for row in rows:
                        result = target.execute(insert, row)
                        if result.rowcount == 1:
                            copied += 1
                            continue
                        key_values = tuple(row[position] for position in key_positions)
                        existing_row = target.execute(
                            f"SELECT {column_names} FROM {table} WHERE {where}", key_values
                        ).fetchone()
                        if existing_row is None or tuple(existing_row) != row:
                            raise ApplicationStateError(
                                "Legacy sync data conflicts with current state"
                            )
                target.execute(
                    "INSERT INTO legacy_imports(source_key, source_digest, imported_at) "
                    "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                    (migration_key, migration_digest),
                )
            return copied
        except ApplicationStateError:
            raise
        except OSError, sqlite3.Error:
            raise ApplicationStateError("Legacy sync database could not be migrated") from None

    def migrate_legacy_hardcover_requests(self, legacy_database: Path) -> int:
        """Copy the old local Hardcover request ledger without changing its source."""
        source_path = _safe_database_path(Path(legacy_database))
        if source_path == self.database:
            raise ApplicationStateError("Legacy request ledger cannot be the application database")
        if not source_path.is_file():
            return 0

        try:
            source_uri = f"{source_path.resolve().as_uri()}?mode=ro"
            with closing(sqlite3.connect(source_uri, uri=True, timeout=10)) as source:
                source.execute("PRAGMA busy_timeout = 10000")
                version = int(source.execute("PRAGMA user_version").fetchone()[0])
                integrity = source.execute("PRAGMA integrity_check").fetchone()
                table_names = {
                    row[0]
                    for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
                columns = tuple(
                    row[1] for row in source.execute("PRAGMA table_info(hardcover_requests)")
                )
                if (
                    integrity != ("ok",)
                    or version != 0
                    or "hardcover_requests" not in table_names
                    or columns != ("requested_at",)
                ):
                    raise ApplicationStateError("Legacy request ledger is invalid or unsupported")
                rows = [
                    row[0]
                    for row in source.execute(
                        "SELECT requested_at FROM hardcover_requests ORDER BY requested_at"
                    )
                ]
                if any(not isinstance(timestamp, int) or timestamp < 0 for timestamp in rows):
                    raise ApplicationStateError("Legacy request ledger contains invalid timestamps")

            digest = hashlib.sha256()
            for timestamp in rows:
                digest.update(str(timestamp).encode("ascii"))
                digest.update(b"\n")
            source_digest = digest.hexdigest()
            source_key = "hardcover-requests-sqlite-v1"
            with self.connect() as target:
                existing = target.execute(
                    "SELECT 1 FROM legacy_imports WHERE source_key = ?", (source_key,)
                ).fetchone()
                if existing is not None:
                    return 0
                target.executemany(
                    "INSERT INTO hardcover_requests (requested_at) VALUES (?)",
                    ((timestamp,) for timestamp in rows),
                )
                target.execute(
                    "INSERT INTO legacy_imports(source_key, source_digest, imported_at) "
                    "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                    (source_key, source_digest),
                )
            return len(rows)
        except ApplicationStateError:
            raise
        except OSError, sqlite3.Error:
            raise ApplicationStateError("Legacy request ledger could not be migrated") from None

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a configured connection and commit only after the caller succeeds."""
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.database, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            yield connection
            connection.commit()
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()
