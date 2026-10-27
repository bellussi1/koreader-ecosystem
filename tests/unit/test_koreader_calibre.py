from __future__ import annotations

import json
from pathlib import Path

import pytest

from koreader_ecosystem.calibre_catalog import CalibreBook
from koreader_ecosystem.koreader_calibre import (
    KoreaderCalibreError,
    assert_preview_sources_unchanged,
    create_preview,
)

CALIBRE_UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def _catalog(*, progress: object = None, annotations: object = None) -> tuple[CalibreBook, ...]:
    return (
        CalibreBook(
            book_id=7,
            uuid=CALIBRE_UUID,
            title="Livro de teste",
            authors=("Autora",),
            identifiers={"isbn": "9780000000000"},
            custom_values={
                "koreader_progress": progress,
                "koreader_status": None,
                "koreader_position": None,
                "koreader_rating": None,
                "koreader_note": None,
                "koreader_annotations": annotations,
            },
        ),
    )


def _write_sidecar(stage_root: Path, contents: str) -> Path:
    directory = stage_root / "Books" / "Example.sdr"
    directory.mkdir(parents=True)
    sidecar = directory / "metadata.epub.lua"
    sidecar.write_text(contents, encoding="utf-8")
    return sidecar


def _valid_sidecar(*, progress: str = "0.75", status: str = '"reading"') -> str:
    return f"""-- Example KOReader metadata\nreturn {{
        ["stats"] = {{ ["identifiers"] = "calibre:{CALIBRE_UUID}" }},
        ["percent_finished"] = {progress},
        ["summary"] = {{
            ["status"] = {status}, ["rating"] = 4, ["note"] = "Minha nota",
        }},
        ["last_xpointer"] = "/body/p[4]",
        ["annotations"] = {{
            [1] = {{ ["pos0"] = "p4a", ["pos1"] = "p4b",
                     ["text"] = "Trecho", ["datetime"] = "2026-10-23" }},
            [2] = {{ ["pos0"] = "p4a", ["pos1"] = "p4b",
                     ["text"] = "Trecho", ["datetime"] = "2026-10-24" }},
        }},
    }}"""


def test_preview_extracts_fields_and_deduplicates_annotations(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    _write_sidecar(stage, _valid_sidecar())

    preview = create_preview(stage, _catalog(), tmp_path / "state.sqlite3")

    assert len(preview.changes) == 1
    change = preview.changes[0]
    assert change.book_id == 7
    assert change.calibre_uuid == CALIBRE_UUID
    assert change.new_values["koreader_progress"] == 0.75
    assert change.new_values["koreader_status"] == "reading"
    assert change.new_values["koreader_position"] == "/body/p[4]"
    assert change.new_values["koreader_rating"] == 4.0
    assert change.new_values["koreader_note"] == "Minha nota"
    annotations = json.loads(str(change.new_values["koreader_annotations"]))
    assert len(annotations) == 1
    assert annotations[0]["annotation"]["text"] == "Trecho"
    assert len(annotations[0]["id"]) == 64


def test_unknown_status_and_invalid_lua_do_not_create_changes(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    _write_sidecar(stage, _valid_sidecar(status='"future_status"'))
    invalid = stage / "Books" / "Broken.sdr"
    invalid.mkdir()
    (invalid / "metadata.epub.lua").write_text(
        'return { ["stats"] = os.execute("whoami") }', encoding="utf-8"
    )

    preview = create_preview(stage, _catalog(), tmp_path / "state.sqlite3")

    assert preview.changes == ()
    reasons = {item.reason_code for item in preview.items}
    assert reasons == {"unknown_status", "invalid_lua"}


def test_identical_previews_have_a_stable_digest_and_resume_state(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    _write_sidecar(stage, _valid_sidecar())
    database = tmp_path / "state.sqlite3"

    first = create_preview(stage, _catalog(), database)
    second = create_preview(stage, _catalog(), database)

    assert first.preview_id == second.preview_id
    assert first.plan_digest == second.plan_digest
    assert first.changes == second.changes


def test_changed_sidecar_invalidates_the_saved_preview(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    sidecar = _write_sidecar(stage, _valid_sidecar())
    database = tmp_path / "state.sqlite3"
    preview = create_preview(stage, _catalog(), database)
    sidecar.write_text(_valid_sidecar(progress="0.80"), encoding="utf-8")

    with pytest.raises(KoreaderCalibreError) as error:
        assert_preview_sources_unchanged(stage, database, preview.preview_id)

    assert error.value.reason_code == "sidecar_changed"


def test_progress_regression_is_reported_and_not_proposed(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    _write_sidecar(stage, _valid_sidecar(progress="0.50"))

    preview = create_preview(stage, _catalog(progress=0.75), tmp_path / "state.sqlite3")

    assert preview.changes == ()
    assert "progress_regression" in preview.items[0].warnings


def test_complete_status_with_incomplete_progress_is_held_for_review(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    _write_sidecar(stage, _valid_sidecar(progress="0.127", status='"complete"'))

    preview = create_preview(stage, _catalog(), tmp_path / "state.sqlite3")

    assert preview.changes == ()
    assert preview.items[0].reason_code == "status_progress_conflict"
    assert "status_progress_conflict" in preview.items[0].warnings
