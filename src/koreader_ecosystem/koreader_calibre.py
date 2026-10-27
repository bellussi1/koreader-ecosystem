from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TypeGuard

from koreader_ecosystem.calibre_catalog import CalibreBook, MatchResult, match_sidecar
from koreader_ecosystem.lua_sidecar import LuaSidecarError, parse_sidecar
from koreader_ecosystem.sync_state import SyncState, SyncStateError


class KoreaderCalibreError(RuntimeError):
    """Raised when a KOReader preview cannot be created or safely reused."""

    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class ImportChange:
    book_id: int
    calibre_uuid: str
    title: str
    old_values: dict[str, object]
    new_values: dict[str, object]
    source_digest: str
    warnings: tuple[str, ...]
    sidecar_key: str
    relative_path: str


@dataclass(frozen=True, slots=True)
class PreviewItem:
    sidecar_key: str
    relative_path: str
    source_digest: str
    match_status: str
    reason_code: str
    book_id: int | None
    calibre_uuid: str | None
    title: str | None
    old_values: dict[str, object]
    new_values: dict[str, object]
    warnings: tuple[str, ...]
    authors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreviewResult:
    preview_id: str
    plan_digest: str
    items: tuple[PreviewItem, ...]
    changes: tuple[ImportChange, ...]


@dataclass(frozen=True, slots=True)
class _Source:
    path: Path
    relative_path: str
    sidecar_key: str
    source_digest: str
    raw_data: dict[str, object] | None
    reason_code: str | None


_MAX_SIDECAR_BYTES = 16_777_216
_IMPORT_FIELDS = (
    "koreader_progress",
    "koreader_status",
    "koreader_position",
    "koreader_rating",
    "koreader_note",
    "koreader_annotations",
)
_VALID_STATUSES = frozenset({"new", "reading", "complete", "abandoned"})
_ANNOTATION_ID_FIELDS = (
    "pos0",
    "pos1",
    "text",
    "note",
    "chapter",
    "pageno",
    "drawer",
    "color",
)


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except TypeError, ValueError, OverflowError:
        raise KoreaderCalibreError(
            "KOReader data is not valid JSON", reason_code="invalid_data"
        ) from None


def _normalize_relative_path(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        raise KoreaderCalibreError(
            "Sidecar is outside the staging directory", reason_code="invalid_path"
        ) from None
    normalized = unicodedata.normalize("NFC", relative)
    parsed = PurePosixPath(normalized)
    if (
        not normalized
        or parsed.is_absolute()
        or any(part in ("", ".", "..") for part in parsed.parts)
        or "\\" in normalized
        or "\x00" in normalized
        or (parsed.parts and ":" in parsed.parts[0])
    ):
        raise KoreaderCalibreError("Sidecar path is invalid", reason_code="invalid_path")
    return normalized


def _read_source(path: Path, root: Path) -> _Source:
    relative = _normalize_relative_path(path, root)
    sidecar_key = hashlib.sha256(relative.encode("utf-8")).hexdigest()
    try:
        if path.is_symlink() or path.is_junction():
            raise KoreaderCalibreError("Sidecar path cannot use a link", reason_code="invalid_path")
        size = path.stat().st_size
        if size > _MAX_SIDECAR_BYTES:
            raw = path.open("rb").read(_MAX_SIDECAR_BYTES + 1)
            digest = hashlib.sha256(raw).hexdigest()
            return _Source(path, relative, sidecar_key, digest, None, "invalid_lua")
        with path.open("rb") as handle:
            raw = handle.read(_MAX_SIDECAR_BYTES + 1)
    except KoreaderCalibreError:
        raise
    except OSError:
        return _Source(path, relative, sidecar_key, "0" * 64, None, "sidecar_read_error")

    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) > _MAX_SIDECAR_BYTES:
        return _Source(path, relative, sidecar_key, digest, None, "invalid_lua")
    try:
        source = raw.decode("utf-8")
        data = parse_sidecar(source, max_bytes=_MAX_SIDECAR_BYTES)
    except UnicodeDecodeError, LuaSidecarError:
        return _Source(path, relative, sidecar_key, digest, None, "invalid_lua")
    return _Source(path, relative, sidecar_key, digest, data, None)


def _discover_sources(stage_root: Path) -> tuple[_Source, ...]:
    root = Path(stage_root).expanduser()
    if not root.is_dir() or root.is_symlink() or root.is_junction():
        raise KoreaderCalibreError(
            "KOReader staging directory is unavailable", reason_code="stage_unavailable"
        )
    try:
        paths = sorted(
            (path for path in root.rglob("metadata.*.lua") if path.is_file()),
            key=lambda path: unicodedata.normalize("NFC", path.relative_to(root).as_posix()),
        )
        sources = tuple(_read_source(path, root) for path in paths)
    except OSError, RuntimeError:
        raise KoreaderCalibreError(
            "KOReader staging directory could not be read", reason_code="stage_read_error"
        ) from None
    keys = [source.sidecar_key for source in sources]
    if len(set(keys)) != len(keys):
        raise KoreaderCalibreError(
            "KOReader sidecar paths are not unique", reason_code="duplicate_sidecar"
        )
    return sources


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _number(value: object) -> float | None:
    if not _is_number(value):
        return None
    return float(value)


def has_completion_progress_conflict(values: Mapping[str, object]) -> bool:
    progress = _number(values.get("koreader_progress"))
    return values.get("koreader_status") == "complete" and progress is not None and progress < 1.0


def _deduplicate_annotations(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    unique: dict[str, dict[str, object]] = {}
    for annotation in value:
        if not isinstance(annotation, dict) or not all(isinstance(key, str) for key in annotation):
            continue
        stable = {key: annotation[key] for key in _ANNOTATION_ID_FIELDS if key in annotation}
        if not stable:
            stable = {key: item for key, item in annotation.items() if key != "datetime"}
        try:
            identifier = hashlib.sha256(_canonical_json(stable).encode("utf-8")).hexdigest()
            _canonical_json(annotation)
        except KoreaderCalibreError:
            continue
        unique.setdefault(identifier, {"id": identifier, "annotation": dict(annotation)})
    return _canonical_json([unique[key] for key in sorted(unique)])


def _extract_sidecar_values(
    data: dict[str, object],
) -> tuple[dict[str, object], str | None, tuple[str, ...]]:
    candidates: dict[str, object] = {}
    warnings: list[str] = []

    progress = _number(data.get("percent_finished"))
    if progress is not None:
        if 0 <= progress <= 1:
            candidates["koreader_progress"] = progress
        else:
            warnings.append("invalid_progress")

    summary = data.get("summary")
    if isinstance(summary, dict):
        status = summary.get("status")
        if status is not None:
            if isinstance(status, str) and status in _VALID_STATUSES:
                candidates["koreader_status"] = status
            else:
                return {}, "unknown_status", tuple(warnings)

        rating = _number(summary.get("rating"))
        if rating is not None:
            if 0 <= rating <= 5:
                candidates["koreader_rating"] = rating
            else:
                warnings.append("invalid_rating")

        note = summary.get("note")
        if note is not None:
            if isinstance(note, str):
                candidates["koreader_note"] = note
            else:
                warnings.append("invalid_note")

    position = data.get("last_xpointer")
    if position is not None:
        if isinstance(position, str):
            candidates["koreader_position"] = position
        else:
            warnings.append("invalid_position")

    if "annotations" in data:
        annotations = _deduplicate_annotations(data["annotations"])
        if annotations is None:
            warnings.append("invalid_annotations")
        else:
            candidates["koreader_annotations"] = annotations

    return candidates, None, tuple(warnings)


def _decode_stored_annotations(value: object) -> list[dict[str, object]] | None:
    if value is None or value == "":
        return []
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, list) or not all(isinstance(item, dict) for item in decoded):
        return None
    output: list[dict[str, object]] = []
    for item in decoded:
        identifier = item.get("id")
        annotation = item.get("annotation")
        if not isinstance(identifier, str) or not isinstance(annotation, dict):
            return None
        output.append({"id": identifier, "annotation": annotation})
    return output


def _merged_annotations(old_value: object, incoming_value: object) -> str | None:
    existing = _decode_stored_annotations(old_value)
    incoming = _decode_stored_annotations(incoming_value)
    if existing is None or incoming is None:
        return None
    merged = {str(item["id"]): item for item in [*existing, *incoming]}
    return _canonical_json([merged[key] for key in sorted(merged)])


def _prepare_values(
    book: CalibreBook,
    candidates: dict[str, object],
    checkpoint: dict[str, object] | None,
) -> tuple[dict[str, object], dict[str, object], tuple[str, ...], str | None]:
    old_values: dict[str, object] = {}
    new_values: dict[str, object] = {}
    warnings: list[str] = []
    if has_completion_progress_conflict(candidates):
        return {}, {}, ("status_progress_conflict",), "status_progress_conflict"
    checkpoint_values = checkpoint.get("values", {}) if checkpoint else {}
    if not isinstance(checkpoint_values, dict):
        checkpoint_values = {}

    for field in _IMPORT_FIELDS:
        if field not in candidates:
            continue
        incoming = candidates[field]
        current = book.custom_values.get(field)
        if field == "koreader_annotations":
            merged = _merged_annotations(current, incoming)
            if merged is None:
                warnings.append("annotations_conflict")
                return {}, {}, tuple(warnings), "field_conflict"
            incoming = merged
            if current == incoming:
                continue
        elif current == incoming:
            continue
        else:
            current_number = _number(current)
            incoming_number = _number(incoming)
            if (
                field == "koreader_progress"
                and current_number is not None
                and incoming_number is not None
                and incoming_number < current_number
            ):
                warnings.append("progress_regression")
                return {}, {}, tuple(warnings), "progress_regression"
            if current not in (None, "") and field not in checkpoint_values:
                warnings.append(f"{field}_conflict")
                return {}, {}, tuple(warnings), "field_conflict"
            if (
                current not in (None, "")
                and field in checkpoint_values
                and current != checkpoint_values[field]
            ):
                warnings.append(f"{field}_changed_since_sync")
                return {}, {}, tuple(warnings), "field_conflict"

        old_values[field] = current
        new_values[field] = incoming
    return old_values, new_values, tuple(warnings), None


def _item_dict(item: PreviewItem) -> dict[str, object]:
    return {
        "sidecar_key": item.sidecar_key,
        "relative_path": item.relative_path,
        "source_digest": item.source_digest,
        "match_status": item.match_status,
        "reason_code": item.reason_code,
        "book_id": item.book_id,
        "calibre_uuid": item.calibre_uuid,
        "title": item.title,
        "authors": list(item.authors),
        "old_values": item.old_values,
        "new_values": item.new_values,
        "warnings": list(item.warnings),
        "applied_values": {},
        "applied_at": None,
    }


def _plan_row(item: PreviewItem) -> dict[str, object]:
    return {
        "sidecar_key": item.sidecar_key,
        "relative_path": item.relative_path,
        "source_digest": item.source_digest,
        "match_status": item.match_status,
        "reason_code": item.reason_code,
        "book_id": item.book_id,
        "calibre_uuid": item.calibre_uuid,
        "old_values": item.old_values,
        "new_values": item.new_values,
        "warnings": item.warnings,
    }


def _match_source(
    source: _Source,
    catalog: tuple[CalibreBook, ...],
    manual_map: dict[str, int],
    state: SyncState,
) -> tuple[PreviewItem, ImportChange | None]:
    if source.raw_data is None:
        reason = source.reason_code or "invalid_lua"
        return (
            PreviewItem(
                source.sidecar_key,
                source.relative_path,
                source.source_digest,
                "unmatched",
                reason,
                None,
                None,
                None,
                {},
                {},
                (),
            ),
            None,
        )
    match: MatchResult = match_sidecar(source.sidecar_key, source.raw_data, catalog, manual_map)
    if match.book is None:
        return (
            PreviewItem(
                source.sidecar_key,
                source.relative_path,
                source.source_digest,
                match.status,
                match.reason_code,
                None,
                None,
                None,
                {},
                {},
                (),
            ),
            None,
        )

    candidates, reason_code, extraction_warnings = _extract_sidecar_values(source.raw_data)
    book = match.book
    if reason_code is not None:
        return (
            PreviewItem(
                source.sidecar_key,
                source.relative_path,
                source.source_digest,
                match.status,
                reason_code,
                book.book_id,
                book.uuid,
                book.title,
                {},
                {},
                extraction_warnings,
            ),
            None,
        )

    try:
        checkpoint = state.load_checkpoint(book.uuid)
    except SyncStateError:
        raise KoreaderCalibreError(
            "Local synchronization state is invalid", reason_code="state_error"
        ) from None
    old_values, new_values, conflict_warnings, conflict = _prepare_values(
        book, candidates, checkpoint
    )
    warnings = (*extraction_warnings, *conflict_warnings)
    if conflict is not None:
        return (
            PreviewItem(
                source.sidecar_key,
                source.relative_path,
                source.source_digest,
                match.status,
                conflict,
                book.book_id,
                book.uuid,
                book.title,
                {},
                {},
                warnings,
            ),
            None,
        )
    if not new_values:
        return (
            PreviewItem(
                source.sidecar_key,
                source.relative_path,
                source.source_digest,
                match.status,
                "already_current",
                book.book_id,
                book.uuid,
                book.title,
                {},
                {},
                warnings,
            ),
            None,
        )

    item = PreviewItem(
        source.sidecar_key,
        source.relative_path,
        source.source_digest,
        match.status,
        match.reason_code,
        book.book_id,
        book.uuid,
        book.title,
        old_values,
        new_values,
        warnings,
        book.authors,
    )
    return item, ImportChange(
        book.book_id,
        book.uuid,
        book.title,
        old_values,
        new_values,
        source.source_digest,
        warnings,
        source.sidecar_key,
        source.relative_path,
    )


def create_preview(
    stage_root: Path,
    catalog: tuple[CalibreBook, ...],
    state_db: Path,
) -> PreviewResult:
    """Create a deterministic, reviewable import plan from staged KOReader sidecars."""
    sources = _discover_sources(stage_root)
    try:
        state = SyncState(state_db)
        manual_map = state.manual_mappings()
        resolved_maps = {
            source.sidecar_key: manual_map[source.sidecar_key]
            for source in sources
            if source.sidecar_key in manual_map
        }
        items_and_changes = [
            _match_source(source, catalog, resolved_maps, state) for source in sources
        ]
    except SyncStateError:
        raise KoreaderCalibreError(
            "Local synchronization state is unavailable", reason_code="state_error"
        ) from None

    items = tuple(item for item, _ in items_and_changes)
    changes = tuple(change for _, change in items_and_changes if change is not None)
    plan_material = [_plan_row(item) for item in sorted(items, key=lambda item: item.sidecar_key)]
    plan_digest = hashlib.sha256(_canonical_json(plan_material).encode("utf-8")).hexdigest()
    preview_id = plan_digest
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        state.save_preview(
            preview_id, plan_digest, created_at, [_item_dict(item) for item in items]
        )
    except SyncStateError:
        raise KoreaderCalibreError(
            "Preview could not be saved to local state", reason_code="state_error"
        ) from None
    return PreviewResult(preview_id, plan_digest, items, changes)


def assert_preview_plan_unchanged(state_db: Path, preview_id: str) -> None:
    """Verify that locally persisted proposed fields still match the preview digest."""
    try:
        saved = SyncState(state_db).load_preview(preview_id)
    except SyncStateError:
        raise KoreaderCalibreError(
            "Saved preview cannot be read", reason_code="state_error"
        ) from None
    if saved.get("plan_digest") != preview_id:
        raise KoreaderCalibreError("Saved preview digest is invalid", reason_code="preview_changed")
    raw_items = saved.get("items")
    if not isinstance(raw_items, list) or not all(isinstance(item, dict) for item in raw_items):
        raise KoreaderCalibreError("Saved preview is invalid", reason_code="state_error")
    fields = (
        "sidecar_key",
        "relative_path",
        "source_digest",
        "match_status",
        "reason_code",
        "book_id",
        "calibre_uuid",
        "old_values",
        "new_values",
        "warnings",
    )
    plan_material = [
        {field: item.get(field) for field in fields}
        for item in sorted(raw_items, key=lambda item: str(item.get("sidecar_key", "")))
    ]
    digest = hashlib.sha256(_canonical_json(plan_material).encode("utf-8")).hexdigest()
    if digest != preview_id:
        raise KoreaderCalibreError("Saved preview contents changed", reason_code="preview_changed")


def assert_preview_sources_unchanged(stage_root: Path, state_db: Path, preview_id: str) -> None:
    """Reject a saved preview if its staged sidecar set or any digest has changed."""
    try:
        state = SyncState(state_db)
        saved = state.load_preview(preview_id)
        saved_items = saved.get("items")
        if not isinstance(saved_items, list):
            raise SyncStateError("Saved preview is invalid")
    except SyncStateError:
        raise KoreaderCalibreError(
            "Saved preview cannot be read", reason_code="state_error"
        ) from None

    current = _discover_sources(stage_root)
    expected: dict[str, tuple[str, str]] = {}
    for item in saved_items:
        if not isinstance(item, dict):
            raise KoreaderCalibreError("Saved preview is invalid", reason_code="state_error")
        key = item.get("sidecar_key")
        path = item.get("relative_path")
        digest = item.get("source_digest")
        if not isinstance(key, str) or not isinstance(path, str) or not isinstance(digest, str):
            raise KoreaderCalibreError("Saved preview is invalid", reason_code="state_error")
        expected[key] = (path, digest)
    observed = {
        source.sidecar_key: (source.relative_path, source.source_digest) for source in current
    }
    if expected != observed:
        raise KoreaderCalibreError(
            "KOReader sidecars changed after the preview", reason_code="sidecar_changed"
        )
