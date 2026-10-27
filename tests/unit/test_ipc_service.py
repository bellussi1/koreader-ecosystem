from __future__ import annotations

import io
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from koreader_ecosystem.calibre_apply import ApplyResult
from koreader_ecosystem.config import AppConfig
from koreader_ecosystem.ipc_service import (
    IpcOperationError,
    _manifest_has_profile,
    build_dispatcher,
    main,
    serve_jsonl,
)
from koreader_ecosystem.koreader_reading_history import (
    EstimatedSession,
    PageEvent,
    ReadingBook,
    ReadingHistoryPreview,
    ReadingSidecar,
)
from koreader_ecosystem.platform_csv_state import (
    PlatformCsvImportBatch,
    PlatformCsvImportPage,
    StoredCsvEntry,
    StoredCsvEntryPage,
)
from koreader_ecosystem.reading_history_state import (
    ReadingImportBatch,
    StoredPageEvent,
    StoredPageEventPage,
)
from koreader_ecosystem.services import (
    KoreaderReadingHistoryImportPreview,
    PlatformCsvImportPreview,
)

type Dispatcher = Callable[[str, dict[str, object]], object]


def test_scheduled_backup_entrypoint_runs_without_jsonl_or_console_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import koreader_ecosystem.ipc_service as ipc_service

    config = object()
    calls: list[object] = []

    class Services:
        def __init__(self, received_config: object) -> None:
            assert received_config is config

        def run_scheduled_backup(self) -> None:
            calls.append("run")

    monkeypatch.setattr(ipc_service, "load_config", lambda: config)
    monkeypatch.setattr(ipc_service, "ApplicationServices", Services)

    assert main(["--scheduled-backup"]) == 0
    assert calls == ["run"]


def test_scheduled_backup_entrypoint_returns_failure_without_leaking_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import koreader_ecosystem.ipc_service as ipc_service

    class Services:
        def __init__(self, _config: object) -> None:
            pass

        def run_scheduled_backup(self) -> None:
            raise RuntimeError("private path and credential details")

    monkeypatch.setattr(ipc_service, "load_config", lambda: object())
    monkeypatch.setattr(ipc_service, "ApplicationServices", Services)

    assert main(["--scheduled-backup"]) == 1
    assert capsys.readouterr() == ("", "")


def test_service_entrypoint_rejects_unknown_arguments() -> None:
    assert main(["--unexpected"]) == 2


def _request(
    request_id: str,
    operation: str = "calibre.list",
    payload: dict[str, object] | None = None,
    *,
    version: int = 1,
) -> str:
    return json.dumps(
        {
            "version": version,
            "id": request_id,
            "operation": operation,
            "payload": payload or {},
        },
        ensure_ascii=False,
    )


def _serve(source: str, dispatcher: Dispatcher) -> list[dict[str, Any]]:
    output = io.StringIO()
    serve_jsonl(io.StringIO(source), output, dispatcher)
    lines = output.getvalue().splitlines()
    return [json.loads(line) for line in lines]


def test_serve_jsonl_returns_catalog_response_with_request_id() -> None:
    expected = {"books": [{"book_id": 7, "title": "Coração de Tinta"}], "total": 1}

    def dispatch(operation: str, payload: dict[str, object]) -> object:
        assert operation == "calibre.list"
        assert payload == {}
        return expected

    response = _serve(_request("request-1") + "\n", dispatch)[0]

    assert response == {
        "version": 1,
        "id": "request-1",
        "ok": True,
        "result": expected,
    }


def test_serve_jsonl_rejects_invalid_version_without_echoing_payload() -> None:
    response = _serve(
        _request(
            "request-2",
            payload={"hostile": "must-not-be-reflected"},
            version=2,
        )
        + "\n",
        lambda operation, payload: None,
    )[0]

    assert response["version"] == 1
    assert response["id"] == "request-2"
    assert response["ok"] is False
    assert response["error"]["code"] == "unsupported_protocol"
    assert "must-not-be-reflected" not in json.dumps(response)


def test_serve_jsonl_rejects_line_over_one_megabyte_and_continues() -> None:
    oversized = json.dumps(
        {
            "version": 1,
            "id": "too-large",
            "operation": "system.status",
            "payload": {"padding": "x" * (1024 * 1024)},
        }
    )
    responses = _serve(
        oversized + "\n" + _request("next", "system.status") + "\n",
        lambda operation, payload: {"status": "ready"},
    )

    assert len(responses) == 2
    assert responses[0]["ok"] is False
    assert responses[0]["error"]["code"] == "request_too_large"
    assert responses[1]["id"] == "next"
    assert responses[1]["result"] == {"status": "ready"}


def test_serve_jsonl_processes_multiple_requests_until_eof() -> None:
    responses = _serve(
        _request("first", "system.status") + "\n" + _request("second", "system.status") + "\n",
        lambda operation, payload: {"status": "ready"},
    )

    assert [response["id"] for response in responses] == ["first", "second"]
    assert all(response["ok"] for response in responses)


def test_serve_jsonl_does_not_return_internal_exception_text() -> None:
    def dispatch(operation: str, payload: dict[str, object]) -> object:
        raise RuntimeError("private-path-or-token")

    response = _serve(_request("request-3", "system.status") + "\n", dispatch)[0]

    assert response["ok"] is False
    assert response["error"]["code"] == "operation_failed"
    assert "private-path-or-token" not in json.dumps(response)


def test_calibre_removal_requires_confirmation_and_calls_protected_service(
    tmp_path,
) -> None:
    calls: list[tuple[int, bool]] = []
    config = AppConfig(
        calibre_library=tmp_path / "library",
        restic_repository=tmp_path / "restic",
        state_root=tmp_path / "state",
    )

    class Services:
        def remove_calibre_book(self, book_id: int, *, recovery_copy_confirmed: bool) -> None:
            calls.append((book_id, recovery_copy_confirmed))

    dispatcher = build_dispatcher(
        config_loader=lambda: config,
        services_factory=lambda _config: Services(),  # type: ignore[arg-type]
    )
    result = dispatcher(
        "calibre.remove",
        {"book_id": 7, "confirmed": True, "recovery_copy_confirmed": True},
    )

    assert result == {"removed": True}
    assert calls == [(7, True)]
    with pytest.raises(IpcOperationError) as error:
        dispatcher(
            "calibre.remove",
            {"book_id": 7, "confirmed": False, "recovery_copy_confirmed": True},
        )
    assert error.value.code == "invalid_payload"
    assert calls == [(7, True)]


def test_profile_recovery_ipc_requires_absolute_source_and_explicit_confirmation(tmp_path) -> None:
    fingerprint = "a" * 64
    source = tmp_path / "verified restore"
    calls: list[tuple[str, str, bool]] = []
    config = AppConfig(
        calibre_library=tmp_path / "library",
        restic_repository=tmp_path / "restic",
        state_root=tmp_path / "state",
    )

    class Services:
        def preview_profile_recovery(self, restore_root: Path) -> dict[str, object]:
            assert restore_root == source
            return {
                "source_fingerprint": fingerprint,
                "schema_version": 3,
                "row_counts": {"reading_events": 4},
                "configured_calibre": True,
                "configured_restic": True,
                "configured_remote_backup": False,
            }

        def apply_profile_recovery(
            self,
            restore_root: Path,
            source_fingerprint: str,
            *,
            confirmed: bool,
        ) -> dict[str, object]:
            calls.append((str(restore_root), source_fingerprint, confirmed))
            return {"checkpoint_id": "b" * 32, "schema_version": 3, "requires_restart": True}

    dispatcher = build_dispatcher(
        config_loader=lambda: config,
        services_factory=lambda _config: Services(),  # type: ignore[arg-type]
    )

    preview = dispatcher("profile-recovery.preview", {"restore_root": str(source)})
    assert preview["source_fingerprint"] == fingerprint
    applied = dispatcher(
        "profile-recovery.apply",
        {"restore_root": str(source), "source_fingerprint": fingerprint, "confirmed": True},
    )
    assert applied["requires_restart"] is True
    assert calls == [(str(source), fingerprint, True)]
    with pytest.raises(IpcOperationError) as error:
        dispatcher(
            "profile-recovery.apply",
            {"restore_root": str(source), "source_fingerprint": fingerprint, "confirmed": False},
        )
    assert error.value.code == "invalid_payload"
    with pytest.raises(IpcOperationError) as error:
        dispatcher("profile-recovery.preview", {"restore_root": "relative-path"})
    assert error.value.code == "invalid_payload"


def test_restore_manifest_exposes_only_complete_profile_pairs() -> None:
    from types import SimpleNamespace

    assert _manifest_has_profile(
        (
            SimpleNamespace(path="source/application-state/config.json"),
            SimpleNamespace(path="source/application-state/application-state.sqlite3"),
        )
    )
    assert not _manifest_has_profile(
        (SimpleNamespace(path="source/application-state/application-state.sqlite3"),)
    )


def test_koreader_reading_preview_is_bounded_and_does_not_expose_paths(tmp_path) -> None:
    observed_at = datetime(2026, 10, 25, tzinfo=UTC)
    book = ReadingBook(
        koreader_book_id=42,
        title="Synthetic Fixture Book",
        authors="Fixture Author",
        notes=1,
        last_opened_at=observed_at,
        highlights=2,
        pages=100,
        series="Fixture Series",
        language="eng",
        partial_md5_checksum="private-fingerprint",
        total_read_time_seconds=600,
        total_read_pages=20,
        sidecar_ids=("b" * 64,),
    )
    sidecar = ReadingSidecar(
        sidecar_id="b" * 64,
        source_digest="f" * 64,
        koreader_book_id=42,
        progress=0.375,
        status="reading",
        rating=4.5,
        note="Fixture note",
        position="epubcfi(/6/4)",
        annotations=({"text": "Fixture highlight"},),
        warnings=(),
    )
    unmatched_sidecar = ReadingSidecar(
        sidecar_id="c" * 64,
        source_digest="e" * 64,
        koreader_book_id=None,
        progress=0.7,
        status="complete",
        rating=3.5,
        note="Private unmatched note",
        position=None,
        annotations=(),
        warnings=("sidecar_unmatched",),
        title="Unmatched Fixture Book",
        authors=None,
        title_source="folder",
    )
    session = EstimatedSession(
        session_id="c" * 64,
        koreader_book_id=42,
        started_at=observed_at,
        ended_at=observed_at,
        page_event_count=1,
        duration_seconds=600,
        first_page=1,
        last_page=2,
    )
    event = PageEvent("e" * 64, 42, 2, observed_at, 600, 100, "a" * 64)
    preview = ReadingHistoryPreview(
        source_digest="a" * 64,
        books=(book,),
        events=(event, event),
        sessions=(session,),
        warnings=("estimated_sessions", "progress_conflict", "sidecar_unmatched"),
        sidecars=(sidecar, unmatched_sidecar),
        observed_at=observed_at,
    )
    batch = ReadingImportBatch(
        import_id="d" * 64,
        source="koreader",
        source_digest="a" * 64,
        status="preview",
        created_at=observed_at.isoformat(),
        completed_at=None,
        record_count=4,
        reason_code=None,
    )

    class Services:
        def preview_koreader_reading_history(self):
            return KoreaderReadingHistoryImportPreview(batch=batch, preview=preview)

    config = AppConfig(
        calibre_library=tmp_path / "library",
        restic_repository=tmp_path / "restic",
        state_root=tmp_path / "state",
    )
    dispatcher = build_dispatcher(
        config_loader=lambda: config,
        services_factory=lambda _config: Services(),  # type: ignore[arg-type]
    )

    result = dispatcher("koreader.readings.preview", {})

    assert result["batch"]["status"] == "preview"
    assert result["summary"]["book_count"] == 1
    assert result["summary"]["page_event_count"] == 2
    assert result["estimated_sessions"][0]["estimated"] is True
    assert result["books"][0]["sidecars"][0]["annotation_count"] == 1
    assert result["books"][0]["statistics_progress"] == 0.2
    assert result["books"][0]["warnings"] == ["progress_conflict"]
    assert result["unmatched_sidecars"][0]["sidecar_id"] == "c" * 64
    assert result["unmatched_sidecars"][0]["progress"] == 0.7
    assert result["unmatched_sidecars"][0]["status"] == "complete"
    assert result["unmatched_sidecars"][0]["has_note"] is True
    assert result["unmatched_sidecars"][0]["title"] == "Unmatched Fixture Book"
    assert result["unmatched_sidecars"][0]["title_source"] == "folder"
    serialized = json.dumps(result)
    assert "private-fingerprint" not in serialized
    assert "Private unmatched note" not in serialized
    assert "path" not in serialized.casefold()


def test_koreader_reading_events_enforces_a_hundred_row_page_cap(tmp_path) -> None:
    calls: list[tuple[int, int]] = []

    class Services:
        def get_koreader_reading_events(self, *, limit: int, offset: int):
            calls.append((limit, offset))
            event = StoredPageEvent(
                event_id="a" * 64,
                identity_id="b" * 64,
                source_event_id="event-1",
                occurred_at="2026-10-25T00:00:00Z",
                observed_at="2026-10-25T00:01:00Z",
                source_digest="c" * 64,
                payload={"page": 12, "total_pages": 200},
            )
            return StoredPageEventPage((event,), True)

    config = AppConfig(
        calibre_library=tmp_path / "library",
        restic_repository=tmp_path / "restic",
        state_root=tmp_path / "state",
    )
    dispatcher = build_dispatcher(
        config_loader=lambda: config,
        services_factory=lambda _config: Services(),  # type: ignore[arg-type]
    )

    assert dispatcher(
        "koreader.readings.events",
        {"limit": 100, "offset": 200},
    ) == {
        "events": [
            {
                "event_id": "a" * 64,
                "identity_id": "b" * 64,
                "source_event_id": "event-1",
                "occurred_at": "2026-10-25T00:00:00Z",
                "observed_at": "2026-10-25T00:01:00Z",
                "source_digest": "c" * 64,
                "payload": {"page": 12, "total_pages": 200},
            }
        ],
        "has_more": True,
    }
    assert calls == [(100, 200)]

    for payload in (
        {"limit": 101},
        {"limit": True},
        {"offset": -1},
        {"import_id": "a" * 64},
        {"limit": 10, "unexpected": 1},
    ):
        with pytest.raises(IpcOperationError) as error:
            dispatcher("koreader.readings.events", payload)
        assert error.value.code == "invalid_payload"


def test_reading_mutations_require_explicit_confirmation_and_exact_payloads(tmp_path) -> None:
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    class Services:
        def confirm_koreader_reading_history(self, *args, **kwargs):
            calls.append(("confirm", args, kwargs))
            return ReadingImportBatch(
                import_id=args[0],
                source="koreader",
                source_digest=args[1],
                status="applied",
                created_at="2026-10-25T00:00:00Z",
                completed_at="2026-10-25T00:00:00Z",
                record_count=4,
                reason_code=None,
            )

        def map_koreader_reading_book(self, *args, **kwargs):
            calls.append(("map", args, kwargs))

        def apply_calibre_reading_projection(self, *args, **kwargs):
            calls.append(("apply", args, kwargs))
            return ApplyResult("e" * 64, 1, 2)

    config = AppConfig(
        calibre_library=tmp_path / "library",
        restic_repository=tmp_path / "restic",
        state_root=tmp_path / "state",
    )
    dispatcher = build_dispatcher(
        config_loader=lambda: config,
        services_factory=lambda _config: Services(),  # type: ignore[arg-type]
    )
    import_id = "a" * 64

    result = dispatcher(
        "koreader.readings.confirm",
        {"import_id": import_id, "source_digest": "b" * 64, "confirmed": True},
    )
    assert result["import_id"] == import_id
    assert result["status"] == "applied"
    assert calls[0] == ("confirm", (import_id, "b" * 64), {"confirmed": True})

    dispatcher(
        "koreader.readings.map",
        {
            "import_id": import_id,
            "koreader_book_id": 42,
            "calibre_book_id": 7,
            "confirmed": True,
        },
    )
    assert calls[1] == ("map", (import_id, 42, 7), {"confirmed": True})

    applied = dispatcher(
        "calibre.readings.apply",
        {"import_id": import_id, "projection_id": "c" * 64, "confirmed": True},
    )
    assert applied == {
        "snapshot_id": "e" * 64,
        "applied_book_count": 1,
        "applied_field_count": 2,
    }
    assert calls[2][0] == "apply"
    assert calls[2][2] == {"confirmed": True}

    for operation, payload in (
        ("koreader.readings.confirm", {"import_id": import_id, "source_digest": "b" * 64}),
        (
            "koreader.readings.map",
            {"import_id": import_id, "koreader_book_id": 42, "calibre_book_id": 7},
        ),
        ("calibre.readings.apply", {"import_id": import_id, "projection_id": "c" * 64}),
    ):
        with pytest.raises(IpcOperationError) as error:
            dispatcher(operation, payload)
        assert error.value.code == "invalid_payload"


def test_hardcover_outbox_reconciliation_requires_explicit_confirmation(tmp_path: Path) -> None:
    config = AppConfig(
        calibre_library=tmp_path / "library",
        restic_repository=tmp_path / "restic",
        state_root=tmp_path / "state",
    )

    class Services:
        hardcover_ready = True

        def reconcile_hardcover_outbox(self, *, confirmed: bool) -> dict[str, int]:
            assert confirmed is True
            return {
                "attempted": 1,
                "completed": 1,
                "failed": 0,
                "reconciliation_required": 0,
                "safe_to_retry": 0,
                "manual_review_required": 0,
            }

    dispatcher = build_dispatcher(
        config_loader=lambda: config,
        services_factory=lambda _config: Services(),  # type: ignore[arg-type]
    )

    assert dispatcher("hardcover.reconcile", {"confirmed": True}) == {
        "attempted": 1,
        "completed": 1,
        "failed": 0,
        "reconciliation_required": 0,
        "safe_to_retry": 0,
        "manual_review_required": 0,
    }
    for payload in ({}, {"confirmed": False}, {"confirmed": True, "extra": 1}):
        with pytest.raises(IpcOperationError) as error:
            dispatcher("hardcover.reconcile", payload)
        assert error.value.code == "invalid_payload"


def test_hardcover_outbox_review_is_local_and_payloads_are_strict(tmp_path: Path) -> None:
    config = AppConfig(
        calibre_library=tmp_path / "library",
        restic_repository=tmp_path / "restic",
        state_root=tmp_path / "state",
    )
    operation_id = "12345678-1234-1234-1234-123456789abc"

    class Services:
        hardcover_ready = False

        def list_hardcover_manual_reviews(self) -> list[dict[str, object]]:
            return [{"operation_id": operation_id, "operation": "add_journal_entry"}]

        def resolve_hardcover_manual_review(
            self, operation_id: str, *, resolution: str, confirmed: bool
        ) -> dict[str, object]:
            assert confirmed is True
            return {"operation_id": operation_id, "resolution": resolution}

    dispatcher = build_dispatcher(
        config_loader=lambda: config,
        services_factory=lambda _config: Services(),  # type: ignore[arg-type]
    )

    assert dispatcher("hardcover.outbox.list", {}) == [
        {"operation_id": operation_id, "operation": "add_journal_entry"}
    ]
    assert dispatcher(
        "hardcover.outbox.resolve",
        {
            "operation_id": operation_id,
            "resolution": "confirmed_remote",
            "confirmed": True,
        },
    ) == {"operation_id": operation_id, "resolution": "confirmed_remote"}
    for operation, payload in (
        ("hardcover.outbox.list", {"unexpected": True}),
        (
            "hardcover.outbox.resolve",
            {"operation_id": operation_id, "resolution": "confirmed_remote"},
        ),
        (
            "hardcover.outbox.resolve",
            {"operation_id": "not-an-id", "resolution": "confirmed_remote", "confirmed": True},
        ),
        (
            "hardcover.outbox.resolve",
            {"operation_id": operation_id, "resolution": "delete", "confirmed": True},
        ),
        (
            "hardcover.outbox.resolve",
            {"operation_id": operation_id, "resolution": "closed_locally", "confirmed": False},
        ),
    ):
        with pytest.raises(IpcOperationError) as error:
            dispatcher(operation, payload)
        assert error.value.code == "invalid_payload"


def test_platform_csv_ipc_previews_and_pages_without_review_text_or_paths(tmp_path: Path) -> None:
    import_id = "a" * 64
    source_digest = "b" * 64
    batch = PlatformCsvImportBatch(
        import_id=import_id,
        source="goodreads",
        source_digest=source_digest,
        status="preview",
        created_at="2026-10-26T00:00:00Z",
        completed_at=None,
        record_count=1,
        reason_code=None,
    )
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    class Services:
        def preview_platform_csv(
            self, path: Path, *, source_name: str, column_mapping, expected_digest
        ):
            calls.append(
                (
                    "preview",
                    (path,),
                    {
                        "source_name": source_name,
                        "column_mapping": column_mapping,
                        "expected_digest": expected_digest,
                    },
                )
            )
            return PlatformCsvImportPreview(batch, 1, 1, {"invalid_date": 1})

        def get_platform_csv_preview_page(self, requested_id, *, offset, limit):
            calls.append(("page", (requested_id,), {"offset": offset, "limit": limit}))
            return StoredCsvEntryPage(
                (
                    StoredCsvEntry(
                        row_number=2,
                        row_type="record",
                        record={
                            "row_number": 2,
                            "title": "T" * 600,
                            "authors": ["A" * 300 for _ in range(12)],
                            "review": "private review text",
                            "source_fields": {"My Review": "private review text"},
                            "warnings": ["invalid_date"],
                        },
                        reason_code=None,
                    ),
                    StoredCsvEntry(
                        row_number=3,
                        row_type="issue",
                        record=None,
                        reason_code="missing_title_or_author",
                    ),
                ),
                False,
            )

    config = AppConfig(
        calibre_library=tmp_path / "library",
        restic_repository=tmp_path / "restic",
        state_root=tmp_path / "state",
    )
    dispatcher = build_dispatcher(
        config_loader=lambda: config,
        services_factory=lambda _config: Services(),  # type: ignore[arg-type]
    )
    selected_path = Path("C:/Users/example/private/reading.csv")

    result = dispatcher(
        "platform-csv.preview-mapped",
        {
            "path": str(selected_path),
            "source": "goodreads",
            "source_digest": source_digest,
            "column_mapping": {
                "external_id": None,
                "title": "Title",
                "author": "Author",
                "isbn_10": None,
                "isbn_13": None,
                "rating": None,
                "status": None,
                "shelves": None,
                "read_count": None,
                "date_added": None,
                "date_read": None,
                "review": None,
            },
        },
    )
    page = dispatcher(
        "platform-csv.page",
        {"import_id": import_id, "offset": 0, "limit": 100},
    )

    assert result == {
        "batch": {
            "import_id": import_id,
            "source": "goodreads",
            "source_digest": source_digest,
            "status": "preview",
            "created_at": "2026-10-26T00:00:00Z",
            "completed_at": None,
            "record_count": 1,
            "reason_code": None,
        },
        "record_count": 1,
        "issue_count": 1,
        "warning_counts": {"invalid_date": 1},
    }
    serialized = json.dumps(page)
    assert "private review text" not in serialized
    assert "source_fields" not in serialized
    assert str(selected_path) not in json.dumps(result)
    assert len(page["entries"][0]["record"]["title"]) == 512
    assert len(page["entries"][0]["record"]["authors"]) == 10
    assert all(len(author) == 256 for author in page["entries"][0]["record"]["authors"])
    assert "preview_truncated" in page["entries"][0]["record"]["warnings"]
    assert page["entries"][1]["reason_code"] == "missing_title_or_author"
    assert calls == [
        (
            "preview",
            (selected_path,),
            {
                "source_name": "goodreads",
                "column_mapping": {
                    "external_id": None,
                    "title": "Title",
                    "author": "Author",
                    "isbn_10": None,
                    "isbn_13": None,
                    "rating": None,
                    "status": None,
                    "shelves": None,
                    "read_count": None,
                    "date_added": None,
                    "date_read": None,
                    "review": None,
                },
                "expected_digest": source_digest,
            },
        ),
        ("page", (import_id,), {"offset": 0, "limit": 100}),
    ]


def test_platform_csv_ipc_inspects_headers_and_previews_only_after_mapping(tmp_path: Path) -> None:
    selected_path = Path("C:/Users/example/private/reading.csv")
    source_digest = "e" * 64
    import_id = "f" * 64
    batch = PlatformCsvImportBatch(
        import_id=import_id,
        source="storygraph",
        source_digest=source_digest,
        status="preview",
        created_at="2026-10-26T00:00:00Z",
        completed_at=None,
        record_count=1,
        reason_code=None,
    )
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    class Services:
        def inspect_platform_csv(self, path: Path, *, source_name: str):
            calls.append(("inspect", (path,), {"source_name": source_name}))
            return {
                "source": source_name,
                "source_digest": source_digest,
                "headers": ["Book name", "Creator", "Private note"],
                "suggested_mapping": {
                    "title": "Book name",
                    "author": "Creator",
                    "external_id": None,
                    "isbn_10": None,
                    "isbn_13": None,
                    "rating": None,
                    "status": None,
                    "shelves": None,
                    "read_count": None,
                    "date_added": None,
                    "date_read": None,
                    "review": None,
                },
            }

        def preview_platform_csv(
            self, path: Path, *, source_name: str, column_mapping, expected_digest
        ):
            calls.append(
                (
                    "mapped_preview",
                    (path,),
                    {
                        "source_name": source_name,
                        "column_mapping": column_mapping,
                        "expected_digest": expected_digest,
                    },
                )
            )
            return PlatformCsvImportPreview(batch, 1, 0, {})

    config = AppConfig(
        calibre_library=tmp_path / "library",
        restic_repository=tmp_path / "restic",
        state_root=tmp_path / "state",
    )
    dispatcher = build_dispatcher(
        config_loader=lambda: config,
        services_factory=lambda _config: Services(),  # type: ignore[arg-type]
    )
    mapping = {
        "external_id": None,
        "title": "Book name",
        "author": "Creator",
        "isbn_10": None,
        "isbn_13": None,
        "rating": None,
        "status": None,
        "shelves": None,
        "read_count": None,
        "date_added": None,
        "date_read": None,
        "review": None,
    }
    draft = dispatcher(
        "platform-csv.inspect",
        {"path": str(selected_path), "source": "storygraph"},
    )
    preview = dispatcher(
        "platform-csv.preview-mapped",
        {
            "path": str(selected_path),
            "source": "storygraph",
            "source_digest": source_digest,
            "column_mapping": mapping,
        },
    )

    assert draft["headers"] == ["Book name", "Creator", "Private note"]
    assert "Private note" in draft["headers"]
    assert "Private note" not in json.dumps(draft["suggested_mapping"])
    assert str(selected_path) not in json.dumps(draft)
    assert preview["batch"]["import_id"] == import_id
    assert calls == [
        ("inspect", (selected_path,), {"source_name": "storygraph"}),
        (
            "mapped_preview",
            (selected_path,),
            {
                "source_name": "storygraph",
                "column_mapping": mapping,
                "expected_digest": source_digest,
            },
        ),
    ]

    for invalid_payload in (
        {
            "path": str(selected_path),
            "source": "storygraph",
            "source_digest": source_digest,
            "column_mapping": {"title": "Book name"},
        },
        {
            "path": str(selected_path),
            "source": "storygraph",
            "source_digest": source_digest,
            "column_mapping": {"title": "Book name", "author": "Book name"},
        },
        {
            "path": str(selected_path),
            "source": "storygraph",
            "source_digest": source_digest,
            "column_mapping": {"review": "Private note", "title": "Book name", "author": "Creator"},
        },
    ):
        with pytest.raises(IpcOperationError) as error:
            dispatcher("platform-csv.preview-mapped", invalid_payload)
        assert error.value.code == "invalid_payload"


def test_platform_csv_ipc_requires_explicit_confirmation_and_bounded_pages(tmp_path: Path) -> None:
    import_id = "c" * 64
    source_digest = "d" * 64
    batch = PlatformCsvImportBatch(
        import_id=import_id,
        source="storygraph",
        source_digest=source_digest,
        status="applied",
        created_at="2026-10-26T00:00:00Z",
        completed_at="2026-10-26T00:00:01Z",
        record_count=0,
        reason_code=None,
    )
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    class Services:
        def list_platform_csv_imports(self, *, offset, limit, source_name=None):
            calls.append(
                ("list", (), {"offset": offset, "limit": limit, "source_name": source_name})
            )
            return PlatformCsvImportPage((batch,), False)

        def confirm_platform_csv_import(self, *args, **kwargs):
            calls.append(("confirm", args, kwargs))
            return batch

        def export_platform_csv(self, *args, **kwargs):
            calls.append(("export", args, kwargs))
            return {"file_name": "reading.csv", "rows_exported": 10}

    config = AppConfig(
        calibre_library=tmp_path / "library",
        restic_repository=tmp_path / "restic",
        state_root=tmp_path / "state",
    )
    dispatcher = build_dispatcher(
        config_loader=lambda: config,
        services_factory=lambda _config: Services(),  # type: ignore[arg-type]
    )

    listed = dispatcher("platform-csv.list", {"offset": 0, "limit": 10})
    assert listed["batches"][0]["import_id"] == import_id
    assert listed["has_more"] is False
    with pytest.raises(IpcOperationError) as missing_confirmation:
        dispatcher(
            "platform-csv.confirm",
            {"import_id": import_id, "source_digest": source_digest, "confirmed": False},
        )
    assert missing_confirmation.value.code == "invalid_payload"
    applied = dispatcher(
        "platform-csv.confirm",
        {"import_id": import_id, "source_digest": source_digest, "confirmed": True},
    )
    assert applied["status"] == "applied"
    output = dispatcher(
        "platform-csv.export",
        {
            "import_id": import_id,
            "path": "C:/Users/example/export.csv",
            "include_reviews": False,
            "confirmed": True,
        },
    )
    assert output == {"file_name": "reading.csv", "rows_exported": 10}
    assert calls[0] == ("list", (), {"offset": 0, "limit": 10, "source_name": None})
    assert calls[1] == (
        "confirm",
        (import_id, source_digest),
        {"confirmed": True},
    )
    assert calls[2] == (
        "export",
        (import_id, Path("C:/Users/example/export.csv")),
        {"include_reviews": False, "confirmed": True},
    )

    for operation, payload in (
        ("platform-csv.page", {"import_id": import_id, "limit": 101}),
        ("platform-csv.list", {"source": "unknown"}),
        (
            "platform-csv.preview-mapped",
            {
                "path": "relative.csv",
                "source": "goodreads",
                "source_digest": source_digest,
                "column_mapping": {},
            },
        ),
        (
            "platform-csv.export",
            {
                "import_id": import_id,
                "path": "relative.csv",
                "include_reviews": True,
                "confirmed": True,
            },
        ),
    ):
        with pytest.raises(IpcOperationError) as error:
            dispatcher(operation, payload)
        assert error.value.code == "invalid_payload"
