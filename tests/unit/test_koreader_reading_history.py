from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from koreader_ecosystem.koreader_reading_history import (
    EstimatedSession,
    PageEvent,
    ReadingHistoryError,
    ReadingHistoryPreview,
    estimate_reading_sessions,
    preview_reading_history,
)

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "koreader"
DATABASE_FIXTURE = FIXTURE_ROOT / "statistics-v20221111.sqlite3"
SIDECAR_FIXTURE = FIXTURE_ROOT / "metadata.epub.lua"
OBSERVED_AT = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _stage_fixture(tmp_path: Path) -> Path:
    stage = tmp_path / "stage"
    database_dir = stage / "koreader" / "settings"
    sidecar_dir = stage / "documents" / "Synthetic Fixture Book.epub.sdr"
    database_dir.mkdir(parents=True)
    sidecar_dir.mkdir(parents=True)
    shutil.copy2(DATABASE_FIXTURE, database_dir / "statistics.sqlite3")
    shutil.copy2(SIDECAR_FIXTURE, sidecar_dir / "metadata.epub.lua")
    return stage


def _database(stage: Path) -> Path:
    return stage / "koreader" / "settings" / "statistics.sqlite3"


def _set_database_version(database: Path, version: int) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(f"PRAGMA user_version={version}")


def _create_wal_database(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.executescript(
        """
        CREATE TABLE book (
            id INTEGER PRIMARY KEY, title TEXT, authors TEXT, notes INTEGER,
            last_open INTEGER, highlights INTEGER, pages INTEGER, series TEXT,
            language TEXT, md5 TEXT, total_read_time INTEGER, total_read_pages INTEGER
        );
        CREATE TABLE page_stat_data (
            id_book INTEGER, page INTEGER, start_time INTEGER, duration INTEGER,
            total_pages INTEGER, UNIQUE(id_book, page, start_time)
        );
        PRAGMA user_version=20221111;
        INSERT INTO book VALUES (1, 'WAL Fixture', 'Fixture Author', 0, 1760000000,
            0, 10, NULL, 'eng', 'fixture-key', 5, 1);
        INSERT INTO page_stat_data VALUES (1, 1, 1760000000, 5, 10);
        """
    )
    connection.commit()
    return connection


def test_preview_reads_the_recognized_fixture_and_links_sidecar_by_exact_identity(
    tmp_path: Path,
) -> None:
    stage = _stage_fixture(tmp_path)

    preview = preview_reading_history(stage, observed_at=OBSERVED_AT)

    assert isinstance(preview, ReadingHistoryPreview)
    assert re.fullmatch(r"[0-9a-f]{64}", preview.source_digest)
    assert len(preview.books) == 1
    book = preview.books[0]
    assert (book.koreader_book_id, book.title, book.authors) == (
        7,
        "Synthetic Fixture Book",
        "Fixture Author",
    )
    assert book.partial_md5_checksum == "fixture-partial-md5"
    assert len(preview.events) == 2
    assert preview.events[0].page == 1
    assert preview.events[0].duration_seconds == 60
    assert len(preview.sidecars) == 1
    assert preview.sidecars[0].koreader_book_id == 7
    assert preview.sidecars[0].progress == 0.375
    assert preview.sidecars[0].title == "Synthetic Fixture Book"
    assert preview.sidecars[0].authors == "Fixture Author"
    assert preview.sidecars[0].rating == 4.5
    assert preview.sidecars[0].note == "Synthetic note"
    assert preview.sidecars[0].annotations[0]["text"] == "Synthetic highlight"
    assert len(preview.sessions) == 1
    assert preview.sessions[0].estimated is True
    assert all("doc_path" not in item for item in preview.sidecars[0].annotations)


def test_database_is_opened_through_a_read_only_sqlite_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = _stage_fixture(tmp_path)
    database = _database(stage)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    calls: list[tuple[str, dict[str, object]]] = []
    original_connect = sqlite3.connect

    def tracked_connect(database_path: str, *args: object, **kwargs: object) -> sqlite3.Connection:
        calls.append((database_path, dict(kwargs)))
        return original_connect(database_path, *args, **kwargs)

    monkeypatch.setattr(
        "koreader_ecosystem.koreader_reading_history.sqlite3.connect", tracked_connect
    )

    preview_reading_history(stage, observed_at=OBSERVED_AT)

    assert calls
    assert "mode=ro" in calls[0][0]
    assert calls[0][1]["uri"] is True
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_parser_reads_committed_rows_from_a_wal_companion(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    database_dir = stage / "koreader" / "settings"
    database_dir.mkdir(parents=True)
    source = tmp_path / "source.sqlite3"
    connection = _create_wal_database(source)
    try:
        shutil.copy2(source, database_dir / "statistics.sqlite3")
        for suffix in ("-wal", "-shm"):
            staged_database = database_dir / "statistics.sqlite3"
            shutil.copy2(Path(f"{source}{suffix}"), Path(f"{staged_database}{suffix}"))

        preview = preview_reading_history(stage, observed_at=OBSERVED_AT)

        assert len(preview.books) == 1
        assert preview.books[0].title == "WAL Fixture"
        assert len(preview.events) == 1
    finally:
        connection.close()


def test_rejects_a_partial_wal_family(tmp_path: Path) -> None:
    stage = _stage_fixture(tmp_path)
    (stage / "koreader" / "settings" / "statistics.sqlite3-wal").write_bytes(b"partial")

    with pytest.raises(ReadingHistoryError) as error:
        preview_reading_history(stage, observed_at=OBSERVED_AT)

    assert error.value.reason_code == "wal_incomplete"


def test_rejects_database_integrity_failure(tmp_path: Path) -> None:
    stage = _stage_fixture(tmp_path)
    _database(stage).write_bytes(b"not a sqlite database")

    with pytest.raises(ReadingHistoryError) as error:
        preview_reading_history(stage, observed_at=OBSERVED_AT)

    assert error.value.reason_code == "database_integrity"


def test_rejects_unknown_database_user_version(tmp_path: Path) -> None:
    stage = _stage_fixture(tmp_path)
    _set_database_version(_database(stage), 20271001)

    with pytest.raises(ReadingHistoryError) as error:
        preview_reading_history(stage, observed_at=OBSERVED_AT)

    assert error.value.reason_code == "schema_unsupported"


@pytest.mark.parametrize(
    "schema_sql",
    [
        "CREATE TABLE page_stat_data (id_book INTEGER)",
        "CREATE TABLE book (id INTEGER PRIMARY KEY); CREATE TABLE page_stat_data "
        "(id_book INTEGER, page INTEGER, start_time INTEGER, duration INTEGER)",
    ],
)
def test_rejects_missing_database_tables_or_columns(tmp_path: Path, schema_sql: str) -> None:
    stage = _stage_fixture(tmp_path)
    database = _database(stage)
    database.unlink()
    with sqlite3.connect(database) as connection:
        connection.executescript(f"PRAGMA user_version=20221111; {schema_sql};")

    with pytest.raises(ReadingHistoryError) as error:
        preview_reading_history(stage, observed_at=OBSERVED_AT)

    assert error.value.reason_code == "schema_invalid"


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("start_time", -1),
        ("duration", -1),
        ("total_pages", 0),
        ("page", 101),
    ],
)
def test_rejects_invalid_dates_durations_and_page_counts(
    tmp_path: Path, column: str, value: int
) -> None:
    stage = _stage_fixture(tmp_path)
    with sqlite3.connect(_database(stage)) as connection:
        connection.execute(f"UPDATE page_stat_data SET {column}=? WHERE rowid=1", (value,))

    with pytest.raises(ReadingHistoryError) as error:
        preview_reading_history(stage, observed_at=OBSERVED_AT)

    assert error.value.reason_code == "invalid_data"


def test_rejects_malformed_sidecar_without_returning_a_partial_preview(tmp_path: Path) -> None:
    stage = _stage_fixture(tmp_path)
    sidecar = next(stage.rglob("metadata.*.lua"))
    sidecar.write_text("return { [", encoding="utf-8")

    with pytest.raises(ReadingHistoryError) as error:
        preview_reading_history(stage, observed_at=OBSERVED_AT)

    assert error.value.reason_code == "invalid_sidecar"


def test_rejects_symlinked_database_path_traversal(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    database_dir = stage / "koreader" / "settings"
    database_dir.mkdir(parents=True)
    link = database_dir / "statistics.sqlite3"
    try:
        link.symlink_to(DATABASE_FIXTURE)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(ReadingHistoryError) as error:
        preview_reading_history(stage, observed_at=OBSERVED_AT)

    assert error.value.reason_code == "invalid_path"


def test_rejects_junctioned_database_path_traversal(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    shutil.copy2(DATABASE_FIXTURE, external / "statistics.sqlite3")
    junction = stage / "koreader"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(external)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("directory junction creation is unavailable")

    try:
        with pytest.raises(ReadingHistoryError) as error:
            preview_reading_history(stage, observed_at=OBSERVED_AT)
    finally:
        junction.rmdir()

    assert error.value.reason_code == "invalid_path"


def test_rejects_ebook_payload_in_the_staging_directory(tmp_path: Path) -> None:
    stage = _stage_fixture(tmp_path)
    (stage / "documents" / "book.epub").write_bytes(b"payload")

    with pytest.raises(ReadingHistoryError) as error:
        preview_reading_history(stage, observed_at=OBSERVED_AT)

    assert error.value.reason_code == "unexpected_file"


def test_event_ids_and_source_digest_are_stable_across_staging_locations(tmp_path: Path) -> None:
    first = preview_reading_history(_stage_fixture(tmp_path / "first"), observed_at=OBSERVED_AT)
    second = preview_reading_history(_stage_fixture(tmp_path / "second"), observed_at=OBSERVED_AT)

    assert first.source_digest == second.source_digest
    assert tuple(event.event_id for event in first.events) == tuple(
        event.event_id for event in second.events
    )


def test_sidecars_without_an_exact_book_identity_remain_unlinked(tmp_path: Path) -> None:
    stage = _stage_fixture(tmp_path)
    sidecar = next(stage.rglob("metadata.*.lua"))
    sidecar.write_text(
        SIDECAR_FIXTURE.read_text(encoding="utf-8").replace("fixture-partial-md5", "other-key"),
        encoding="utf-8",
    )

    preview = preview_reading_history(stage, observed_at=OBSERVED_AT)

    assert preview.sidecars[0].koreader_book_id is None
    assert preview.sidecars[0].title == "Synthetic Fixture Book"
    assert preview.sidecars[0].authors == "Fixture Author"
    assert "sidecar_unmatched" in preview.warnings


def test_sidecar_without_display_metadata_uses_only_its_parent_folder_label(
    tmp_path: Path,
) -> None:
    stage = _stage_fixture(tmp_path)
    sidecar = next(stage.rglob("metadata.*.lua"))
    source = sidecar.read_text(encoding="utf-8")
    source = re.sub(
        r'\["doc_props"\] = \{.*?\n    \},', '["doc_props"] = {},', source, count=1, flags=re.S
    )
    sidecar.write_text(source, encoding="utf-8")

    preview = preview_reading_history(stage, observed_at=OBSERVED_AT)

    assert preview.sidecars[0].koreader_book_id is None
    assert preview.sidecars[0].title == "Synthetic Fixture Book"
    assert preview.sidecars[0].title_source == "folder"
    assert preview.sidecars[0].authors is None


def test_estimated_sessions_split_at_the_exact_inactivity_boundary() -> None:
    started = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
    events = (
        PageEvent("a", 1, 1, started, 60, 100, "digest"),
        PageEvent("b", 1, 2, started + timedelta(minutes=31), 30, 100, "digest"),
        PageEvent("c", 1, 3, started + timedelta(minutes=61, seconds=29), 10, 100, "digest"),
    )

    sessions = estimate_reading_sessions(events)

    assert len(sessions) == 2
    assert all(isinstance(session, EstimatedSession) for session in sessions)
    assert sessions[0].estimated is True
    assert (sessions[0].page_event_count, sessions[0].duration_seconds) == (1, 60)
    assert (sessions[1].page_event_count, sessions[1].duration_seconds) == (2, 40)
    assert (sessions[1].first_page, sessions[1].last_page) == (2, 3)
