from __future__ import annotations

import math
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from koreader_ecosystem.backup import (
    BackupError,
    BackupResult,
    ResticOperations,
    _check_calibre_integrity,
    _running_process_names,
    create_calibre_backup,
)
from koreader_ecosystem.calibre_catalog import (
    CalibreBook,
    CalibreCatalogError,
    load_calibre_catalog,
    load_calibre_custom_columns,
)
from koreader_ecosystem.config import AppConfig
from koreader_ecosystem.koreader_calibre import (
    KoreaderCalibreError,
    assert_preview_plan_unchanged,
    assert_preview_sources_unchanged,
    has_completion_progress_conflict,
)
from koreader_ecosystem.processes import run_hidden
from koreader_ecosystem.safety import SafetyViolation, require_calibre_closed
from koreader_ecosystem.sync_state import SyncState, SyncStateError


class CalibreApplyError(KoreaderCalibreError):
    """Raised when a planned Calibre update cannot be safely verified."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code, reason_code=reason_code)


@dataclass(frozen=True, slots=True)
class ApplyResult:
    snapshot_id: str | None
    applied_book_count: int
    applied_field_count: int


type Runner = Callable[..., subprocess.CompletedProcess[str]]

_COLUMN_TYPES = {
    "koreader_progress": "float",
    "koreader_status": "text",
    "koreader_position": "text",
    "koreader_rating": "float",
    # KOReader annotations can contain ebook markup; plain text avoids rendering it as HTML.
    "koreader_note": "text",
    "koreader_annotations": "text",
}
_COLUMN_NAMES = {
    "koreader_progress": "KOReader Progresso",
    "koreader_status": "KOReader Status",
    "koreader_position": "KOReader Posição",
    "koreader_rating": "KOReader Avaliação",
    "koreader_note": "KOReader Nota",
    "koreader_annotations": "KOReader Anotações",
}
_SNAPSHOT_PREFIX = "koreader-ecosystem"


def _ensure_calibre_closed() -> None:
    try:
        require_calibre_closed(_running_process_names())
    except SafetyViolation:
        raise CalibreApplyError("calibre_open") from None


def _catalog(library: Path, runner: Runner) -> tuple[CalibreBook, ...]:
    try:
        return load_calibre_catalog(library, runner)
    except CalibreCatalogError:
        raise CalibreApplyError("calibre_catalog_unavailable") from None


def _columns(library: Path, runner: Runner) -> dict[str, str]:
    try:
        return load_calibre_custom_columns(library, runner)
    except CalibreCatalogError:
        raise CalibreApplyError("calibre_columns_unavailable") from None


def _run_calibredb(library: Path, command: str, args: list[str], runner: Runner) -> None:
    executable = shutil.which("calibredb")
    if executable is None:
        raise CalibreApplyError("calibredb_unavailable")
    try:
        result = runner(
            [executable, "--with-library", str(library), command, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except OSError, subprocess.SubprocessError:
        raise CalibreApplyError("calibredb_command_failed") from None
    if result.returncode != 0:
        raise CalibreApplyError("calibredb_command_failed")


def _record(
    item: Mapping[str, object],
) -> tuple[int, str, str, dict[str, object], dict[str, object]]:
    book_id = item.get("book_id")
    calibre_uuid = item.get("calibre_uuid")
    sidecar_key = item.get("sidecar_key")
    source_digest = item.get("source_digest")
    old_values = item.get("old_values")
    new_values = item.get("new_values")
    if (
        not isinstance(book_id, int)
        or isinstance(book_id, bool)
        or book_id < 1
        or not isinstance(calibre_uuid, str)
        or not calibre_uuid.strip()
        or not isinstance(sidecar_key, str)
        or not isinstance(source_digest, str)
        or not isinstance(old_values, dict)
        or not isinstance(new_values, dict)
        or not new_values
        or not all(isinstance(key, str) for key in old_values)
        or not all(isinstance(key, str) for key in new_values)
    ):
        raise CalibreApplyError("preview_invalid")
    if any(key not in _COLUMN_TYPES for key in new_values):
        raise CalibreApplyError("preview_invalid")
    if any(key not in old_values for key in new_values):
        raise CalibreApplyError("preview_invalid")
    for field, value in new_values.items():
        if _COLUMN_TYPES[field] == "float":
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                raise CalibreApplyError("preview_invalid")
            limit = 1 if field == "koreader_progress" else 5
            if not 0 <= float(value) <= limit:
                raise CalibreApplyError("preview_invalid")
        elif not isinstance(value, str):
            raise CalibreApplyError("preview_invalid")
    return book_id, calibre_uuid, sidecar_key, old_values, new_values


def _equal(left: object, right: object) -> bool:
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        return math.isfinite(left) and math.isfinite(right) and float(left) == float(right)
    return left == right


def _resolve_books(
    records: list[tuple[dict[str, object], int, str, str, dict[str, object], dict[str, object]]],
    catalog: tuple[CalibreBook, ...],
) -> dict[str, CalibreBook]:
    by_id = {candidate.book_id: candidate for candidate in catalog}
    by_uuid: dict[str, list[CalibreBook]] = {}
    for candidate in catalog:
        by_uuid.setdefault(candidate.uuid.casefold(), []).append(candidate)
    selected: dict[str, CalibreBook] = {}
    used_uuids: set[str] = set()
    for _item, book_id, calibre_uuid, _, old_values, new_values in records:
        resolved_book = by_id.get(book_id)
        if resolved_book is None or resolved_book.uuid.casefold() != calibre_uuid.casefold():
            raise CalibreApplyError("calibre_book_changed")
        matches = by_uuid.get(calibre_uuid.casefold(), [])
        if len(matches) != 1:
            raise CalibreApplyError("calibre_uuid_ambiguous")
        normalized_uuid = calibre_uuid.casefold()
        if normalized_uuid in used_uuids:
            raise CalibreApplyError("duplicate_book_in_preview")
        used_uuids.add(normalized_uuid)
        for field, new_value in new_values.items():
            current = resolved_book.custom_values.get(field)
            old_value = old_values[field]
            if not _equal(current, old_value) and not _equal(current, new_value):
                raise CalibreApplyError("concurrent_change")
        selected[normalized_uuid] = resolved_book
    return selected


def _ensure_columns(library: Path, runner: Runner) -> None:
    columns = _columns(library, runner)
    for label, expected_type in _COLUMN_TYPES.items():
        actual_type = columns.get(label)
        if actual_type is not None and actual_type != expected_type:
            raise CalibreApplyError("column_type_mismatch")

    for label, expected_type in _COLUMN_TYPES.items():
        if label in columns:
            continue
        _ensure_calibre_closed()
        _run_calibredb(
            library,
            "add_custom_column",
            [label, _COLUMN_NAMES[label], expected_type],
            runner,
        )

    verified = _columns(library, runner)
    if any(verified.get(label) != expected for label, expected in _COLUMN_TYPES.items()):
        raise CalibreApplyError("column_verification_failed")


def _load_preview(state: SyncState, preview_id: str) -> list[dict[str, object]]:
    try:
        saved = state.load_preview(preview_id)
    except SyncStateError:
        raise CalibreApplyError("preview_unavailable") from None
    items = saved.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise CalibreApplyError("preview_invalid")
    return [item for item in items if isinstance(item, dict)]


def _write_value(
    library: Path,
    book_id: int,
    field: str,
    value: object,
    runner: Runner,
) -> None:
    if isinstance(value, bool) or value is None:
        raise CalibreApplyError("preview_invalid")
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise CalibreApplyError("preview_invalid")
        serialized = str(value)
    elif isinstance(value, str):
        serialized = value
    else:
        raise CalibreApplyError("preview_invalid")
    _ensure_calibre_closed()
    _run_calibredb(library, "set_custom", [field, str(book_id), serialized], runner)


def _checkpoint_records(
    state: SyncState,
    preview_id: str,
    records: list[tuple[dict[str, object], int, str, str, dict[str, object], dict[str, object]]],
    *,
    applied_at: str,
) -> int:
    count = 0
    for item, book_id, calibre_uuid, sidecar_key, _, new_values in records:
        for field, value in new_values.items():
            try:
                state.record_applied_field(
                    preview_id,
                    sidecar_key,
                    calibre_uuid,
                    book_id,
                    str(item["source_digest"]),
                    field,
                    value,
                    applied_at,
                )
            except SyncStateError:
                raise CalibreApplyError("state_checkpoint_failed") from None
            count += 1
    return count


def _apply_saved_preview(
    config: AppConfig,
    state_db: Path,
    stage_root: Path | None,
    preview_id: str,
    restic: ResticOperations,
    *,
    runner: Runner = run_hidden,
) -> ApplyResult:
    """Back up, apply a saved preview through calibredb, and verify each field."""
    state = SyncState(state_db)
    try:
        assert_preview_plan_unchanged(state_db, preview_id)
        if stage_root is not None:
            assert_preview_sources_unchanged(stage_root, state_db, preview_id)
    except KoreaderCalibreError as exc:
        raise CalibreApplyError(exc.reason_code) from None
    items = _load_preview(state, preview_id)
    records = [
        (item, *_record(item))
        for item in items
        if isinstance(item.get("new_values"), dict) and bool(item["new_values"])
    ]
    if any(has_completion_progress_conflict(new_values) for _, _, _, _, _, new_values in records):
        raise CalibreApplyError("status_progress_conflict")
    if not records:
        return ApplyResult(None, 0, 0)
    if any(item.get("match_status") not in {"matched", "manual"} for item, *_ in records):
        raise CalibreApplyError("preview_invalid")

    _ensure_calibre_closed()
    try:
        _check_calibre_integrity(config.calibre_library)
    except BackupError:
        raise CalibreApplyError("calibre_integrity_failed") from None

    columns = _columns(config.calibre_library, runner)
    if any(
        label in columns and columns[label] != expected for label, expected in _COLUMN_TYPES.items()
    ):
        raise CalibreApplyError("column_type_mismatch")
    catalog = _catalog(config.calibre_library, runner)
    initial_books = _resolve_books(records, catalog)

    if all(
        all(
            _equal(initial_books[calibre_uuid.casefold()].custom_values.get(field), value)
            for field, value in new_values.items()
        )
        for _, _, calibre_uuid, _, _, new_values in records
    ):
        applied_at = datetime.now(UTC).isoformat(timespec="seconds")
        applied_fields = _checkpoint_records(state, preview_id, records, applied_at=applied_at)
        return ApplyResult(None, len(records), applied_fields)

    try:
        backup: BackupResult = create_calibre_backup(config, restic)
    except BackupError:
        raise CalibreApplyError("backup_failed") from None

    _ensure_calibre_closed()
    _ensure_columns(config.calibre_library, runner)
    _ensure_calibre_closed()
    try:
        _check_calibre_integrity(config.calibre_library)
    except BackupError:
        raise CalibreApplyError("calibre_integrity_failed") from None
    catalog = _catalog(config.calibre_library, runner)
    books = _resolve_books(records, catalog)

    for _item, book_id, calibre_uuid, _sidecar_key, old_values, new_values in records:
        book = books[calibre_uuid.casefold()]
        for field, new_value in new_values.items():
            current = book.custom_values.get(field)
            if _equal(current, new_value):
                continue
            if not _equal(current, old_values[field]):
                raise CalibreApplyError("concurrent_change")
            _write_value(config.calibre_library, book_id, field, new_value, runner)

    _ensure_calibre_closed()
    verified_catalog = _catalog(config.calibre_library, runner)
    verified_books = {book.uuid.casefold(): book for book in verified_catalog}
    applied_at = datetime.now(UTC).isoformat(timespec="seconds")
    for _, book_id, calibre_uuid, _, _, new_values in records:
        verified_book = verified_books.get(calibre_uuid.casefold())
        if verified_book is None or verified_book.book_id != book_id:
            raise CalibreApplyError("calibre_book_changed")
        for field, expected in new_values.items():
            if not _equal(verified_book.custom_values.get(field), expected):
                raise CalibreApplyError("verification_failed")
    applied_fields = _checkpoint_records(state, preview_id, records, applied_at=applied_at)
    try:
        _check_calibre_integrity(config.calibre_library)
    except BackupError:
        raise CalibreApplyError("calibre_integrity_failed") from None
    return ApplyResult(backup.snapshot_id, len(records), applied_fields)


def apply_preview(
    config: AppConfig,
    state_db: Path,
    stage_root: Path,
    preview_id: str,
    restic: ResticOperations,
    *,
    runner: Runner = run_hidden,
) -> ApplyResult:
    """Back up, apply a preview, and verify it against unchanged staged sidecars."""
    return _apply_saved_preview(
        config,
        state_db,
        stage_root,
        preview_id,
        restic,
        runner=runner,
    )


def apply_persisted_projection(
    config: AppConfig,
    state_db: Path,
    projection_id: str,
    restic: ResticOperations,
    *,
    confirmed: bool = False,
    runner: Runner = run_hidden,
) -> ApplyResult:
    """Apply a reviewed projection from immutable local history after explicit approval."""
    if confirmed is not True:
        raise CalibreApplyError("apply_unconfirmed")
    return _apply_saved_preview(
        config,
        state_db,
        None,
        projection_id,
        restic,
        runner=runner,
    )
