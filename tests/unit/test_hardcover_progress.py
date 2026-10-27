from pathlib import Path

import pytest
from lupa.luajit21 import LuaRuntime

from koreader_ecosystem.koreader import _apply_patch_to_files, _load_plugin_patch


def mapper(lua=None):
    source = Path("tests/fixtures/hardcover/page_mapper.lua").read_bytes()
    operations = tuple(
        op for op in _load_plugin_patch() if op["path"] == "hardcover/lib/page_mapper.lua"
    )
    patched = _apply_patch_to_files({"hardcover/lib/page_mapper.lua": source}, operations)
    lua = lua or LuaRuntime(unpack_returned_tuples=True)
    lua.execute('package.preload["hardcover/lib/table_util"] = function() return {} end')
    module = lua.execute(patched["hardcover/lib/page_mapper.lua"].decode())
    instance = module.new(module, lua.table_from({"state": lua.table_from({})}))
    return instance


def test_epub_progress_uses_fraction_not_page_labels():
    instance = mapper()
    assert instance.getProgressPage(instance, "book.epub", 76, 100, 355) == 269
    assert instance.getProgressPercent(instance, "book.epub", 76, 100, 355) == (0.76, 269)


@pytest.mark.parametrize("raw,total,remote", [(0, 100, 355), (101, 100, 355), (-1, 100, 355)])
def test_epub_progress_is_bounded(raw, total, remote):
    instance = mapper()
    result = instance.getProgressPage(instance, "book.EPUB", raw, total, remote)
    assert 0 <= result <= remote


def test_epub_missing_page_counts_does_not_invent_progress():
    instance = mapper()
    assert instance.getProgressPage(instance, "book.epub", 20, 100, None) is None
    assert instance.getProgressPage(instance, "book.epub", 20, 0, 355) is None


def test_epub_ignores_printed_page_labels_without_changing_them():
    lua = LuaRuntime(unpack_returned_tuples=True)
    instance = mapper(lua)
    # A labelled page in the EPUB belongs to its own edition, not the remote edition.
    instance.state.page_map = lua.table_from({76: 316})
    assert instance.getProgressPage(instance, "book.epub", 76, 100, 355) == 269
    assert instance.state.page_map[76] == 316


def test_other_formats_keep_the_original_mapping():
    lua = LuaRuntime(unpack_returned_tuples=True)
    instance = mapper(lua)
    instance.use_page_map = True
    instance.ui = lua.eval("{pagemap = { wantsPageLabels = function() return true end }}")
    instance.state.page_map = lua.table_from({76: 316})
    assert instance.getProgressPage(instance, "book.pdf", 76, 100, 355) == 316


def test_epub_requires_matching_remote_edition_before_sync():
    operations = _load_plugin_patch()
    operation = next(
        op for op in operations if "function HardcoverApp:progressEditionMatches" in op["new"]
    )
    fragment = operation["new"]
    start = fragment.index("function HardcoverApp:progressEditionMatches")
    end = fragment.index("\nfunction HardcoverApp:", start + 1)
    lua = LuaRuntime(unpack_returned_tuples=True)
    app = lua.execute("local HardcoverApp = {}\n" + fragment[start:end] + "\nreturn HardcoverApp")
    context = lua.eval("""function(selected, remote)
      return { settings = { readBookSetting = function() return selected end },
        state = { book_status = { edition_id = remote, user_book_reads = {} } } }
    end""")
    assert app.progressEditionMatches(context(355, 355), "book.epub") is True
    assert app.progressEditionMatches(context(355, 422), "book.epub") is False
    assert app.progressEditionMatches(context(None, 355), "book.epub") is False
    assert app.progressEditionMatches(context(None, 355), "book.pdf") is True


def test_epub_rejects_a_different_active_read_edition():
    operation = next(
        op
        for op in _load_plugin_patch()
        if "function HardcoverApp:progressEditionMatches" in op["new"]
    )
    fragment = operation["new"]
    start = fragment.index("function HardcoverApp:progressEditionMatches")
    end = fragment.index("\nfunction HardcoverApp:", start + 1)
    lua = LuaRuntime(unpack_returned_tuples=True)
    app = lua.execute("local HardcoverApp = {}\n" + fragment[start:end] + "\nreturn HardcoverApp")
    context = lua.eval("""{ settings = { readBookSetting = function() return 355 end },
        state = { book_status = { edition_id = 355,
        user_book_reads = {{edition_id = 422}} } } }""")
    assert app.progressEditionMatches(context, "book.epub") is False


def test_plugin_main_patch_applies_to_upstream_and_compiles():
    source = Path("tests/fixtures/hardcover/main.lua").read_bytes()
    operations = tuple(op for op in _load_plugin_patch() if op["path"] == "main.lua")
    patched = _apply_patch_to_files({"main.lua": source}, operations)["main.lua"].decode()
    lua = LuaRuntime(unpack_returned_tuples=True)
    compile_lua = lua.eval("function(source) return loadstring(source) end")
    assert not isinstance(compile_lua(patched), tuple)
    assert patched.count("self.page_mapper:getProgressPage(") == 4
    assert patched.count("self.page_mapper:getProgressPercent(") == 2
