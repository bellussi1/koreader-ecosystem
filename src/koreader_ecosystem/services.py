from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, TypeVar

from koreader_ecosystem.application_state import ApplicationState, ApplicationStateError
from koreader_ecosystem.backup import (
    BackupError,
    BackupResult,
    ResticOperations,
    RestoreResult,
    _check_calibre_integrity,
    _running_process_names,
    preflight_before_device_copy,
)
from koreader_ecosystem.backup import (
    create_backup as create_verified_backup,
)
from koreader_ecosystem.backup import (
    create_calibre_backup as create_library_backup,
)
from koreader_ecosystem.backup import (
    validate_restore as validate_snapshot_restore,
)
from koreader_ecosystem.backup_automation import (
    AutomationError,
    BackupAutomationSettings,
    ProcessOperationLock,
    TaskScheduler,
)
from koreader_ecosystem.calibre_apply import (
    ApplyResult,
    CalibreApplyError,
    apply_persisted_projection,
    apply_preview,
)
from koreader_ecosystem.calibre_catalog import (
    CalibreBook,
    CalibreCatalogError,
    load_calibre_catalog,
)
from koreader_ecosystem.calibre_library import (
    CalibreImportPreview,
    CalibreLibraryError,
    export_calibre_book_format,
    preview_calibre_import,
)
from koreader_ecosystem.calibre_library import (
    add_calibre_file as add_calibre_library_file,
)
from koreader_ecosystem.calibre_library import (
    edit_calibre_metadata as edit_library_metadata,
)
from koreader_ecosystem.calibre_library import (
    edit_calibre_tags_batch as edit_library_tags_batch,
)
from koreader_ecosystem.calibre_library import (
    remove_calibre_book as remove_calibre_library_book,
)
from koreader_ecosystem.config import (
    AppConfig,
    save_config,
    validate_s3_region,
    validate_s3_repository,
)
from koreader_ecosystem.hardcover import (
    HardcoverClient,
    HardcoverError,
    HardcoverRequestBudget,
)
from koreader_ecosystem.hardcover_models import (
    HardcoverLibraryEntry,
    HardcoverRead,
    deserialize_library_entry,
    serialize_library_entry,
)
from koreader_ecosystem.koreader import (
    PluginInstallError,
    PluginInstallResult,
    PluginUpdateResult,
    install_hardcover_with_secret,
    prepare_hardcover_plugin,
)
from koreader_ecosystem.koreader import (
    update_hardcover_plugin as update_plugin_mtp,
)
from koreader_ecosystem.koreader_calibre import (
    KoreaderCalibreError,
    PreviewResult,
    create_preview,
)
from koreader_ecosystem.koreader_calibre_report import render_preview
from koreader_ecosystem.koreader_reading_history import (
    ReadingHistoryError,
    ReadingHistoryPreview,
    preview_reading_history,
)
from koreader_ecosystem.mtp import (
    MtpAmbiguousError,
    MtpCopyError,
    MtpDeviceSummary,
    MtpError,
    MtpReadingStageSummary,
    MtpSendError,
    MtpSendResult,
    MtpStageSummary,
    MtpUnavailableError,
    detect_kindle,
    probe_kindle,
    send_kindle_file,
    stage_kindle,
    stage_koreader_reading_data,
)
from koreader_ecosystem.operation_history import OperationHistory, OperationHistoryError
from koreader_ecosystem.platform_csv import (
    MAX_CSV_BYTES,
    CsvReadingRecord,
    ReadingCsvError,
    csv_reading_record_from_mapping,
    export_goodreads_csv,
    inspect_reading_csv,
    parse_reading_csv,
)
from koreader_ecosystem.platform_csv_state import (
    PlatformCsvImportBatch,
    PlatformCsvImportPage,
    PlatformCsvStore,
    PlatformCsvStoreError,
    StoredCsvEntryPage,
)
from koreader_ecosystem.profile_recovery import (
    ProfileRecoveryError,
    mark_verified_restore,
)
from koreader_ecosystem.profile_recovery import (
    apply_profile_recovery as install_recovered_profile,
)
from koreader_ecosystem.profile_recovery import (
    preview_profile_recovery as inspect_restored_profile,
)
from koreader_ecosystem.reading_history_projection import (
    CalibreReadingProjectionPreview,
    ReadingHistoryProjectionError,
)
from koreader_ecosystem.reading_history_projection import (
    preview_calibre_reading_projection as build_calibre_reading_projection,
)
from koreader_ecosystem.reading_history_state import (
    KoreaderReadingHistoryStore,
    ReadingHistoryStateError,
    ReadingImportBatch,
    StoredPageEventPage,
)
from koreader_ecosystem.remote_backup import RemoteSyncResult
from koreader_ecosystem.remote_backup import sync_remote as mirror_remote
from koreader_ecosystem.restic import ResticClient, ResticError, ResticSnapshot
from koreader_ecosystem.safety import SafetyViolation, remove_tree, require_calibre_closed
from koreader_ecosystem.secrets import (
    SecretStoreError,
    hardcover_token_is_stored,
    load_hardcover_token,
    load_s3_credentials,
    restic_password_is_stored,
    save_hardcover_token,
    save_restic_password,
    save_s3_credentials,
)
from koreader_ecosystem.sync_persistence import (
    OutboxOperation,
    OutboxReviewResolution,
    RemoteCacheStore,
    SyncOutboxStore,
    SyncPersistenceError,
)
from koreader_ecosystem.sync_state import SyncState, SyncStateError


class ServiceError(RuntimeError):
    """A safe, user-facing application service failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


_MutationResult = TypeVar("_MutationResult")


@dataclass(frozen=True, slots=True)
class HardcoverPluginUpdateResult:
    restic_snapshot_id: str
    rollback_backup_id: str | None
    file_count: int
    total_bytes: int
    updated: bool


@dataclass(frozen=True, slots=True)
class KindleTransferResult:
    backup_snapshot_id: str
    format: str
    filename: str
    size: int
    sha256: str
    verified: bool
    already_present: bool


@dataclass(frozen=True, slots=True)
class KoreaderReadingHistoryImportPreview:
    batch: ReadingImportBatch
    preview: ReadingHistoryPreview


@dataclass(frozen=True, slots=True)
class PlatformCsvImportPreview:
    batch: PlatformCsvImportBatch
    record_count: int
    issue_count: int
    warning_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class HardcoverBooksPage:
    books: tuple[HardcoverLibraryEntry, ...]
    source: Literal["live", "cache"]
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class HardcoverCurrentReading:
    book: HardcoverLibraryEntry | None
    source: Literal["live", "cache"]
    fetched_at: datetime


_CACHE_FALLBACK_ERRORS = frozenset(
    {"remote_unavailable", "rate_limited", "daily_request_budget_reached", "local_rate_limit"}
)
_UNSENT_HARDCOVER_OPERATIONS = frozenset(
    {"start_read", "finish_read", "update_rating", "add_journal_entry"}
)
_IDEMPOTENT_HARDCOVER_OPERATIONS = frozenset({"finish_read", "update_rating"})
_MAX_HARDCOVER_RECONCILIATION_BATCH = 4


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _nonnegative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _optional_hardcover_rating(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 5:
        return float(value)
    return None


def _hardcover_action_is_safe_to_retry(operation: OutboxOperation) -> bool:
    return (
        operation.status == "pending"
        and operation.attempts == 0
        and operation.operation in _UNSENT_HARDCOVER_OPERATIONS
    ) or (
        operation.status == "reconcile" and operation.operation in _IDEMPOTENT_HARDCOVER_OPERATIONS
    )


class ApplicationServices:
    """Shared operations used by the CLI and Windows desktop interface."""

    def __init__(
        self,
        config: AppConfig,
        restic: ResticOperations | None = None,
        hardcover: HardcoverClient | None = None,
    ) -> None:
        self.config = config
        self._daily_task_checked = False
        self._daily_task_needs_reactivation = False
        self.restic: ResticOperations = restic or ResticClient(config.restic_repository)
        self.hardcover = hardcover or HardcoverClient()
        self.application_state_database = self._application_state_database()
        try:
            application_state = ApplicationState(self.application_state_database)
            self.remote_cache = RemoteCacheStore(application_state)
            self.sync_outbox = SyncOutboxStore(application_state)
            self.platform_csv = PlatformCsvStore(application_state)
            self.sync_outbox.recover_interrupted()
        except SyncPersistenceError:
            raise ServiceError("application_state_unavailable") from None
        try:
            self.operation_history = OperationHistory(
                self.application_state_database,
                legacy_path=config.state_root / "operation-history.json",
            )
        except OperationHistoryError:
            raise ServiceError("application_state_unavailable") from None
        if isinstance(self.hardcover, HardcoverClient):
            self.hardcover.set_request_budget(
                HardcoverRequestBudget(self.application_state_database)
            )
        # v2 deliberately ignores credentials accepted before local replacement was required.
        self._hardcover_ready_marker = config.state_root / "credentials" / "hardcover-ready-v2"

    def update_local_paths(self, *, calibre_library: Path, restic_repository: Path) -> None:
        if not calibre_library.is_absolute() or not restic_repository.is_absolute():
            raise ServiceError("invalid_configuration")
        updated = replace(
            self.config,
            calibre_library=calibre_library.expanduser().resolve(),
            restic_repository=restic_repository.expanduser().resolve(),
        )
        save_config(updated)
        self.config = updated

    def configure_restic_password(self, password: str) -> None:
        repository = self.config.restic_repository
        if repository.exists():
            try:
                if not repository.is_dir():
                    raise ServiceError("restic_repository_unavailable")
                has_repository_data = next(repository.iterdir(), None) is not None
            except OSError:
                raise ServiceError("restic_repository_unavailable") from None
            if has_repository_data:
                candidate = ResticClient(repository, password_provider=lambda: password)
                if not candidate.available:
                    raise ServiceError("restic_repository_unavailable")
                try:
                    result = candidate.run(["snapshots", "--json"])
                except ResticError:
                    raise ServiceError("restic_password_invalid") from None
                if not result.ok:
                    raise ServiceError("restic_password_invalid")
        save_restic_password(password)

    @property
    def hardcover_ready(self) -> bool:
        return self._hardcover_token_available()

    def inspect_platform_csv(self, path: Path, *, source_name: str) -> dict[str, object]:
        """Return safe CSV headers and mapping suggestions without exposing row values."""
        if not isinstance(source_name, str) or source_name not in {
            "goodreads",
            "storygraph",
            "maratona",
        }:
            raise ServiceError("platform_csv_invalid_source")
        source_path = _validate_platform_csv_path(path, require_existing=True)
        if source_path.suffix.casefold() != ".csv":
            raise ServiceError("platform_csv_invalid_file")
        try:
            with source_path.open("rb") as source_file:
                content = source_file.read(MAX_CSV_BYTES + 1)
        except OSError:
            raise ServiceError("platform_csv_file_unavailable") from None
        if len(content) > MAX_CSV_BYTES:
            raise ServiceError("platform_csv_file_too_large")
        try:
            draft = inspect_reading_csv(content, source=source_name)
        except ReadingCsvError as error:
            if error.reason_code == "file_too_large":
                raise ServiceError("platform_csv_file_too_large") from None
            raise ServiceError("platform_csv_invalid_file") from None
        return {
            "source": draft.source,
            "source_digest": draft.source_digest,
            "headers": list(draft.headers),
            "suggested_mapping": draft.suggested_mapping,
        }

    def preview_platform_csv(
        self,
        path: Path,
        *,
        source_name: str,
        column_mapping: Mapping[str, str | None] | None = None,
        expected_digest: str | None = None,
    ) -> PlatformCsvImportPreview:
        """Validate a user-selected CSV and persist an inactive local preview."""
        if not isinstance(source_name, str) or source_name not in {
            "goodreads",
            "storygraph",
            "maratona",
        }:
            raise ServiceError("platform_csv_invalid_source")
        source_path = _validate_platform_csv_path(path, require_existing=True)
        if source_path.suffix.casefold() != ".csv":
            raise ServiceError("platform_csv_invalid_file")
        try:
            with source_path.open("rb") as source_file:
                content = source_file.read(MAX_CSV_BYTES + 1)
        except OSError:
            raise ServiceError("platform_csv_file_unavailable") from None
        if len(content) > MAX_CSV_BYTES:
            raise ServiceError("platform_csv_file_too_large")
        actual_digest = hashlib.sha256(content).hexdigest()
        if expected_digest is not None and expected_digest != actual_digest:
            raise ServiceError("platform_csv_source_changed")
        try:
            preview = parse_reading_csv(
                content,
                source=source_name,
                column_mapping=column_mapping,
            )
            batch = self.platform_csv.stage_preview(preview)
        except ReadingCsvError as error:
            if error.reason_code == "file_too_large":
                raise ServiceError("platform_csv_file_too_large") from None
            if error.reason_code == "invalid_column_mapping":
                raise ServiceError("platform_csv_invalid_mapping") from None
            raise ServiceError("platform_csv_invalid_file") from None
        except PlatformCsvStoreError as error:
            raise _platform_csv_store_error(error.reason_code) from None
        warning_counts = Counter(
            warning for record in preview.records for warning in record.warnings
        )
        return PlatformCsvImportPreview(
            batch=batch,
            record_count=len(preview.records),
            issue_count=len(preview.issues),
            warning_counts=dict(sorted(warning_counts.items())),
        )

    def list_platform_csv_imports(
        self, *, offset: int = 0, limit: int = 100, source_name: str | None = None
    ) -> PlatformCsvImportPage:
        try:
            return self.platform_csv.list_imports(
                offset=offset,
                limit=limit,
                source=source_name,
            )
        except PlatformCsvStoreError as error:
            raise _platform_csv_store_error(error.reason_code) from None

    def get_platform_csv_preview_page(
        self, import_id: str, *, offset: int = 0, limit: int = 100
    ) -> StoredCsvEntryPage:
        try:
            return self.platform_csv.get_preview_page(import_id, offset=offset, limit=limit)
        except PlatformCsvStoreError as error:
            raise _platform_csv_store_error(error.reason_code) from None

    def confirm_platform_csv_import(
        self, import_id: str, source_digest: str, *, confirmed: bool
    ) -> PlatformCsvImportBatch:
        try:
            return self.platform_csv.confirm_import(
                import_id,
                source_digest,
                confirmed=confirmed,
            )
        except PlatformCsvStoreError as error:
            raise _platform_csv_store_error(error.reason_code) from None

    def export_platform_csv(
        self,
        import_id: str,
        destination: Path,
        *,
        include_reviews: bool,
        confirmed: bool,
    ) -> dict[str, object]:
        """Export one confirmed import with an explicit save path and review choice."""
        if confirmed is not True or not isinstance(include_reviews, bool):
            raise ServiceError("platform_csv_confirmation_required")
        try:
            batch = self.platform_csv.get_import(import_id)
        except PlatformCsvStoreError as error:
            raise _platform_csv_store_error(error.reason_code) from None
        if batch.status != "applied":
            raise ServiceError("platform_csv_import_not_applied")

        records: list[CsvReadingRecord] = []
        offset = 0
        while True:
            try:
                page = self.platform_csv.get_applied_records(
                    import_id,
                    offset=offset,
                    limit=100,
                )
                records.extend(csv_reading_record_from_mapping(record) for record in page.records)
            except PlatformCsvStoreError as error:
                raise _platform_csv_store_error(error.reason_code) from None
            except ReadingCsvError:
                raise ServiceError("platform_csv_stored_data_invalid") from None
            if not page.has_more:
                break
            offset += len(page.records)

        try:
            exported = export_goodreads_csv(records, include_reviews=include_reviews)
        except ReadingCsvError as error:
            if error.reason_code in {"file_too_large", "too_many_rows"}:
                raise ServiceError("platform_csv_export_too_large") from None
            raise ServiceError("platform_csv_stored_data_invalid") from None
        output_path = _validate_platform_csv_path(destination, require_existing=False)
        if output_path.suffix.casefold() != ".csv":
            raise ServiceError("platform_csv_invalid_file")
        try:
            _write_platform_csv_atomically(output_path, exported.content)
        except OSError:
            raise ServiceError("platform_csv_export_failed") from None
        return {
            "file_name": output_path.name,
            "bytes_written": len(exported.content),
            "rows_exported": exported.rows_exported,
            "formula_escaped_cells": exported.formula_escaped_cells,
            "reviews_included": exported.reviews_included,
            "unexported_fields": exported.unexported_fields,
            "warnings": exported.warnings,
        }

    def doctor(self) -> dict[str, Any]:
        checks: dict[str, Any] = {}

        calibre_ok = False
        try:
            require_calibre_closed(_running_process_names())
            _check_calibre_integrity(self.config.calibre_library)
            calibre_ok = True
        except BackupError, SafetyViolation:
            pass
        checks["calibre"] = {"status": "ok" if calibre_ok else "error"}

        try:
            kindle = detect_kindle()
            kindle_check: dict[str, Any] = {
                "status": "ok",
                "roots": kindle.roots,
                "file_count": kindle.file_count,
                "directory_count": kindle.directory_count,
                "total_bytes": kindle.total_bytes,
            }
        except MtpAmbiguousError:
            kindle_check = {"status": "error", "reason_code": "multiple_devices"}
        except MtpUnavailableError:
            kindle_check = {"status": "error", "reason_code": "kindle_unavailable"}
        except MtpError:
            kindle_check = {"status": "error", "reason_code": "inventory_failed"}
        checks["kindle"] = kindle_check

        restic_available = bool(getattr(self.restic, "available", True))
        checks["restic"] = {"status": "ok" if restic_available else "error"}

        credential_secure = False
        password_stored = False
        try:
            password_stored = restic_password_is_stored()
            credential_secure = True
        except SecretStoreError:
            pass
        checks["credentials"] = {
            "status": "ok" if credential_secure and password_stored else "needs_attention",
            "windows_credential_manager": credential_secure,
            "restic_password_stored": password_stored,
            "hardcover_token_stored": self._hardcover_token_available(),
        }

        free_bytes = 0
        try:
            probe = self.config.restic_repository
            while not probe.exists() and probe != probe.parent:
                probe = probe.parent
            free_bytes = shutil.disk_usage(probe).free
        except OSError:
            pass
        checks["backup_space"] = {
            "status": "ok" if free_bytes >= 64 * 1024 * 1024 else "error",
            "available_bytes": free_bytes,
        }

        all_ok = (
            calibre_ok
            and kindle_check["status"] == "ok"
            and restic_available
            and credential_secure
            and password_stored
            and free_bytes >= 64 * 1024 * 1024
        )
        return {"status": "ok" if all_ok else "needs_attention", "checks": checks}

    def status(self) -> tuple[dict[str, str], ...]:
        snapshots = self._list_restic_snapshots(allow_missing_repository=False)
        return tuple({"id": snapshot.id, "time": snapshot.time} for snapshot in snapshots)

    def backup_snapshots(self) -> tuple[dict[str, str | bool], ...]:
        snapshots = self._list_restic_snapshots(allow_missing_repository=True)
        return tuple(
            {
                "id": snapshot.id,
                "time": snapshot.time,
                "scope": (
                    "calibre_only"
                    if "calibre-only" in snapshot.tags
                    else "calibre_and_kindle"
                    if {"koreader-ecosystem", "kindle-reading-ecosystem"}.intersection(
                        snapshot.tags
                    )
                    else "unknown"
                ),
                "includes_app_state": "application-state-v1" in snapshot.tags,
            }
            for snapshot in snapshots
        )

    def _list_restic_snapshots(
        self, *, allow_missing_repository: bool
    ) -> tuple[ResticSnapshot, ...]:
        try:
            if not restic_password_is_stored():
                raise ServiceError("restic_password_unavailable")
        except SecretStoreError:
            raise ServiceError("restic_password_unavailable") from None
        if getattr(self.restic, "available", True) is False:
            raise ServiceError("restic_repository_unavailable")
        if allow_missing_repository and not self.config.restic_repository.exists():
            return ()
        result = self.restic.run(["snapshots", "--json"])
        if not result.ok:
            raise ServiceError("restic_repository_unavailable")
        return result.snapshots

    def inspect_kindle(self) -> MtpDeviceSummary:
        return detect_kindle()

    def preview_calibre_import(self, files: tuple[str, ...]) -> tuple[CalibreImportPreview, ...]:
        return preview_calibre_import(self.config.calibre_library, files)

    def add_calibre_file(
        self,
        file: str,
        *,
        mode: str,
        target_book_id: int | None = None,
        recovery_copy_confirmed: bool = False,
    ) -> CalibreBook:
        if recovery_copy_confirmed is not True:
            raise ServiceError("recovery_copy_unconfirmed")
        with self._operation_lock():
            self._backup_calibre_before_write()
            return add_calibre_library_file(
                self.config.calibre_library,
                file,
                mode=mode,
                target_book_id=target_book_id,
            )

    def edit_calibre_metadata(
        self,
        *,
        book_id: int,
        changes: dict[str, object],
        cover_path: str | None = None,
        recovery_copy_confirmed: bool = False,
    ) -> CalibreBook:
        if recovery_copy_confirmed is not True:
            raise ServiceError("recovery_copy_unconfirmed")
        with self._operation_lock():
            self._backup_calibre_before_write()
            return edit_library_metadata(
                self.config.calibre_library,
                book_id,
                changes,
                cover_path=cover_path,
            )

    def edit_calibre_tags_batch(
        self,
        *,
        book_ids: tuple[int, ...],
        add_tags: tuple[str, ...],
        remove_tags: tuple[str, ...],
        recovery_copy_confirmed: bool = False,
    ) -> tuple[CalibreBook, ...]:
        if recovery_copy_confirmed is not True:
            raise ServiceError("recovery_copy_unconfirmed")
        with self._operation_lock():
            self._backup_calibre_before_write()
            return edit_library_tags_batch(
                self.config.calibre_library,
                book_ids,
                add_tags=add_tags,
                remove_tags=remove_tags,
            )

    def remove_calibre_book(
        self,
        book_id: int,
        *,
        recovery_copy_confirmed: bool = False,
    ) -> None:
        if recovery_copy_confirmed is not True:
            raise ServiceError("recovery_copy_unconfirmed")
        with self._operation_lock():
            try:
                backup = self._create_backup_with_history_locked(
                    calibre_only=True,
                    verify_restore=True,
                )
            except BackupError:
                raise ServiceError("backup_unavailable") from None
            if backup.restore_verified is not True:
                raise ServiceError("calibre_remove_backup_unverified")
            remove_calibre_library_book(self.config.calibre_library, book_id)

    def kindle_presence(self) -> dict[str, object]:
        try:
            presence = probe_kindle()
        except MtpAmbiguousError:
            return {"connected": False, "reason_code": "multiple_devices"}
        except MtpUnavailableError:
            return {"connected": False, "reason_code": "kindle_unavailable"}
        except MtpError:
            return {"connected": False, "reason_code": "inventory_failed"}
        return {"connected": presence.connected, "reason_code": presence.reason_code}

    def send_calibre_book_to_kindle(
        self,
        *,
        book_id: int,
        format_name: str,
        filename: str,
        recovery_copy_confirmed: bool = False,
    ) -> KindleTransferResult:
        """Back up and verify Calibre + Kindle before sending one reviewed book."""
        if recovery_copy_confirmed is not True:
            raise ServiceError("recovery_copy_unconfirmed")
        with self._operation_lock():
            return self._send_calibre_book_to_kindle_locked(
                book_id=book_id,
                format_name=format_name,
                filename=filename,
                recovery_copy_confirmed=True,
            )

    def _send_calibre_book_to_kindle_locked(
        self,
        *,
        book_id: int,
        format_name: str,
        filename: str,
        recovery_copy_confirmed: bool,
    ) -> KindleTransferResult:
        if recovery_copy_confirmed is not True:
            raise ServiceError("recovery_copy_unconfirmed")
        try:
            backup = self.create_backup(verify_restore=True, recovery_copy_confirmed=True)
        except BackupError:
            raise ServiceError("backup_unavailable") from None
        except MtpAmbiguousError:
            raise ServiceError("multiple_devices") from None
        except MtpUnavailableError:
            raise ServiceError("device_not_found") from None
        except MtpCopyError:
            raise ServiceError("backup_unavailable") from None
        except MtpError:
            raise ServiceError("device_not_found") from None
        if backup.restore_verified is not True:
            raise ServiceError("kindle_send_backup_unverified")

        stage_parent = self.config.state_root / "staging"
        try:
            stage_parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise ServiceError("staging_unavailable") from None
        try:
            with tempfile.TemporaryDirectory(prefix="kre-send-", dir=stage_parent) as temporary:
                exported = export_calibre_book_format(
                    self.config.calibre_library,
                    book_id,
                    format_name,
                    Path(temporary) / "export",
                )
                try:
                    sent: MtpSendResult = send_kindle_file(exported, filename)
                except MtpSendError as error:
                    if error.reason_code == "content_mismatch":
                        raise ServiceError("content_mismatch") from None
                    raise ServiceError(
                        "kindle_send_uncertain" if error.copied else error.reason_code
                    ) from None
        except CalibreLibraryError as error:
            raise ServiceError(error.reason_code) from None
        except SafetyViolation:
            raise ServiceError("calibre_open") from None
        except MtpSendError as error:
            raise ServiceError(error.reason_code) from None
        except OSError:
            raise ServiceError("staging_unavailable") from None
        return KindleTransferResult(
            backup_snapshot_id=backup.snapshot_id,
            format=format_name.upper(),
            filename=sent.filename,
            size=sent.size,
            sha256=sent.sha256,
            verified=sent.verified,
            already_present=sent.already_present,
        )

    @contextmanager
    def _operation_lock(self) -> Iterator[None]:
        try:
            with ProcessOperationLock(self.config.state_root):
                yield
        except AutomationError as error:
            raise ServiceError(str(error)) from None

    def _backup_calibre_before_write(self) -> BackupResult:
        try:
            require_calibre_closed(_running_process_names())
        except SafetyViolation:
            raise ServiceError("calibre_open") from None
        except BackupError:
            raise ServiceError("backup_unavailable") from None
        try:
            return self._create_backup_with_history_locked(
                calibre_only=True,
                verify_restore=False,
            )
        except BackupError:
            raise ServiceError("backup_unavailable") from None

    def create_backup(
        self,
        *,
        calibre_only: bool = False,
        verify_restore: bool = False,
        recovery_copy_confirmed: bool = False,
    ) -> BackupResult:
        if recovery_copy_confirmed is not True:
            raise ServiceError("recovery_copy_unconfirmed")
        with self._operation_lock():
            return self._create_backup_with_history_locked(
                calibre_only=calibre_only,
                verify_restore=verify_restore,
            )

    def _create_backup_with_history_locked(
        self, *, calibre_only: bool, verify_restore: bool
    ) -> BackupResult:
        self.operation_history.recover_interrupted_backup()
        self.operation_history.record(key="local_backup", status="running")
        try:
            result = self._create_backup_locked(
                calibre_only=calibre_only,
                verify_restore=verify_restore,
            )
        except Exception as error:
            reason_code = getattr(error, "reason_code", "backup_unavailable")
            if not isinstance(reason_code, str) or not re.fullmatch(
                r"[a-z0-9_]{1,64}", reason_code
            ):
                reason_code = "backup_unavailable"
            self.operation_history.record(
                key="local_backup",
                status="failed",
                reason_code=reason_code,
            )
            raise
        self.operation_history.record(key="local_backup", status="success")
        return result

    def _create_backup_locked(self, *, calibre_only: bool, verify_restore: bool) -> BackupResult:
        if calibre_only:
            if verify_restore:
                return create_library_backup(
                    self.config,
                    self.restic,
                    verify_restore=True,
                )
            return create_library_backup(
                self.config,
                self.restic,
            )

        preflight_before_device_copy(self.config, self.restic)
        stage_path = self._new_stage_path()
        device_stage: MtpStageSummary | None = None
        try:
            device_stage = stage_kindle(stage_path)
            self._remove_stage_residuals(device_stage)
            return create_verified_backup(
                self.config,
                device_stage,
                self.restic,
                verify_restore=verify_restore,
            )
        finally:
            self._remove_stage_result(stage_path, device_stage)

    def backup_automation_status(self) -> dict[str, object]:
        if self.config.daily_backup_enabled and not self._daily_task_checked:
            self._daily_task_checked = True
            try:
                executable = os.environ.get("KOREADER_ECOSYSTEM_SCHEDULER_EXECUTABLE")
                if not executable or not restic_password_is_stored():
                    raise AutomationError("scheduled_task_unavailable")
                TaskScheduler(executable=Path(executable)).register_daily_backup(
                    self.config.daily_backup_time
                )
                self._daily_task_needs_reactivation = False
            except AutomationError, SecretStoreError:
                self._daily_task_needs_reactivation = True
        settings = BackupAutomationSettings(
            daily_backup_enabled=self.config.daily_backup_enabled,
            daily_backup_time=self.config.daily_backup_time,
            backup_on_kindle_connect=self.config.backup_on_kindle_connect,
        )
        record = self.operation_history.get("local_backup")
        last_backup = None
        if record is not None:
            last_backup = {
                "status": record.status,
                "updated_at": record.updated_at,
                "last_success_at": record.last_success_at,
                "reason_code": record.reason_code,
            }
        return {
            **settings.to_dict(),
            "last_backup": last_backup,
            "daily_backup_needs_reactivation": self._daily_task_needs_reactivation,
        }

    def configure_backup_automation(
        self,
        *,
        daily_backup_enabled: bool,
        daily_backup_time: str,
        backup_on_kindle_connect: bool,
    ) -> dict[str, object]:
        try:
            settings = BackupAutomationSettings(
                daily_backup_enabled=daily_backup_enabled,
                daily_backup_time=daily_backup_time,
                backup_on_kindle_connect=backup_on_kindle_connect,
            )
        except AutomationError as error:
            raise ServiceError(str(error)) from None

        if settings.daily_backup_enabled or settings.backup_on_kindle_connect:
            try:
                password_available = restic_password_is_stored()
            except SecretStoreError:
                password_available = False
            if not password_available:
                raise ServiceError("restic_password_unavailable")

        previous = self.config
        needs_task_update = (settings.daily_backup_enabled or previous.daily_backup_enabled) and (
            settings.daily_backup_enabled != previous.daily_backup_enabled
            or settings.daily_backup_time != previous.daily_backup_time
            or self._daily_task_needs_reactivation
            or (settings.daily_backup_enabled and not self._daily_task_checked)
        )
        task_scheduler: TaskScheduler | None = None
        if needs_task_update:
            scheduler_executable = os.environ.get("KOREADER_ECOSYSTEM_SCHEDULER_EXECUTABLE")
            if not scheduler_executable:
                raise ServiceError("scheduled_task_unavailable")
            try:
                task_scheduler = TaskScheduler(executable=Path(scheduler_executable))
                if settings.daily_backup_enabled:
                    task_scheduler.register_daily_backup(settings.daily_backup_time)
                else:
                    task_scheduler.remove_daily_backup()
            except AutomationError as error:
                raise ServiceError(str(error)) from None

        updated = replace(
            previous,
            daily_backup_enabled=settings.daily_backup_enabled,
            daily_backup_time=settings.daily_backup_time,
            backup_on_kindle_connect=settings.backup_on_kindle_connect,
        )
        try:
            save_config(updated)
        except Exception:
            if task_scheduler is not None:
                try:
                    if previous.daily_backup_enabled:
                        task_scheduler.register_daily_backup(previous.daily_backup_time)
                    else:
                        task_scheduler.remove_daily_backup()
                except AutomationError:
                    pass
            raise ServiceError("configuration_unavailable") from None
        self.config = updated
        self._daily_task_checked = True
        self._daily_task_needs_reactivation = False
        return self.backup_automation_status()

    def run_scheduled_backup(self) -> BackupResult:
        if not self.config.daily_backup_enabled:
            raise ServiceError("scheduled_backup_disabled")
        try:
            with ProcessOperationLock(self.config.state_root):
                return self._create_backup_with_history_locked(
                    calibre_only=True,
                    verify_restore=False,
                )
        except AutomationError as error:
            raise ServiceError(str(error)) from None

    def validate_restore(self, snapshot: str, target: Path) -> RestoreResult:
        sources = tuple(
            path
            for path in (self.config.calibre_library, self.config.restic_repository)
            if path.exists()
        )
        result = validate_snapshot_restore(snapshot, target, self.restic, sources=sources)
        try:
            mark_verified_restore(
                target,
                snapshot_id=result.snapshot_id,
                file_count=result.file_count,
                total_bytes=result.total_bytes,
            )
        except ProfileRecoveryError as error:
            raise ServiceError(error.reason_code) from None
        return result

    def preview_profile_recovery(self, restore_root: Path) -> dict[str, object]:
        try:
            return inspect_restored_profile(restore_root).to_dict()
        except ProfileRecoveryError as error:
            raise ServiceError(error.reason_code) from None

    def apply_profile_recovery(
        self,
        restore_root: Path,
        expected_fingerprint: str,
        *,
        confirmed: bool,
    ) -> dict[str, object]:
        with self._operation_lock():
            try:
                result = install_recovered_profile(
                    restore_root,
                    self.config.state_root,
                    expected_fingerprint,
                    active_config=self.config,
                    confirmed=confirmed,
                )
            except ProfileRecoveryError as error:
                raise ServiceError(error.reason_code) from None
            return {
                "checkpoint_id": result.checkpoint_id,
                "schema_version": result.schema_version,
                "requires_restart": result.requires_restart,
            }

    def preview_koreader_calibre(self) -> tuple[PreviewResult, Path]:
        try:
            require_calibre_closed(_running_process_names())
            _check_calibre_integrity(self.config.calibre_library)
        except BackupError, SafetyViolation:
            raise
        stage_path = self._new_stage_path()
        staged: MtpReadingStageSummary | None = None
        try:
            staged = stage_koreader_reading_data(stage_path)
            self._remove_stage_residuals(staged)
            catalog = load_calibre_catalog(self.config.calibre_library)
            preview = create_preview(
                stage_path,
                catalog,
                self._application_state_database(),
            )
            report_path = (
                self.config.state_root / "reports" / f"koreader-calibre-{preview.preview_id}.html"
            )
            render_preview(preview, report_path)
            return preview, report_path
        finally:
            self._remove_stage_result(stage_path, staged)

    def map_koreader_sidecar(self, preview_id: str, sidecar_key: str, book_id: int) -> None:
        state = SyncState(self._application_state_database())
        saved = state.load_preview(preview_id)
        items = saved.get("items")
        if not isinstance(items, list):
            raise KoreaderCalibreError("Saved preview is invalid", reason_code="state_error")
        if not any(
            isinstance(item, dict) and item.get("sidecar_key") == sidecar_key for item in items
        ):
            raise KoreaderCalibreError(
                "Sidecar is not part of the saved preview", reason_code="sidecar_not_in_preview"
            )
        catalog = load_calibre_catalog(self.config.calibre_library)
        if not any(book.book_id == book_id for book in catalog):
            raise KoreaderCalibreError(
                "Calibre book ID does not exist", reason_code="manual_target_missing"
            )
        existing = state.manual_mappings()
        if any(key != sidecar_key and target == book_id for key, target in existing.items()):
            raise KoreaderCalibreError(
                "Another sidecar already maps to this Calibre ID",
                reason_code="duplicate_manual_target",
            )
        state.save_manual_mapping(sidecar_key, book_id)

    def apply_koreader_preview(self, preview_id: str, *, confirmed: bool = False) -> ApplyResult:
        if confirmed is not True:
            raise ServiceError("apply_unconfirmed")
        with self._operation_lock():
            return self._apply_koreader_preview_locked(preview_id)

    def _apply_koreader_preview_locked(self, preview_id: str) -> ApplyResult:
        try:
            require_calibre_closed(_running_process_names())
            _check_calibre_integrity(self.config.calibre_library)
        except BackupError, SafetyViolation:
            raise
        stage_path = self._new_stage_path()
        staged: MtpReadingStageSummary | None = None
        try:
            staged = stage_koreader_reading_data(stage_path)
            self._remove_stage_residuals(staged)
            return apply_preview(
                self.config,
                self._application_state_database(),
                stage_path,
                preview_id,
                self.restic,
            )
        finally:
            self._remove_stage_result(stage_path, staged)

    def preview_koreader_reading_history(self) -> KoreaderReadingHistoryImportPreview:
        """Stage Kindle history read-only, parse it, and save an inactive local preview."""
        stage_path = self._new_stage_path()
        staged: MtpReadingStageSummary | None = None
        try:
            staged = stage_koreader_reading_data(stage_path)
            self._remove_stage_residuals(staged)
            preview = preview_reading_history(staged.destination, observed_at=datetime.now(UTC))
            batch = KoreaderReadingHistoryStore(
                ApplicationState(self.application_state_database)
            ).stage_preview(preview)
            return KoreaderReadingHistoryImportPreview(batch=batch, preview=preview)
        except MtpAmbiguousError:
            raise ServiceError("kindle_multiple_devices") from None
        except MtpUnavailableError:
            raise ServiceError("kindle_unavailable") from None
        except MtpError as exc:
            reason_code = getattr(exc, "reason_code", "kindle_copy_failed")
            raise ServiceError(str(reason_code)) from None
        except ReadingHistoryError as exc:
            raise ServiceError(f"koreader_{exc.reason_code}") from None
        except ReadingHistoryStateError as exc:
            raise ServiceError(exc.reason_code) from None
        except ApplicationStateError:
            raise ServiceError("application_state_unavailable") from None
        finally:
            self._remove_stage_result(stage_path, staged)

    def confirm_koreader_reading_history(
        self,
        import_id: str,
        source_digest: str,
        *,
        confirmed: bool = False,
    ) -> ReadingImportBatch:
        if confirmed is not True:
            raise ServiceError("import_unconfirmed")
        try:
            return KoreaderReadingHistoryStore(
                ApplicationState(self.application_state_database)
            ).confirm_import(import_id, source_digest)
        except (ApplicationStateError, ReadingHistoryStateError) as exc:
            if isinstance(exc, ReadingHistoryStateError):
                raise ServiceError(exc.reason_code) from None
            raise ServiceError("application_state_unavailable") from None

    def get_koreader_reading_events(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> StoredPageEventPage:
        try:
            return KoreaderReadingHistoryStore(
                ApplicationState(self.application_state_database)
            ).get_events_page(limit=limit, offset=offset)
        except ReadingHistoryStateError as exc:
            raise ServiceError(exc.reason_code) from None
        except ApplicationStateError:
            raise ServiceError("application_state_unavailable") from None

    def map_koreader_reading_book(
        self,
        import_id: str,
        koreader_book_id: int,
        calibre_book_id: int,
        *,
        confirmed: bool = False,
    ) -> None:
        if confirmed is not True:
            raise ServiceError("mapping_unconfirmed")
        if (
            not isinstance(import_id, str)
            or len(import_id) != 64
            or any(character not in "0123456789abcdef" for character in import_id)
            or isinstance(koreader_book_id, bool)
            or not isinstance(koreader_book_id, int)
            or koreader_book_id < 1
            or isinstance(calibre_book_id, bool)
            or not isinstance(calibre_book_id, int)
            or calibre_book_id < 1
        ):
            raise ServiceError("invalid_mapping")
        try:
            require_calibre_closed(_running_process_names())
            _check_calibre_integrity(self.config.calibre_library)
            catalog = load_calibre_catalog(self.config.calibre_library)
        except SafetyViolation:
            raise ServiceError("calibre_open") from None
        except BackupError:
            raise ServiceError("calibre_integrity_failed") from None
        except CalibreCatalogError:
            raise ServiceError("calibre_catalog_unavailable") from None
        calibre_book = next(
            (book for book in catalog if book.book_id == calibre_book_id),
            None,
        )
        if calibre_book is None:
            raise ServiceError("manual_target_missing")

        try:
            state = ApplicationState(self.application_state_database)
            with state.connect() as connection:
                batch = connection.execute(
                    "SELECT status, source FROM import_batches WHERE import_id=?",
                    (import_id,),
                ).fetchone()
                if batch is None or batch["source"] != "koreader":
                    raise ServiceError("import_not_found")
                if batch["status"] not in {"preview", "applied"}:
                    raise ServiceError("import_not_pending")
                source_identity = connection.execute(
                    """
                    SELECT identity_id FROM book_identities
                    WHERE provider='koreader' AND external_type='book_id' AND external_id=?
                    """,
                    (str(koreader_book_id),),
                ).fetchone()
                snapshot = connection.execute(
                    """
                    SELECT 1 FROM reading_events
                    WHERE import_id=? AND source='koreader' AND event_type='book_snapshot'
                      AND source_event_id LIKE ?
                    LIMIT 1
                    """,
                    (import_id, f"book:{koreader_book_id}:%"),
                ).fetchone()
                if source_identity is None or snapshot is None:
                    raise ServiceError("reading_book_not_in_import")
                source_identity_id = str(source_identity[0])
                existing = connection.execute(
                    """
                    SELECT right_identity_id FROM identity_links
                    WHERE left_identity_id=? AND relation='same_edition'
                    """,
                    (source_identity_id,),
                ).fetchall()
                target_rows = connection.execute(
                    """
                    SELECT identity_id FROM book_identities
                    WHERE provider='calibre' AND external_type='book_uuid' AND external_id=?
                    """,
                    (calibre_book.uuid,),
                ).fetchall()
                target_identity_id = (
                    str(target_rows[0][0])
                    if target_rows
                    else hashlib.sha256(
                        f"calibre\x00book_uuid\x00{calibre_book.uuid}".encode()
                    ).hexdigest()
                )
                if existing:
                    if len(existing) == 1 and str(existing[0][0]) == target_identity_id:
                        return
                    raise ServiceError("mapping_exists")
                duplicate = connection.execute(
                    """
                    SELECT 1 FROM identity_links
                    WHERE right_identity_id=? AND relation='same_edition'
                    LIMIT 1
                    """,
                    (target_identity_id,),
                ).fetchone()
                if duplicate is not None:
                    raise ServiceError("mapping_target_conflict")
                now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
                connection.execute(
                    """
                    INSERT INTO book_identities(
                        identity_id, provider, external_type, external_id, created_at, updated_at
                    ) VALUES (?, 'calibre', 'book_uuid', ?, ?, ?)
                    ON CONFLICT(provider, external_type, external_id) DO NOTHING
                    """,
                    (target_identity_id, calibre_book.uuid, now, now),
                )
                saved_target = connection.execute(
                    """
                    SELECT identity_id FROM book_identities
                    WHERE provider='calibre' AND external_type='book_uuid' AND external_id=?
                    """,
                    (calibre_book.uuid,),
                ).fetchone()
                if saved_target is None or str(saved_target[0]) != target_identity_id:
                    raise ServiceError("identity_conflict")
                connection.execute(
                    """
                    INSERT INTO identity_links(
                        left_identity_id, right_identity_id, relation, confidence, created_at
                    ) VALUES (?, ?, 'same_edition', 'confirmed', ?)
                    """,
                    (source_identity_id, target_identity_id, now),
                )
        except ServiceError:
            raise
        except ApplicationStateError:
            raise ServiceError("application_state_unavailable") from None
        except sqlite3.Error:
            raise ServiceError("mapping_save_failed") from None

    def preview_calibre_reading_projection(
        self,
        import_id: str,
    ) -> CalibreReadingProjectionPreview:
        try:
            require_calibre_closed(_running_process_names())
            _check_calibre_integrity(self.config.calibre_library)
            catalog = load_calibre_catalog(self.config.calibre_library)
            return build_calibre_reading_projection(
                ApplicationState(self.application_state_database),
                import_id,
                catalog,
            )
        except SafetyViolation:
            raise ServiceError("calibre_open") from None
        except BackupError:
            raise ServiceError("calibre_integrity_failed") from None
        except CalibreCatalogError:
            raise ServiceError("calibre_catalog_unavailable") from None
        except ReadingHistoryProjectionError as exc:
            raise ServiceError(exc.reason_code) from None
        except ApplicationStateError:
            raise ServiceError("application_state_unavailable") from None

    def apply_calibre_reading_projection(
        self,
        import_id: str,
        projection_id: str,
        *,
        confirmed: bool = False,
    ) -> ApplyResult:
        if confirmed is not True:
            raise ServiceError("projection_unconfirmed")
        with self._operation_lock():
            return self._apply_calibre_reading_projection_locked(import_id, projection_id)

    def _apply_calibre_reading_projection_locked(
        self, import_id: str, projection_id: str
    ) -> ApplyResult:
        try:
            with ApplicationState(self.application_state_database).connect() as connection:
                batch = connection.execute(
                    "SELECT source_digest, status FROM import_batches "
                    "WHERE import_id=? AND source='koreader'",
                    (import_id,),
                ).fetchone()
            if batch is None:
                raise ServiceError("import_not_found")
            if batch["status"] != "applied":
                raise ServiceError("import_not_applied")
            state = SyncState(self.application_state_database)
            saved = state.load_preview(projection_id)
            items = saved.get("items")
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                raise ServiceError("projection_preview_invalid")
            if any(item.get("source_digest") != batch["source_digest"] for item in items):
                raise ServiceError("projection_import_mismatch")
            if not items:
                return ApplyResult(None, 0, 0)
            return apply_persisted_projection(
                self.config,
                self.application_state_database,
                projection_id,
                self.restic,
                confirmed=True,
            )
        except ServiceError:
            raise
        except ApplicationStateError, SyncStateError:
            raise ServiceError("projection_preview_unavailable") from None
        except CalibreApplyError as exc:
            raise ServiceError(exc.reason_code) from None

    def configure_hardcover(self, token: str) -> None:
        candidate = token.strip()
        if not candidate or len(candidate) > 4096:
            raise ServiceError("hardcover_token_invalid")

        # Validate before replacing the credential already stored in Windows.
        self.hardcover.verify_token(candidate)
        try:
            self.remote_cache.clear_provider("hardcover")
        except SyncPersistenceError:
            raise ServiceError("application_state_unavailable") from None
        save_hardcover_token(candidate)
        self._hardcover_ready_marker.parent.mkdir(parents=True, exist_ok=True)
        marker_temp = self._hardcover_ready_marker.with_suffix(".tmp")
        marker_temp.write_text("verified\n", encoding="ascii")
        marker_temp.replace(self._hardcover_ready_marker)

    def verify_hardcover(self) -> bool:
        if not self._hardcover_token_available():
            return False
        self.hardcover.verify_token(load_hardcover_token())
        return True

    def install_hardcover_plugin(self) -> PluginInstallResult:
        if not self._hardcover_token_available():
            raise ServiceError("hardcover_token_unavailable")
        with self._operation_lock():
            return install_hardcover_with_secret(
                self.config.state_root,
                load_hardcover_token(),
                hardcover_client=self.hardcover,
            )

    def update_hardcover_plugin(
        self, *, recovery_copy_confirmed: bool
    ) -> HardcoverPluginUpdateResult:
        """Verify a complete backup before updating the existing plugin's patched files."""
        if recovery_copy_confirmed is not True:
            raise ServiceError("recovery_copy_unconfirmed")
        with self._operation_lock():
            backup = self.create_backup(verify_restore=True, recovery_copy_confirmed=True)
            if backup.restore_verified is not True:
                raise ServiceError("plugin_update_backup_unverified")
            try:
                source_directory = prepare_hardcover_plugin(self.config.state_root)
                result: PluginUpdateResult = update_plugin_mtp(
                    source_directory,
                    self.config.state_root / "plugin-backups",
                )
            except PluginInstallError as exc:
                raise ServiceError(exc.reason_code) from None
            return HardcoverPluginUpdateResult(
                restic_snapshot_id=backup.snapshot_id,
                rollback_backup_id=result.rollback_backup_id,
                file_count=result.file_count,
                total_bytes=result.total_bytes,
                updated=result.updated,
            )

    def list_hardcover_books(self, *, limit: int = 100, offset: int = 0) -> HardcoverBooksPage:
        token = self._get_hardcover_token()
        cache_key = f"{limit}:{offset}"
        try:
            books = self.hardcover.list_user_books(token, limit=limit, offset=offset)
            fetched_at = datetime.now(UTC)
            self.remote_cache.put(
                "library_page",
                cache_key,
                {"books": [serialize_library_entry(book) for book in books]},
                fetched_at=fetched_at,
            )
            return HardcoverBooksPage(books=books, source="live", fetched_at=fetched_at)
        except HardcoverError as error:
            if error.reason_code not in _CACHE_FALLBACK_ERRORS:
                raise
            try:
                cached = self.remote_cache.get(
                    "library_page", cache_key, max_age=timedelta(days=30)
                )
            except SyncPersistenceError:
                raise ServiceError("application_state_unavailable") from None
            if cached is None:
                raise
            raw_books = cached.payload.get("books")
            if not isinstance(raw_books, list):
                raise ServiceError("hardcover_cache_invalid") from None
            try:
                books = tuple(deserialize_library_entry(item) for item in raw_books)
            except TypeError, ValueError:
                raise ServiceError("hardcover_cache_invalid") from None
            return HardcoverBooksPage(books=books, source="cache", fetched_at=cached.fetched_at)
        except SyncPersistenceError:
            raise ServiceError("application_state_unavailable") from None

    def current_hardcover_reading(self) -> HardcoverCurrentReading:
        token = self._get_hardcover_token()
        try:
            book = self.hardcover.current_reading(token)
            fetched_at = datetime.now(UTC)
            payload: dict[str, object] = {
                "book": serialize_library_entry(book) if book is not None else None
            }
            self.remote_cache.put("current_reading", "me", payload, fetched_at=fetched_at)
            return HardcoverCurrentReading(book=book, source="live", fetched_at=fetched_at)
        except HardcoverError as error:
            if error.reason_code not in _CACHE_FALLBACK_ERRORS:
                raise
            try:
                cached = self.remote_cache.get("current_reading", "me", max_age=timedelta(days=30))
            except SyncPersistenceError:
                raise ServiceError("application_state_unavailable") from None
            if cached is None or "book" not in cached.payload:
                raise
            raw_book = cached.payload["book"]
            if raw_book is None:
                book = None
            else:
                try:
                    book = deserialize_library_entry(raw_book)
                except TypeError, ValueError:
                    raise ServiceError("hardcover_cache_invalid") from None
            return HardcoverCurrentReading(book=book, source="cache", fetched_at=cached.fetched_at)
        except SyncPersistenceError:
            raise ServiceError("application_state_unavailable") from None

    def hardcover_sync_summary(self) -> dict[str, int]:
        try:
            operations = self.sync_outbox.list_pending()
        except SyncPersistenceError:
            raise ServiceError("application_state_unavailable") from None
        hardcover_operations = tuple(
            operation for operation in operations if operation.provider == "hardcover"
        )
        safe_to_retry = sum(
            _hardcover_action_is_safe_to_retry(operation) for operation in hardcover_operations
        )
        return {
            "pending_mutations": len(hardcover_operations),
            "reconciliation_required": sum(
                operation.status == "reconcile" for operation in hardcover_operations
            ),
            "safe_to_retry": safe_to_retry,
            "manual_review_required": len(hardcover_operations) - safe_to_retry,
        }

    def list_hardcover_manual_reviews(self) -> list[dict[str, object]]:
        """Return a local-only, redacted list of ambiguous Hardcover mutations."""
        try:
            operations = self.sync_outbox.list_pending()
            cached_pages = self.remote_cache.list_resources("library_page")
        except SyncPersistenceError:
            raise ServiceError("application_state_unavailable") from None

        by_user_book: dict[int, HardcoverLibraryEntry] = {}
        by_book: dict[int, HardcoverLibraryEntry] = {}
        by_read: dict[int, HardcoverLibraryEntry] = {}
        for cached_page in cached_pages:
            raw_books = cached_page.payload.get("books")
            if not isinstance(raw_books, list):
                continue
            for raw_book in raw_books:
                try:
                    cached_book = deserialize_library_entry(raw_book)
                except TypeError, ValueError:
                    continue
                by_user_book[cached_book.user_book_id] = cached_book
                by_book.setdefault(cached_book.book_id, cached_book)
                for read in cached_book.reads:
                    by_read[read.id] = cached_book

        reviews: list[dict[str, object]] = []
        for operation in operations:
            if (
                operation.provider != "hardcover"
                or operation.status not in {"pending", "reconcile", "failed"}
                or _hardcover_action_is_safe_to_retry(operation)
            ):
                continue
            payload = operation.payload
            user_book_id = _positive_int(payload.get("user_book_id"))
            book_id = _positive_int(payload.get("book_id"))
            read_id = _positive_int(payload.get("read_id"))
            page = _nonnegative_int(payload.get("page"))
            rating = _optional_hardcover_rating(payload.get("rating"))
            reference = (
                f"user_book_id:{user_book_id}"
                if user_book_id is not None
                else f"book_id:{book_id}"
                if book_id is not None
                else f"read_id:{read_id}"
                if read_id is not None
                else None
            )
            matched_book = (
                by_user_book.get(user_book_id)
                if user_book_id is not None
                else by_book.get(book_id)
                if book_id is not None
                else by_read.get(read_id)
                if read_id is not None
                else None
            )
            reviews.append(
                {
                    "operation_id": operation.operation_id,
                    "operation": operation.operation,
                    "status": operation.status,
                    "reason_code": operation.reason_code,
                    "attempts": operation.attempts,
                    "created_at": operation.created_at.isoformat(),
                    "updated_at": operation.updated_at.isoformat(),
                    "book_title": matched_book.title if matched_book is not None else None,
                    "authors": list(matched_book.authors) if matched_book is not None else [],
                    "book_reference": reference,
                    "rating": rating,
                    "page": page,
                }
            )
        return reviews

    def resolve_hardcover_manual_review(
        self,
        operation_id: str,
        *,
        resolution: OutboxReviewResolution,
        confirmed: bool,
    ) -> dict[str, object]:
        if confirmed is not True:
            raise ServiceError("hardcover_confirmation_required")
        if resolution not in {"confirmed_remote", "closed_locally"}:
            raise ServiceError("hardcover_review_not_allowed")
        try:
            operations = self.sync_outbox.list_pending()
            operation = next(
                (
                    item
                    for item in operations
                    if item.operation_id == operation_id and item.provider == "hardcover"
                ),
                None,
            )
            if operation is None or operation.status not in {"pending", "reconcile", "failed"}:
                raise ServiceError("hardcover_review_not_allowed")
            if _hardcover_action_is_safe_to_retry(operation):
                raise ServiceError("hardcover_review_not_allowed")
            reviewed_at = datetime.now(UTC)
            self.sync_outbox.resolve_manual_review(
                operation.operation_id,
                resolution=resolution,
                expected_status=operation.status,
                expected_attempts=operation.attempts,
                reviewed_at=reviewed_at,
            )
        except ServiceError:
            raise
        except SyncPersistenceError as error:
            if "state changed" in str(error) or "already reviewed" in str(error):
                raise ServiceError("hardcover_review_not_allowed") from None
            raise ServiceError("application_state_unavailable") from None
        return {
            "operation_id": operation_id,
            "resolution": resolution,
            "reviewed_at": reviewed_at.isoformat(),
        }

    def reconcile_hardcover_outbox(self, *, confirmed: bool) -> dict[str, int]:
        if confirmed is not True:
            raise ServiceError("hardcover_confirmation_required")
        try:
            operations = self.sync_outbox.list_pending()
        except SyncPersistenceError:
            raise ServiceError("application_state_unavailable") from None

        eligible = [
            operation
            for operation in operations
            if operation.provider == "hardcover" and _hardcover_action_is_safe_to_retry(operation)
        ][:_MAX_HARDCOVER_RECONCILIATION_BATCH]
        if not eligible:
            return self._hardcover_reconciliation_result(0, 0, 0)

        token = self._get_hardcover_token()
        attempted = completed = failed = 0
        for operation in eligible:
            if operation.status == "pending":
                expected_status: Literal["pending", "reconcile"] = "pending"
            elif operation.status == "reconcile":
                expected_status = "reconcile"
            else:
                continue
            try:
                claimed = self.sync_outbox.claim_for_retry(
                    operation.operation_id,
                    expected_status=expected_status,
                    expected_attempts=operation.attempts,
                )
            except SyncPersistenceError:
                raise ServiceError("application_state_unavailable") from None
            if claimed is None:
                continue

            attempted += 1
            try:
                self._replay_hardcover_operation(token, claimed)
            except HardcoverError as error:
                next_status: Literal["reconcile", "failed"] = (
                    "reconcile" if error.reason_code == "remote_unavailable" else "failed"
                )
                try:
                    self.sync_outbox.transition(
                        claimed.operation_id,
                        next_status,
                        reason_code=error.reason_code,
                    )
                except SyncPersistenceError:
                    raise ServiceError("application_state_unavailable") from None
                if next_status == "failed":
                    failed += 1
                continue
            except KeyError, TypeError, ValueError:
                try:
                    self.sync_outbox.transition(
                        claimed.operation_id,
                        "failed",
                        reason_code="invalid_payload",
                    )
                except SyncPersistenceError:
                    raise ServiceError("application_state_unavailable") from None
                failed += 1
                continue
            except Exception:
                try:
                    self.sync_outbox.transition(
                        claimed.operation_id,
                        "reconcile",
                        reason_code="operation_failed",
                    )
                except SyncPersistenceError:
                    raise ServiceError("application_state_unavailable") from None
                continue

            try:
                self.sync_outbox.transition(claimed.operation_id, "complete")
            except SyncPersistenceError:
                raise ServiceError("application_state_unavailable") from None
            completed += 1

        return self._hardcover_reconciliation_result(attempted, completed, failed)

    def _replay_hardcover_operation(self, token: str, operation: OutboxOperation) -> object:
        payload = operation.payload
        if operation.operation == "start_read":
            return self.hardcover.start_read(
                token,
                user_book_id=payload["user_book_id"],  # type: ignore[arg-type]
                edition_id=payload["edition_id"],  # type: ignore[arg-type]
                page=payload["page"],  # type: ignore[arg-type]
                started_at=date.fromisoformat(payload["started_at"]),  # type: ignore[arg-type]
            )
        if operation.operation == "finish_read":
            return self.hardcover.finish_read(
                token,
                read_id=payload["read_id"],  # type: ignore[arg-type]
                finished_at=date.fromisoformat(payload["finished_at"]),  # type: ignore[arg-type]
            )
        if operation.operation == "update_rating":
            rating = payload["rating"]
            if rating is not None and (
                isinstance(rating, bool) or not isinstance(rating, int | float)
            ):
                raise ValueError("Stored rating is invalid")
            return self.hardcover.update_rating(
                token,
                user_book_id=payload["user_book_id"],  # type: ignore[arg-type]
                rating=float(rating) if rating is not None else None,
            )
        if operation.operation == "add_journal_entry":
            entry = payload["entry"]
            if not isinstance(entry, str):
                raise ValueError("Stored journal entry is invalid")
            return self.hardcover.add_journal_entry(
                token,
                book_id=payload["book_id"],  # type: ignore[arg-type]
                entry=entry,
                privacy_setting_id=payload["privacy_setting_id"],  # type: ignore[arg-type]
            )
        raise ValueError("Stored Hardcover operation is unsupported")

    def _hardcover_reconciliation_result(
        self, attempted: int, completed: int, failed: int
    ) -> dict[str, int]:
        summary = self.hardcover_sync_summary()
        return {
            "attempted": attempted,
            "completed": completed,
            "failed": failed,
            "reconciliation_required": summary["reconciliation_required"],
            "safe_to_retry": summary["safe_to_retry"],
            "manual_review_required": summary["manual_review_required"],
        }

    def start_read(
        self, *, user_book_id: int, edition_id: int, page: int = 0, started_at: date | None = None
    ) -> HardcoverRead:
        start_date = started_at or date.today()
        return self._perform_hardcover_mutation(
            "start_read",
            {
                "user_book_id": user_book_id,
                "edition_id": edition_id,
                "page": page,
                "started_at": start_date.isoformat(),
            },
            lambda token: self.hardcover.start_read(
                token,
                user_book_id=user_book_id,
                edition_id=edition_id,
                page=page,
                started_at=start_date,
            ),
        )

    def finish_read(self, *, read_id: int, finished_at: date | None = None) -> HardcoverRead:
        finish_date = finished_at or date.today()
        return self._perform_hardcover_mutation(
            "finish_read",
            {"read_id": read_id, "finished_at": finish_date.isoformat()},
            lambda token: self.hardcover.finish_read(
                token, read_id=read_id, finished_at=finish_date
            ),
        )

    def update_rating(self, *, user_book_id: int, rating: float | None) -> HardcoverLibraryEntry:
        return self._perform_hardcover_mutation(
            "update_rating",
            {"user_book_id": user_book_id, "rating": rating},
            lambda token: self.hardcover.update_rating(
                token, user_book_id=user_book_id, rating=rating
            ),
        )

    def add_journal_entry(
        self,
        *,
        book_id: int,
        entry: str,
        privacy_setting_id: int = 3,
    ) -> int:
        return self._perform_hardcover_mutation(
            "add_journal_entry",
            {"book_id": book_id, "entry": entry, "privacy_setting_id": privacy_setting_id},
            lambda token: self.hardcover.add_journal_entry(
                token,
                book_id=book_id,
                entry=entry,
                privacy_setting_id=privacy_setting_id,
            ),
        )

    def _perform_hardcover_mutation(
        self,
        operation: str,
        payload: dict[str, object],
        request: Callable[[str], _MutationResult],
    ) -> _MutationResult:
        token = self._get_hardcover_token()
        try:
            queued = self.sync_outbox.enqueue(
                provider="hardcover",
                operation=operation,
                idempotency_key=f"{operation}:{uuid.uuid4().hex}",
                payload=payload,
            )
            self.sync_outbox.transition(queued.operation_id, "sending")
        except SyncPersistenceError:
            raise ServiceError("application_state_unavailable") from None
        try:
            result = request(token)
        except HardcoverError as error:
            next_status = "reconcile" if error.reason_code == "remote_unavailable" else "failed"
            try:
                self.sync_outbox.transition(
                    queued.operation_id,
                    next_status,  # type: ignore[arg-type]
                    reason_code=error.reason_code,
                )
            except SyncPersistenceError:
                raise ServiceError("application_state_unavailable") from None
            raise
        except Exception:
            try:
                self.sync_outbox.transition(
                    queued.operation_id, "reconcile", reason_code="operation_failed"
                )
            except SyncPersistenceError:
                raise ServiceError("application_state_unavailable") from None
            raise HardcoverError("remote_unavailable") from None
        try:
            self.sync_outbox.transition(queued.operation_id, "complete")
        except SyncPersistenceError:
            raise ServiceError("application_state_unavailable") from None
        return result

    def configure_remote(
        self,
        *,
        repository: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        config_path: Path | None = None,
    ) -> None:
        safe_repository = validate_s3_repository(repository)
        safe_region = validate_s3_region(region)
        save_s3_credentials(access_key_id, secret_access_key)
        updated = replace(
            self.config,
            restic_remote_repository=safe_repository,
            restic_remote_region=safe_region,
        )
        save_config(updated, config_path)
        self.config = updated

    def configure_remote_with_stored_credentials(
        self, *, repository: str, region: str, config_path: Path | None = None
    ) -> None:
        credentials = load_s3_credentials()
        self.configure_remote(
            repository=repository,
            region=region,
            access_key_id=credentials.access_key_id,
            secret_access_key=credentials.secret_access_key,
            config_path=config_path,
        )

    def sync_remote(self) -> RemoteSyncResult:
        if not self.config.restic_remote_repository or not self.config.restic_remote_region:
            raise ServiceError("remote_repository_unconfigured")
        destination = ResticClient(
            self.config.restic_remote_repository,
            s3_region=self.config.restic_remote_region,
        )
        return mirror_remote(self.restic, destination)  # type: ignore[arg-type]

    def _hardcover_token_available(self) -> bool:
        if (
            not self._hardcover_ready_marker.is_file()
            or self._hardcover_ready_marker.is_symlink()
            or self._hardcover_ready_marker.is_junction()
        ):
            return False
        try:
            return hardcover_token_is_stored()
        except SecretStoreError:
            return False

    def _application_state_database(self) -> Path:
        database = self.config.state_root / "application-state.sqlite3"
        try:
            state = ApplicationState(database)
            state.migrate_legacy_sync_database(self.config.state_root / "koreader-calibre.sqlite3")
            state.migrate_legacy_hardcover_requests(
                self.config.state_root / "hardcover-requests.sqlite3"
            )
        except ApplicationStateError:
            raise ServiceError("application_state_unavailable") from None
        return database

    def _get_hardcover_token(self) -> str:
        if not self._hardcover_token_available():
            raise ServiceError("hardcover_token_unavailable")
        return load_hardcover_token()

    def _new_stage_path(self) -> Path:
        stage_parent = self.config.state_root / "staging"
        stage_parent.mkdir(parents=True, exist_ok=True)
        return stage_parent / uuid.uuid4().hex

    @staticmethod
    def _remove_stage(stage_path: Path) -> None:
        try:
            remove_tree(stage_path)
        except OSError, SafetyViolation:
            raise ServiceError("staging_cleanup_failed") from None
        if stage_path.exists():
            raise ServiceError("staging_cleanup_failed")

    @staticmethod
    def _remove_stage_residuals(staged: MtpStageSummary | MtpReadingStageSummary) -> None:
        for cleanup_path in staged.cleanup_paths:
            ApplicationServices._remove_stage(cleanup_path)

    @staticmethod
    def _remove_stage_result(
        stage_path: Path,
        staged: MtpStageSummary | MtpReadingStageSummary | None,
    ) -> None:
        paths = (*staged.cleanup_paths, stage_path) if staged is not None else (stage_path,)
        first_error: ServiceError | None = None
        for path in paths:
            try:
                ApplicationServices._remove_stage(path)
            except ServiceError as error:
                first_error = first_error or error
        if first_error is not None:
            raise first_error


def _validate_platform_csv_path(path: Path, *, require_existing: bool) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() or "\x00" in str(candidate) or not candidate.name:
        raise ServiceError("platform_csv_file_unavailable")
    try:
        _assert_path_has_no_links(candidate)
        resolved_parent = candidate.parent.resolve(strict=True)
        _assert_path_has_no_links(resolved_parent)
        if not resolved_parent.is_dir():
            raise ServiceError("platform_csv_file_unavailable")
        resolved = resolved_parent / candidate.name
        if resolved.is_symlink() or resolved.is_junction():
            raise ServiceError("platform_csv_file_unavailable")
        if require_existing:
            details = resolved.lstat()
            if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_CSV_BYTES:
                if details.st_size > MAX_CSV_BYTES:
                    raise ServiceError("platform_csv_file_too_large")
                raise ServiceError("platform_csv_file_unavailable")
        elif resolved.exists():
            details = resolved.lstat()
            if not stat.S_ISREG(details.st_mode):
                raise ServiceError("platform_csv_file_unavailable")
        return resolved
    except ServiceError:
        raise
    except OSError:
        raise ServiceError("platform_csv_file_unavailable") from None


def _assert_path_has_no_links(path: Path) -> None:
    current = path
    while True:
        try:
            if current.is_symlink() or current.is_junction():
                raise ServiceError("platform_csv_file_unavailable")
        except OSError:
            raise ServiceError("platform_csv_file_unavailable") from None
        if current == current.parent:
            return
        current = current.parent


def _write_platform_csv_atomically(destination: Path, content: bytes) -> None:
    if destination.exists() or destination.is_symlink() or destination.is_junction():
        details = destination.lstat()
        if not stat.S_ISREG(details.st_mode):
            raise OSError("CSV destination is not a regular file")
    file_descriptor = -1
    temporary_path: Path | None = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".kre-platform-csv-",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(file_descriptor, "wb") as output:
            file_descriptor = -1
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _platform_csv_store_error(reason_code: str) -> ServiceError:
    mapping = {
        "invalid_preview": "platform_csv_invalid_file",
        "invalid_pagination": "platform_csv_invalid_pagination",
        "unsupported_source": "platform_csv_invalid_source",
        "import_not_found": "platform_csv_import_not_found",
        "source_digest_mismatch": "platform_csv_source_changed",
        "mapping_conflict": "platform_csv_mapping_conflict",
        "import_not_pending": "platform_csv_import_not_pending",
        "invalid_import": "platform_csv_invalid_request",
        "invalid_reason_code": "platform_csv_invalid_request",
        "invalid_stored_data": "platform_csv_stored_data_invalid",
        "persistence_failed": "application_state_unavailable",
    }
    return ServiceError(mapping.get(reason_code, "application_state_unavailable"))
