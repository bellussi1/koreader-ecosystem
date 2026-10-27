from __future__ import annotations

import json
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

import pytest

from koreader_ecosystem.calibre_catalog import (
    load_calibre_catalog,
    load_calibre_custom_columns,
)
from koreader_ecosystem.calibre_library import (
    edit_calibre_metadata,
    edit_calibre_tags_batch,
    remove_calibre_book,
)
from koreader_ecosystem.config import AppConfig
from koreader_ecosystem.koreader_calibre import create_preview
from koreader_ecosystem.restic import ResticResult


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("calibredb") is None, reason="Calibre CLI is unavailable")
def test_real_calibredb_round_trips_unicode_multiline_custom_values() -> None:
    executable = shutil.which("calibredb")
    assert executable is not None
    with tempfile.TemporaryDirectory(prefix="c") as temporary_root:
        library = Path(temporary_root) / "L"
        library.mkdir()

        added = subprocess.run(
            [
                executable,
                "--with-library",
                str(library),
                "add",
                "--empty",
                "--title",
                "Synthetic Fixture",
                "--authors",
                "Test Author",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert added.returncode == 0

        definitions = {
            "koreader_progress": ("KOReader Progress", "float"),
            "koreader_status": ("KOReader Status", "text"),
            "koreader_position": ("KOReader Position", "text"),
            "koreader_rating": ("KOReader Rating", "float"),
            "koreader_note": ("KOReader Note", "text"),
            "koreader_annotations": ("KOReader Annotations", "text"),
        }
        for label, (name, datatype) in definitions.items():
            created = subprocess.run(
                [
                    executable,
                    "--with-library",
                    str(library),
                    "add_custom_column",
                    label,
                    name,
                    datatype,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            assert created.returncode == 0

        columns = load_calibre_custom_columns(library)
        assert columns == {label: datatype for label, (_, datatype) in definitions.items()}
        (book,) = load_calibre_catalog(library)
        note = 'Linha 1\n"ação <script>"'
        annotations = json.dumps(
            [{"id": "a" * 64, "annotation": {"text": "oração <trecho>"}}],
            ensure_ascii=False,
        )
        for label, value in {
            "koreader_progress": "0.75",
            "koreader_status": "reading",
            "koreader_position": "/body/p[4]",
            "koreader_rating": "4.5",
            "koreader_note": note,
            "koreader_annotations": annotations,
        }.items():
            updated = subprocess.run(
                [
                    executable,
                    "--with-library",
                    str(library),
                    "set_custom",
                    label,
                    str(book.book_id),
                    value,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            assert updated.returncode == 0

        (verified,) = load_calibre_catalog(library)
        assert verified.custom_values["koreader_progress"] == 0.75
        assert verified.custom_values["koreader_status"] == "reading"
        assert verified.custom_values["koreader_position"] == "/body/p[4]"
        assert verified.custom_values["koreader_rating"] == 4.5
        assert verified.custom_values["koreader_note"] == note
        assert verified.custom_values["koreader_annotations"] == annotations


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("calibredb") is None, reason="Calibre CLI is unavailable")
def test_real_calibredb_removal_uses_trash_and_reloads_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise real Calibre deletion only against a disposable synthetic library."""
    executable = shutil.which("calibredb")
    assert executable is not None
    with tempfile.TemporaryDirectory(prefix="kre-calibre-remove-") as temporary_root:
        library = Path(temporary_root) / "library"
        library.mkdir()
        added = subprocess.run(
            [
                executable,
                "--with-library",
                str(library),
                "add",
                "--empty",
                "--title",
                "Synthetic Removal Fixture",
                "--authors",
                "Test Author",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert added.returncode == 0, added.stderr
        (book,) = load_calibre_catalog(library)

        # The guard is global to this desktop session. This isolated throwaway library is
        # independent of the user's Calibre database, even if their GUI is open.
        monkeypatch.setattr(
            "koreader_ecosystem.calibre_library._ensure_calibre_closed", lambda: None
        )
        remove_calibre_book(library, book.book_id)

        assert load_calibre_catalog(library) == ()
        trash_metadata = library / ".caltrash" / "b" / str(book.book_id) / "metadata.opf"
        assert trash_metadata.is_file()


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("calibredb") is None, reason="Calibre CLI is unavailable")
def test_real_calibredb_edits_cover_custom_fields_and_batch_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = shutil.which("calibredb")
    assert executable is not None
    with tempfile.TemporaryDirectory(prefix="kre-calibre-edit-") as temporary_root:
        root = Path(temporary_root)
        library = root / "library"
        library.mkdir()

        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [executable, "--with-library", str(library), *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )

        for title in ("Synthetic Cover Fixture", "Synthetic Batch Fixture"):
            added = run("add", "--empty", "--title", title, "--authors", "Test Author")
            assert added.returncode == 0, added.stderr
        enum_display = json.dumps({"enum_values": ["New", "Reading", "Read"]})
        for args in (
            (
                "add_custom_column",
                "reading_state",
                "Reading State",
                "enumeration",
                "--display",
                enum_display,
            ),
            ("add_custom_column", "private_note", "Private Note", "comments"),
            ("add_custom_column", "reading_enabled", "Reading Enabled", "bool"),
            ("add_custom_column", "shelf_count", "Shelf Count", "int"),
        ):
            created = run(*args)
            assert created.returncode == 0, created.stderr
        books = load_calibre_catalog(library)
        cover_book, batch_book = books

        def png_chunk(name: bytes, data: bytes) -> bytes:
            content = name + data
            return struct.pack(">I", len(data)) + content + struct.pack(">I", zlib.crc32(content))

        cover_file = root / "cover.png"
        cover_file.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + png_chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff"))
            + png_chunk(b"IEND", b"")
        )

        monkeypatch.setattr(
            "koreader_ecosystem.calibre_library._ensure_calibre_closed", lambda: None
        )
        edited = edit_calibre_metadata(
            library,
            cover_book.book_id,
            {
                "custom_values": {
                    "reading_state": "Reading",
                    "private_note": "Revisar depois",
                    "reading_enabled": True,
                    "shelf_count": 12,
                }
            },
            cover_path=cover_file,
        )
        tagged = edit_calibre_tags_batch(
            library,
            (cover_book.book_id, batch_book.book_id),
            add_tags=("Fantasia", "Para revisar"),
            remove_tags=(),
        )

        assert edited.cover_path is not None and Path(edited.cover_path).is_file()
        assert edited.custom_values["reading_state"] == "Reading"
        assert edited.custom_values["private_note"] == "Revisar depois"
        assert edited.custom_values["reading_enabled"] is True
        assert edited.custom_values["shelf_count"] == 12
        assert [book.book_id for book in tagged] == [cover_book.book_id, batch_book.book_id]
        assert tagged[0].tags == ("Fantasia", "Para revisar")
        assert tagged[1].tags == ("Fantasia", "Para revisar")
        assert load_calibre_catalog(library) == tagged


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("calibredb") is None, reason="Calibre CLI is unavailable")
def test_full_apply_pipeline_uses_snapshot_and_verifies_real_calibre(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from koreader_ecosystem.calibre_apply import apply_preview

    executable = shutil.which("calibredb")
    assert executable is not None
    with tempfile.TemporaryDirectory(prefix="c") as temporary_root:
        root = Path(temporary_root)
        library = root / "L"
        library.mkdir()
        added = subprocess.run(
            [
                executable,
                "--with-library",
                str(library),
                "add",
                "--empty",
                "--title",
                "Synthetic Apply Fixture",
                "--authors",
                "Test Author",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert added.returncode == 0
        (book,) = load_calibre_catalog(library)

        stage = root / "S"
        sidecar = stage / "Books" / "Fixture.sdr" / "metadata.epub.lua"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(
            f"""return {{
                ["stats"] = {{ ["identifiers"] = "calibre:{book.uuid}" }},
                ["percent_finished"] = 0.75,
                ["last_xpointer"] = "/body/p[4]",
                ["summary"] = {{
                    ["status"] = "reading", ["rating"] = 4.5,
                    ["note"] = "Linha 1\\n\\"ação\\"",
                }},
                ["annotations"] = {{
                    [1] = {{ ["pos0"] = "p4a", ["pos1"] = "p4b",
                             ["text"] = "oração <script>alert(1)</script>" }},
                }},
            }}""",
            encoding="utf-8",
        )
        config = AppConfig(library, root / "R", root / "State")
        state_db = config.state_root / "koreader-calibre.sqlite3"
        preview = create_preview(stage, (book,), state_db)

        class LocalRestic:
            available = True

            def __init__(self) -> None:
                self.calls: list[str] = []

            def ensure_password(self) -> None:
                self.calls.append("password")

            def run(self, args, *, cwd=None) -> ResticResult:
                del cwd
                self.calls.append(args[0])
                if args[0] == "backup":
                    return ResticResult(returncode=0, summary={"snapshot_id": "c" * 64})
                return ResticResult(returncode=0)

        restic = LocalRestic()
        monkeypatch.setattr("koreader_ecosystem.calibre_apply._running_process_names", lambda: [])
        monkeypatch.setattr("koreader_ecosystem.backup._running_process_names", lambda: [])

        result = apply_preview(config, state_db, stage, preview.preview_id, restic)

        assert result.snapshot_id == "c" * 64
        assert result.applied_book_count == 1
        assert result.applied_field_count == 6
        assert restic.calls == ["password", "init", "backup", "check"]
        (verified,) = load_calibre_catalog(library)
        assert verified.custom_values["koreader_progress"] == 0.75
        assert verified.custom_values["koreader_status"] == "reading"
        assert verified.custom_values["koreader_position"] == "/body/p[4]"
        assert verified.custom_values["koreader_rating"] == 4.5
        assert verified.custom_values["koreader_note"] == 'Linha 1\n"ação"'
        assert "<script>" in str(verified.custom_values["koreader_annotations"])
