from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from koreader_ecosystem.application_state import ApplicationState
from koreader_ecosystem.platform_csv import parse_reading_csv
from koreader_ecosystem.platform_csv_state import (
    PlatformCsvStore,
    PlatformCsvStoreError,
)

CSV = (
    b"Book Id,Title,Author,ISBN,ISBN13,My Rating,Exclusive Shelf,Bookshelves,Read Count,"
    b"Date Added,Date Read,My Review\n"
    b'42,"A Synthetic Book","Example Author",0345339681,,4.5,read,"fiction, favorite",'
    b'2,2026/10/01,2026/10/10,"Private fixture review"\n'
    b'43,"Another Book","Second Author",,,0,to-read,,0,,,\n'
    b'44,,"Missing Title",,,,,,,,,\n'
)


def _preview(content: bytes = CSV, *, include_review: bool = False):
    mapping = None
    if include_review:
        mapping = {
            "external_id": "Book Id",
            "title": "Title",
            "author": "Author",
            "isbn_10": "ISBN",
            "isbn_13": "ISBN13",
            "rating": "My Rating",
            "status": "Exclusive Shelf",
            "shelves": "Bookshelves",
            "read_count": "Read Count",
            "date_added": "Date Added",
            "date_read": "Date Read",
            "review": "My Review",
        }
    return parse_reading_csv(content, source="goodreads", column_mapping=mapping)


def _store(tmp_path: Path) -> PlatformCsvStore:
    return PlatformCsvStore(ApplicationState(tmp_path / "application-state.sqlite3"))


def test_preview_is_stored_inactive_and_pages_records_and_issues(tmp_path: Path) -> None:
    store = _store(tmp_path)
    preview = _preview(include_review=True)

    batch = store.stage_preview(preview)

    assert batch.source == "goodreads"
    assert batch.source_digest == preview.source_digest
    assert batch.status == "preview"
    assert batch.record_count == 2
    page = store.get_preview_page(batch.import_id, offset=0, limit=2)
    assert [entry.row_number for entry in page.entries] == [2, 3]
    assert [entry.row_type for entry in page.entries] == ["record", "record"]
    assert page.has_more is True
    assert page.entries[0].record is not None
    assert page.entries[0].record["review"] == "Private fixture review"
    assert store.get_applied_records(batch.import_id, offset=0, limit=100).records == ()

    issues = store.get_preview_page(batch.import_id, offset=2, limit=2)
    assert [(entry.row_number, entry.reason_code) for entry in issues.entries] == [
        (4, "missing_title_or_author")
    ]
    assert issues.has_more is False


def test_confirmation_requires_matching_digest_and_explicit_choice(tmp_path: Path) -> None:
    store = _store(tmp_path)
    batch = store.stage_preview(_preview())

    declined = store.confirm_import(batch.import_id, batch.source_digest, confirmed=False)
    assert declined.status == "preview"
    with pytest.raises(PlatformCsvStoreError) as mismatch:
        store.confirm_import(batch.import_id, "a" * 64, confirmed=True)
    assert mismatch.value.reason_code == "source_digest_mismatch"
    assert store.get_import(batch.import_id).status == "preview"

    applied = store.confirm_import(batch.import_id, batch.source_digest, confirmed=True)

    assert applied.status == "applied"
    assert applied.completed_at is not None
    page = store.get_applied_records(batch.import_id, offset=0, limit=1)
    assert len(page.records) == 1
    assert page.records[0]["title"] == "A Synthetic Book"
    assert page.has_more is True
    with pytest.raises(PlatformCsvStoreError) as inactive:
        store.get_preview_page("missing-import", offset=0, limit=10)
    assert inactive.value.reason_code == "import_not_found"


def test_identical_import_is_idempotent_and_failed_import_can_be_repreviewed(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = store.stage_preview(_preview())
    repeated = store.stage_preview(_preview())

    assert repeated.import_id == first.import_id
    assert repeated.created_at == first.created_at
    failed = store.fail_import(first.import_id, "user_cancelled")
    assert failed.status == "failed"
    retried = store.stage_preview(_preview())
    assert retried.import_id == first.import_id
    assert retried.status == "preview"
    assert retried.completed_at is None
    assert retried.reason_code is None
    with sqlite3.connect(store.state.database) as connection:
        assert connection.execute("SELECT count(*) FROM import_batches").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM csv_import_rows").fetchone() == (3,)


def test_same_csv_with_different_mapping_is_rejected_without_replacing_saved_rows(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = store.stage_preview(_preview())
    changed_mapping = {
        "external_id": "Book Id",
        "title": "Title",
        "author": "Author",
        "isbn_10": "ISBN",
        "isbn_13": "ISBN13",
        "rating": "My Rating",
        "status": "Exclusive Shelf",
        "shelves": "Bookshelves",
        "read_count": "Read Count",
        "date_added": "Date Added",
        "date_read": "Date Read",
        "review": "My Review",
    }
    changed_preview = parse_reading_csv(CSV, source="goodreads", column_mapping=changed_mapping)

    with pytest.raises(PlatformCsvStoreError) as conflict:
        store.stage_preview(changed_preview)

    assert conflict.value.reason_code == "mapping_conflict"
    page = store.get_preview_page(first.import_id, offset=0, limit=10)
    assert page.entries[0].record is not None
    assert page.entries[0].record["review"] is None
    with sqlite3.connect(store.state.database) as connection:
        assert connection.execute("SELECT count(*) FROM import_batches").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM csv_import_rows").fetchone() == (3,)


def test_changed_source_content_creates_a_distinct_import(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.stage_preview(_preview())
    second = store.stage_preview(_preview(CSV.replace(b"A Synthetic Book", b"Changed Book")))

    assert second.import_id != first.import_id
    with sqlite3.connect(store.state.database) as connection:
        assert connection.execute("SELECT count(*) FROM import_batches").fetchone() == (2,)


def test_pagination_is_bounded_and_validated(tmp_path: Path) -> None:
    store = _store(tmp_path)
    batch = store.stage_preview(_preview())

    with pytest.raises(PlatformCsvStoreError) as too_many:
        store.get_preview_page(batch.import_id, offset=0, limit=101)
    assert too_many.value.reason_code == "invalid_pagination"
    with pytest.raises(PlatformCsvStoreError) as negative:
        store.get_preview_page(batch.import_id, offset=-1, limit=10)
    assert negative.value.reason_code == "invalid_pagination"
    with pytest.raises(PlatformCsvStoreError) as boolean:
        store.get_applied_records(batch.import_id, offset=0, limit=True)
    assert boolean.value.reason_code == "invalid_pagination"


def test_stage_preview_rolls_back_batch_and_rows_on_persistence_failure(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with store.state.connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_csv_rows BEFORE INSERT ON csv_import_rows
            BEGIN SELECT RAISE(ABORT, 'fixture'); END
            """
        )

    with pytest.raises(PlatformCsvStoreError) as error:
        store.stage_preview(_preview())

    assert error.value.reason_code == "persistence_failed"
    with sqlite3.connect(store.state.database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM import_batches WHERE source='goodreads'"
        ).fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM csv_import_rows").fetchone() == (0,)


def test_import_state_rejects_invalid_digest_and_reason_codes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    preview = _preview()
    invalid = type(preview)(
        source=preview.source,
        source_digest="not-a-digest",
        mapping_digest=preview.mapping_digest,
        records=preview.records,
        issues=preview.issues,
    )
    with pytest.raises(PlatformCsvStoreError) as invalid_preview:
        store.stage_preview(invalid)
    assert invalid_preview.value.reason_code == "invalid_preview"

    batch = store.stage_preview(preview)
    with pytest.raises(PlatformCsvStoreError) as invalid_reason:
        store.fail_import(batch.import_id, "private reason text")
    assert invalid_reason.value.reason_code == "invalid_reason_code"


def test_lists_only_csv_imports_with_bounded_pages(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.stage_preview(_preview())
    second = store.stage_preview(_preview(CSV.replace(b"A Synthetic Book", b"Changed Book")))

    page = store.list_imports(offset=0, limit=1)
    next_page = store.list_imports(offset=1, limit=1)

    assert len(page.batches) == 1
    assert page.has_more is True
    assert next_page.has_more is False
    assert {page.batches[0].import_id, next_page.batches[0].import_id} == {
        first.import_id,
        second.import_id,
    }


def test_lists_csv_batches_without_exposing_review_content(tmp_path: Path) -> None:
    store = _store(tmp_path)
    batch = store.stage_preview(_preview())

    page = store.list_imports(offset=0, limit=100, source="goodreads")

    assert [item.import_id for item in page.batches] == [batch.import_id]
    assert not hasattr(page.batches[0], "records")
    assert not hasattr(page.batches[0], "review")
