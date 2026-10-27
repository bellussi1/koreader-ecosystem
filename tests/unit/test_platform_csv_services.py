from __future__ import annotations

from pathlib import Path

import pytest

from koreader_ecosystem.config import AppConfig
from koreader_ecosystem.services import ApplicationServices, ServiceError


def _config(root: Path) -> AppConfig:
    return AppConfig(
        calibre_library=root / "library",
        restic_repository=root / "restic",
        state_root=root / "state",
    )


def _csv(path: Path) -> Path:
    path.write_bytes(
        b"Book Id,Title,Author,My Rating,Exclusive Shelf,Date Read,My Review\r\n"
        b'42,"A Test Book","A Test Author",4.5,read,2026/10/02,"Private test review"\r\n'
    )
    return path


def test_preview_persists_local_csv_rows_without_returning_review_text(tmp_path: Path) -> None:
    source = _csv(tmp_path / "reading.csv")
    service = ApplicationServices(_config(tmp_path))

    preview = service.preview_platform_csv(source, source_name="goodreads")
    page = service.get_platform_csv_preview_page(preview.batch.import_id, offset=0, limit=100)

    assert preview.batch.status == "preview"
    assert preview.batch.record_count == 1
    assert preview.issue_count == 0
    assert page.entries[0].record is not None
    assert page.entries[0].record["title"] == "A Test Book"
    assert "Private test review" not in repr(preview)


def test_csv_import_rejects_invalid_paths_sources_and_extensions(tmp_path: Path) -> None:
    service = ApplicationServices(_config(tmp_path))
    with pytest.raises(ServiceError) as relative:
        service.preview_platform_csv(Path("relative.csv"), source_name="goodreads")
    assert relative.value.reason_code == "platform_csv_file_unavailable"

    source = _csv(tmp_path / "reading.txt")
    with pytest.raises(ServiceError) as extension:
        service.preview_platform_csv(source, source_name="goodreads")
    assert extension.value.reason_code == "platform_csv_invalid_file"

    with pytest.raises(ServiceError) as provider:
        service.preview_platform_csv(source.with_suffix(".csv"), source_name="unsupported")
    assert provider.value.reason_code == "platform_csv_invalid_source"


def test_csv_import_rejects_a_different_mapping_for_an_existing_file(tmp_path: Path) -> None:
    source = _csv(tmp_path / "reading.csv")
    service = ApplicationServices(_config(tmp_path))
    first = service.preview_platform_csv(source, source_name="goodreads")

    with pytest.raises(ServiceError) as conflict:
        service.preview_platform_csv(
            source,
            source_name="goodreads",
            column_mapping={
                "external_id": "Book Id",
                "title": "Title",
                "author": "Author",
                "isbn_10": None,
                "isbn_13": None,
                "rating": "My Rating",
                "status": "Exclusive Shelf",
                "shelves": None,
                "read_count": None,
                "date_added": None,
                "date_read": "Date Read",
                "review": "My Review",
            },
        )

    assert conflict.value.reason_code == "platform_csv_mapping_conflict"
    page = service.get_platform_csv_preview_page(first.batch.import_id, offset=0, limit=10)
    assert page.entries[0].record is not None
    assert page.entries[0].record["review"] is None


def test_csv_export_requires_applied_import_and_writes_only_to_selected_csv_path(
    tmp_path: Path,
) -> None:
    source = _csv(tmp_path / "reading.csv")
    destination = tmp_path / "export.csv"
    service = ApplicationServices(_config(tmp_path))
    preview = service.preview_platform_csv(source, source_name="goodreads")

    with pytest.raises(ServiceError) as unconfirmed:
        service.export_platform_csv(
            preview.batch.import_id,
            destination,
            include_reviews=False,
            confirmed=True,
        )
    assert unconfirmed.value.reason_code == "platform_csv_import_not_applied"
    service.confirm_platform_csv_import(
        preview.batch.import_id,
        preview.batch.source_digest,
        confirmed=True,
    )

    result = service.export_platform_csv(
        preview.batch.import_id,
        destination,
        include_reviews=False,
        confirmed=True,
    )

    assert destination.is_file()
    assert result["file_name"] == "export.csv"
    assert result["rows_exported"] == 1
    assert result["reviews_included"] is False
    assert b"Private test review" not in destination.read_bytes()


def test_review_export_is_explicit_and_private_notes_are_never_written(tmp_path: Path) -> None:
    source = _csv(tmp_path / "reading.csv")
    service = ApplicationServices(_config(tmp_path))
    preview = service.preview_platform_csv(
        source,
        source_name="goodreads",
        column_mapping={
            "external_id": "Book Id",
            "title": "Title",
            "author": "Author",
            "isbn_10": None,
            "isbn_13": None,
            "rating": "My Rating",
            "status": "Exclusive Shelf",
            "shelves": None,
            "read_count": None,
            "date_added": None,
            "date_read": "Date Read",
            "review": "My Review",
        },
    )
    service.confirm_platform_csv_import(
        preview.batch.import_id,
        preview.batch.source_digest,
        confirmed=True,
    )

    result = service.export_platform_csv(
        preview.batch.import_id,
        tmp_path / "export.csv",
        include_reviews=True,
        confirmed=True,
    )

    assert result["reviews_included"] is True
    assert b"Private test review" in (tmp_path / "export.csv").read_bytes()


def test_csv_export_refuses_unconfirmed_path_and_existing_links(tmp_path: Path) -> None:
    source = _csv(tmp_path / "reading.csv")
    service = ApplicationServices(_config(tmp_path))
    preview = service.preview_platform_csv(source, source_name="goodreads")
    service.confirm_platform_csv_import(
        preview.batch.import_id,
        preview.batch.source_digest,
        confirmed=True,
    )

    with pytest.raises(ServiceError) as no_confirm:
        service.export_platform_csv(
            preview.batch.import_id,
            tmp_path / "export.csv",
            include_reviews=False,
            confirmed=False,
        )
    assert no_confirm.value.reason_code == "platform_csv_confirmation_required"
    with pytest.raises(ServiceError) as wrong_extension:
        service.export_platform_csv(
            preview.batch.import_id,
            tmp_path / "export.txt",
            include_reviews=False,
            confirmed=True,
        )
    assert wrong_extension.value.reason_code == "platform_csv_invalid_file"
