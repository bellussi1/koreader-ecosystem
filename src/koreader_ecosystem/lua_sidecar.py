from __future__ import annotations

import math
import re
from dataclasses import dataclass


class LuaSidecarError(ValueError):
    """Raised when a KOReader sidecar is not a bounded data-only Lua table."""


_MAX_NESTING = 128
_MAX_VALUES = 500_000
_NUMBER = re.compile(r"(?:0[xX][0-9A-Fa-f]+|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)")
_IDENTIFIER_START = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
_IDENTIFIER_CONTINUE = _IDENTIFIER_START + "0123456789"
_STRING_ESCAPES = {
    "a": 0x07,
    "b": 0x08,
    "f": 0x0C,
    "n": 0x0A,
    "r": 0x0D,
    "t": 0x09,
    "v": 0x0B,
    "\\": 0x5C,
    '"': 0x22,
    "'": 0x27,
}


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    value: object


class _Lexer:
    def __init__(self, source: str) -> None:
        self._source = source
        self._position = 0
        self._length = len(source)

    def next_token(self) -> _Token:
        self._skip_trivia()
        if self._position >= self._length:
            return _Token("eof", None)

        current = self._source[self._position]
        if current in "{}[]=,;+-":
            self._position += 1
            return _Token("symbol", current)
        if current in "\"'":
            return _Token("string", self._read_string(current))
        if current in "0123456789" or (
            current == "."
            and self._position + 1 < self._length
            and self._source[self._position + 1].isdigit()
        ):
            return _Token("number", self._read_number())
        if current in _IDENTIFIER_START:
            return _Token("identifier", self._read_identifier())
        raise LuaSidecarError("Unsupported Lua sidecar syntax")

    def _skip_trivia(self) -> None:
        while self._position < self._length:
            current = self._source[self._position]
            if current.isspace() or (current == "\ufeff" and self._position == 0):
                self._position += 1
                continue
            if self._source.startswith("--", self._position):
                self._skip_comment()
                continue
            break

    def _skip_comment(self) -> None:
        self._position += 2
        if self._position < self._length and self._source[self._position] == "[":
            opener = self._long_bracket_level(self._position)
            if opener is not None:
                level, content_start = opener
                closer = "]" + ("=" * level) + "]"
                end = self._source.find(closer, content_start)
                if end < 0:
                    raise LuaSidecarError("Unterminated Lua comment")
                self._position = end + len(closer)
                return
        newline = self._source.find("\n", self._position)
        self._position = self._length if newline < 0 else newline + 1

    def _long_bracket_level(self, start: int) -> tuple[int, int] | None:
        cursor = start + 1
        while cursor < self._length and self._source[cursor] == "=":
            cursor += 1
        if cursor < self._length and self._source[cursor] == "[":
            return cursor - start - 1, cursor + 1
        return None

    def _read_identifier(self) -> str:
        start = self._position
        self._position += 1
        while (
            self._position < self._length and self._source[self._position] in _IDENTIFIER_CONTINUE
        ):
            self._position += 1
        return self._source[start : self._position]

    def _read_number(self) -> int | float:
        match = _NUMBER.match(self._source, self._position)
        if match is None:
            raise LuaSidecarError("Invalid Lua number")
        literal = match.group(0)
        if len(literal) > 256:
            raise LuaSidecarError("Lua number exceeds the supported limit")
        self._position = match.end()
        try:
            if literal.lower().startswith("0x"):
                return int(literal, 16)
            if "." in literal or "e" in literal.lower():
                number = float(literal)
                if not math.isfinite(number):
                    raise LuaSidecarError("Lua number must be finite")
                return number
            return int(literal, 10)
        except ValueError as exc:
            raise LuaSidecarError("Invalid Lua number") from exc

    def _read_string(self, quote: str) -> str:
        self._position += 1
        output = bytearray()
        while self._position < self._length:
            current = self._source[self._position]
            self._position += 1
            if current == quote:
                try:
                    return output.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise LuaSidecarError("Lua string is not valid UTF-8") from exc
            if current in "\r\n":
                raise LuaSidecarError("Unescaped newline in Lua string")
            if current != "\\":
                output.extend(current.encode("utf-8"))
                continue
            if self._position >= self._length:
                raise LuaSidecarError("Truncated Lua string escape")

            escaped = self._source[self._position]
            self._position += 1
            if escaped in _STRING_ESCAPES:
                output.append(_STRING_ESCAPES[escaped])
                continue
            if escaped in "0123456789":
                digits = escaped
                while (
                    len(digits) < 3
                    and self._position < self._length
                    and self._source[self._position] in "0123456789"
                ):
                    digits += self._source[self._position]
                    self._position += 1
                byte_value = int(digits, 10)
                if byte_value > 255:
                    raise LuaSidecarError("Lua decimal escape exceeds one byte")
                output.append(byte_value)
                continue
            if escaped == "x":
                digits = self._source[self._position : self._position + 2]
                if len(digits) != 2 or any(char not in "0123456789abcdefABCDEF" for char in digits):
                    raise LuaSidecarError("Invalid Lua hexadecimal string escape")
                output.append(int(digits, 16))
                self._position += 2
                continue
            if escaped == "\n":
                output.append(0x0A)
                continue
            if escaped == "\r":
                if self._position < self._length and self._source[self._position] == "\n":
                    self._position += 1
                output.append(0x0A)
                continue
            raise LuaSidecarError("Unsupported Lua string escape")
        raise LuaSidecarError("Unterminated Lua string")


class _Parser:
    def __init__(self, source: str) -> None:
        self._lexer = _Lexer(source)
        self._buffer: list[_Token] = []
        self._value_count = 0

    def _peek(self, offset: int = 0) -> _Token:
        while len(self._buffer) <= offset:
            self._buffer.append(self._lexer.next_token())
        return self._buffer[offset]

    def _take(self) -> _Token:
        token = self._peek()
        del self._buffer[0]
        return token

    def _expect_symbol(self, symbol: str) -> None:
        token = self._take()
        if token.kind != "symbol" or token.value != symbol:
            raise LuaSidecarError("Unexpected Lua sidecar syntax")

    def parse(self) -> dict[str, object]:
        header = self._take()
        if header.kind != "identifier" or header.value != "return":
            raise LuaSidecarError("KOReader sidecar must return a data table")
        root = self._parse_table(depth=0, root=True)
        trailing = self._take()
        if trailing.kind != "eof":
            raise LuaSidecarError("Lua sidecar contains trailing syntax")
        if not isinstance(root, dict) or any(not isinstance(key, str) for key in root):
            raise LuaSidecarError("KOReader sidecar root keys must be strings")
        return {key: value for key, value in root.items() if isinstance(key, str)}

    def _parse_value(self, depth: int) -> object:
        self._value_count += 1
        if self._value_count > _MAX_VALUES:
            raise LuaSidecarError("Lua sidecar contains too many values")

        token = self._peek()
        if token.kind == "symbol" and token.value in ("+", "-"):
            sign = -1 if self._take().value == "-" else 1
            number = self._take()
            if (
                number.kind != "number"
                or not isinstance(number.value, (int, float))
                or isinstance(number.value, bool)
            ):
                raise LuaSidecarError("Lua signs are only allowed on numeric literals")
            return number.value if sign > 0 else -number.value
        if token.kind == "symbol" and token.value == "{":
            return self._parse_table(depth=depth, root=False)
        if token.kind == "string":
            return self._take().value
        if token.kind == "number":
            return self._take().value
        if token.kind == "identifier" and token.value in ("true", "false", "nil"):
            value = self._take().value
            if value == "true":
                return True
            if value == "false":
                return False
            return None
        raise LuaSidecarError("Unsupported Lua sidecar value")

    def _parse_table(self, depth: int, *, root: bool) -> object:
        if depth > _MAX_NESTING:
            raise LuaSidecarError("Lua sidecar exceeds the nesting limit")
        self._expect_symbol("{")
        fields: dict[str | int | float | bool, object] = {}
        implicit_index = 1

        while True:
            token = self._peek()
            if token.kind == "symbol" and token.value == "}":
                self._take()
                break
            if token.kind == "eof":
                raise LuaSidecarError("Truncated Lua table")

            if token.kind == "symbol" and token.value == "[":
                self._take()
                key = self._parse_value(depth + 1)
                self._expect_symbol("]")
                self._expect_symbol("=")
            elif (
                token.kind == "identifier"
                and token.value not in ("true", "false", "nil", "return")
                and self._peek(1).kind == "symbol"
                and self._peek(1).value == "="
            ):
                key = str(self._take().value)
                self._expect_symbol("=")
            else:
                key = implicit_index
                implicit_index += 1

            if not isinstance(key, (str, int, float, bool)) or key is None:
                raise LuaSidecarError("Lua table keys must be strings, numbers, or booleans")
            if key in fields:
                raise LuaSidecarError("Lua table contains a duplicate key")
            fields[key] = self._parse_value(depth + 1)

            delimiter = self._peek()
            if delimiter.kind == "symbol" and delimiter.value in (",", ";"):
                self._take()
                continue
            if delimiter.kind == "symbol" and delimiter.value == "}":
                continue
            raise LuaSidecarError("Lua table fields need a comma or semicolon")

        if root:
            return fields
        numeric_keys = [
            key for key in fields if not isinstance(key, bool) and isinstance(key, (int, float))
        ]
        if fields and len(numeric_keys) == len(fields):
            expected_keys = set(range(1, len(fields) + 1))
            if set(numeric_keys) == expected_keys:
                return [fields[index] for index in range(1, len(fields) + 1)]
        if not fields:
            return []
        return fields


def parse_sidecar(source: str, *, max_bytes: int = 16_777_216) -> dict[str, object]:
    """Parse a KOReader Lua sidecar as data without evaluating any Lua code."""
    if not isinstance(source, str):
        raise LuaSidecarError("Lua sidecar must be text")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise LuaSidecarError("The Lua sidecar byte limit must be a positive integer")
    try:
        source_size = len(source.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise LuaSidecarError("Lua sidecar is not valid UTF-8 text") from exc
    if source_size > max_bytes:
        raise LuaSidecarError("Lua sidecar exceeds the byte limit")
    return _Parser(source).parse()
