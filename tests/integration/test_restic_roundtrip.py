from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from koreader_ecosystem.application_state import ApplicationState
from koreader_ecosystem.backup import create_backup
from koreader_ecosystem.config import AppConfig
from koreader_ecosystem.koreader_reading_history import (
    PageEvent,
    ReadingBook,
    ReadingHistoryPreview,
    ReadingSidecar,
)
from koreader_ecosystem.manifest import build_manifest
from koreader_ecosystem.mtp import MtpStageSummary
from koreader_ecosystem.reading_history_state import KoreaderReadingHistoryStore
from koreader_ecosystem.restic import ResticClient


def test_real_restic_backup_check_restore_and_hash_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "Calibre Library"
    (library / "books" / "nested").mkdir(parents=True)
    connection = sqlite3.connect(library / "metadata.db")
    connection.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)")
    connection.execute("INSERT INTO books (title) VALUES ('synthetic fixture')")
    connection.commit()
    connection.close()

    observed_at = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    start_time = observed_at - timedelta(minutes=4)
    source_digest = "a" * 64
    event_source_id = hashlib.sha256(
        f"42\x001\x00{int(start_time.timestamp())}".encode("ascii")
    ).hexdigest()
    reading_preview = ReadingHistoryPreview(
        source_digest=source_digest,
        books=(
            ReadingBook(
                koreader_book_id=42,
                title="Synthetic Reading History Book",
                authors="Fixture Author",
                notes=1,
                last_opened_at=start_time,
                highlights=1,
                pages=100,
                series="Fixture Series",
                language="eng",
                partial_md5_checksum="synthetic-partial-md5",
                total_read_time_seconds=60,
                total_read_pages=1,
                sidecar_ids=("d" * 64,),
            ),
        ),
        events=(
            PageEvent(
                event_id=event_source_id,
                koreader_book_id=42,
                page=1,
                start_time=start_time,
                duration_seconds=60,
                total_pages=100,
                source_digest=source_digest,
            ),
        ),
        sessions=(),
        warnings=(),
        sidecars=(
            ReadingSidecar(
                sidecar_id="d" * 64,
                source_digest="e" * 64,
                koreader_book_id=42,
                progress=0.01,
                status="reading",
                rating=4.5,
                note="Synthetic private note",
                position="epubcfi(/6/4)",
                annotations=({"pos0": "start", "pos1": "end", "text": "Synthetic highlight"},),
                warnings=(),
            ),
        ),
        observed_at=observed_at,
    )
    state_root = tmp_path / "local-state"
    reading_store = KoreaderReadingHistoryStore(
        ApplicationState(state_root / "application-state.sqlite3")
    )
    pending_batch = reading_store.stage_preview(reading_preview)
    applied_batch = reading_store.confirm_import(pending_batch.import_id, source_digest)
    assert applied_batch.status == "applied"
    (library / "empty.txt").write_bytes(b"")
    (library / "books" / "nested" / "unicode-π.txt").write_bytes(
        "conteúdo determinístico\n".encode()
    )
    (library / "books" / "payload.bin").write_bytes(bytes(range(256)) * 4)

    kindle_stage = tmp_path / "Kindle Stage"
    (kindle_stage / "documents").mkdir(parents=True)
    (kindle_stage / "documents" / "reading-status.txt").write_text(
        "synthetic kindle fixture", encoding="utf-8"
    )
    device_manifest = build_manifest(kindle_stage)
    device_files = tuple(entry for entry in device_manifest if entry.kind == "file")
    device_stage = MtpStageSummary(
        destination=kindle_stage,
        roots=("documents",),
        file_count=len(device_files),
        directory_count=sum(entry.kind == "directory" for entry in device_manifest),
        total_bytes=sum(entry.size for entry in device_files),
        manifest=device_manifest,
    )

    repository = tmp_path / "restic-repository"
    client = ResticClient(
        repository,
        password_provider=lambda: "synthetic-integration-passphrase-only",
    )
    if not client.available:
        pytest.skip("Restic is not installed")

    monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])
    config = AppConfig(
        calibre_library=library,
        restic_repository=repository,
        state_root=state_root,
    )
    result = create_backup(config, device_stage, client, verify_restore=True)

    validation_parent = config.state_root / "restore-validation"
    assert list(validation_parent.iterdir()) == []
    assert result.restore_verified is True
    assert result.snapshot_id
    assert result.kindle_file_count == 1
    assert result.application_state_file_count == 1

    restored_root = tmp_path / "independent-restoration"
    assert not restored_root.exists()
    restore_result = client.run(
        ["restore", result.snapshot_id, "--target", str(restored_root), "--json"]
    )
    assert restore_result.ok
    restored_databases = tuple(restored_root.rglob("application-state.sqlite3"))
    assert len(restored_databases) == 1

    restored_state = ApplicationState(restored_databases[0])
    restored_store = KoreaderReadingHistoryStore(restored_state)
    assert restored_store.get_import(applied_batch.import_id).status == "applied"
    restored_events = restored_store.get_events(limit=100)
    assert len(restored_events) == 1
    assert restored_events[0].source_event_id == event_source_id
    assert restored_events[0].payload["page"] == 1

    with restored_state.connect() as connection:
        links = connection.execute(
            "SELECT relation, confidence FROM identity_links ORDER BY relation"
        ).fetchall()
        annotations = connection.execute(
            "SELECT version, payload_json FROM annotation_versions ORDER BY version"
        ).fetchall()
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    assert [tuple(row) for row in links] == [("same_document", "exact")]
    assert len(annotations) == 1
    assert annotations[0][0] == 1
    assert '"text":"Synthetic highlight"' in annotations[0][1]
