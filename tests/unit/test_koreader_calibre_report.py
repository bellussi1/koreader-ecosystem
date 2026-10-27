from __future__ import annotations

import json
from pathlib import Path

from koreader_ecosystem.koreader_calibre import PreviewItem, PreviewResult
from koreader_ecosystem.koreader_calibre_report import render_preview


def test_report_escapes_title_note_and_annotation_markup(tmp_path: Path) -> None:
    annotation_json = json.dumps(
        [{"id": "a" * 64, "annotation": {"text": '<script>alert("x")</script>'}}]
    )
    item = PreviewItem(
        sidecar_key="b" * 64,
        relative_path="Books/Privado.sdr/metadata.epub.lua",
        source_digest="c" * 64,
        match_status="matched",
        reason_code="exact_calibre_uuid",
        book_id=7,
        calibre_uuid="uuid-7",
        title='<img src=x onerror="alert(1)">',
        old_values={"koreader_note": None},
        new_values={
            "koreader_note": '<svg onload="alert(2)">',
            "koreader_annotations": annotation_json,
        },
        warnings=(),
        authors=("Autora & Co.",),
    )
    preview = PreviewResult("d" * 64, "d" * 64, (item,), ())

    output = render_preview(preview, tmp_path / "reports" / "preview.html")
    rendered = output.read_text(encoding="utf-8")

    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in rendered
    assert "&lt;svg onload=&quot;alert(2)&quot;&gt;" in rendered
    assert "&lt;script&gt;alert(\\&quot;x\\&quot;)&lt;/script&gt;" in rendered
    assert "Autora &amp; Co." in rendered
    assert "default-src 'none'" in rendered
    assert "https://" not in rendered
    assert "<script>alert" not in rendered


def test_report_lists_pending_items_and_change_values(tmp_path: Path) -> None:
    pending = PreviewItem(
        sidecar_key="e" * 64,
        relative_path="Books/Pendente.sdr/metadata.epub.lua",
        source_digest="f" * 64,
        match_status="unmatched",
        reason_code="calibre_uuid_missing",
        book_id=None,
        calibre_uuid=None,
        title=None,
        old_values={},
        new_values={},
        warnings=(),
    )
    output = render_preview(
        PreviewResult("1" * 64, "1" * 64, (pending,), ()), tmp_path / "pending.html"
    )
    rendered = output.read_text(encoding="utf-8")

    assert "calibre_uuid_missing" in rendered
    assert "Books/Pendente.sdr/metadata.epub.lua" in rendered
    assert "Pendências" in rendered


def test_report_does_not_count_unique_isbn_match_as_pending(tmp_path: Path) -> None:
    matched = PreviewItem(
        sidecar_key="a" * 64,
        relative_path="Books/Livro.sdr/metadata.epub.lua",
        source_digest="b" * 64,
        match_status="matched",
        reason_code="exact_isbn",
        book_id=7,
        calibre_uuid="uuid-7",
        title="Livro",
        old_values={},
        new_values={},
        warnings=(),
        authors=("Autora",),
    )

    output = render_preview(
        PreviewResult("c" * 64, "c" * 64, (matched,), ()), tmp_path / "isbn.html"
    )

    assert "1 sidecars · 0 com alterações · 0 pendentes" in output.read_text(encoding="utf-8")
