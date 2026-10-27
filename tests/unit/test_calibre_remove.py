from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from koreader_ecosystem.calibre_catalog import CalibreBook
from koreader_ecosystem.calibre_library import remove_calibre_book


def _book() -> CalibreBook:
    return CalibreBook(7, "0f8fad5b-d9cb-469f-a165-70867728950e", "Livro", ("Autora",), {}, {})


def test_removal_uses_calibre_recycle_bin_and_verifies_catalog_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "calibre"
    library.mkdir()
    (library / "metadata.db").touch()
    calls: list[list[str]] = []
    catalogs = iter([(_book(),), ()])

    def runner(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("koreader_ecosystem.calibre_library._ensure_calibre_closed", lambda: None)
    monkeypatch.setattr("koreader_ecosystem.calibre_library.shutil.which", lambda _: "calibredb")
    monkeypatch.setattr(
        "koreader_ecosystem.calibre_library.load_calibre_catalog", lambda *_: next(catalogs)
    )

    remove_calibre_book(library, 7, runner)

    assert calls == [["calibredb", "--with-library", str(library), "remove", "7"]]
    assert "--permanent" not in calls[0]
