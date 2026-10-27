from __future__ import annotations

import pytest

from koreader_ecosystem.lua_sidecar import LuaSidecarError, parse_sidecar


def test_parse_koreader_sidecar_header_nested_data_and_unicode_escapes() -> None:
    source = r"""-- /mnt/us/books/example.sdr/metadata.epub.lua
return {
    ["percent_finished"] = 0.625,
    ["summary"] = {
        ["status"] = "reading",
        ["note"] = "A\195\167\195\163o – café",
    },
    ["annotations"] = {
        [1] = { ["text"] = 'Trecho "importante"', ["page"] = 12 },
        [2] = { ["text"] = "Segunda nota", ["page"] = 13 },
    },
    ["flags"] = { enabled = true, hidden = false, missing = nil },
}"""

    result = parse_sidecar(source)

    assert result["percent_finished"] == 0.625
    assert result["summary"] == {"status": "reading", "note": "Ação – café"}
    assert result["annotations"] == [
        {"text": 'Trecho "importante"', "page": 12},
        {"text": "Segunda nota", "page": 13},
    ]
    assert result["flags"] == {"enabled": True, "hidden": False, "missing": None}


@pytest.mark.parametrize(
    "source",
    [
        'return { ["x"] = os.execute("whoami") }',
        'return { ["x"] = 1 + 2 }',
        'return { ["x"] = function() return 1 end }',
        "return setmetatable({}, {})",
        'return { ["x"] = { [1] = "truncated" ',
        'return { ["x"] = "unterminated }',
    ],
)
def test_rejects_executable_expressions_and_truncated_input(source: str) -> None:
    with pytest.raises(LuaSidecarError):
        parse_sidecar(source)


def test_rejects_input_above_byte_limit() -> None:
    with pytest.raises(LuaSidecarError):
        parse_sidecar('return { ["x"] = "é" }', max_bytes=10)


def test_requires_a_returned_root_table_and_rejects_trailing_code() -> None:
    for source in ("{}", 'return {} os.execute("whoami")', "return 1"):
        with pytest.raises(LuaSidecarError):
            parse_sidecar(source)


def test_rejects_excessive_nesting_without_recursing_unboundedly() -> None:
    source = "return " + ("{" * 140) + "0" + ("}" * 140)

    with pytest.raises(LuaSidecarError):
        parse_sidecar(source)


def test_array_shape_does_not_depend_on_lua_table_iteration_order() -> None:
    source = 'return { ["annotations"] = { [2] = "second", [1] = "first" } }'

    assert parse_sidecar(source) == {"annotations": ["first", "second"]}
