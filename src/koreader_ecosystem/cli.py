from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from koreader_ecosystem.backup import BackupError
from koreader_ecosystem.calibre_catalog import CalibreCatalogError
from koreader_ecosystem.config import ConfigError, load_config
from koreader_ecosystem.hardcover import HardcoverClient, HardcoverError
from koreader_ecosystem.koreader import PluginInstallError
from koreader_ecosystem.koreader_calibre import KoreaderCalibreError
from koreader_ecosystem.mtp import (
    MtpAmbiguousError,
    MtpCopyError,
    MtpError,
    MtpUnavailableError,
)
from koreader_ecosystem.remote_backup import RemoteSyncError
from koreader_ecosystem.restic import (
    ResticClient,
    ResticCredentialError,
    ResticError,
    ResticTimeoutError,
    ResticUnavailableError,
)
from koreader_ecosystem.safety import SafetyViolation
from koreader_ecosystem.secrets import (
    SecretStoreError,
    load_hardcover_token,
    store_hardcover_token,
    store_s3_credentials,
)
from koreader_ecosystem.services import ApplicationServices, ServiceError
from koreader_ecosystem.sync_state import SyncStateError


class _CommandError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reading")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("doctor", "backup", "status"):
        child = subparsers.add_parser(command)
        child.add_argument("--config", type=Path)
        if command == "backup":
            child.add_argument(
                "--calibre-only",
                action="store_true",
                help="cria snapshot Restic de Calibre e estado local do app, sem o Kindle",
            )
            child.add_argument(
                "--verify-restore",
                action="store_true",
                help="restaura o snapshot em pasta temporária e compara os hashes",
            )

    restore = subparsers.add_parser("restore")
    restore.add_argument("--config", type=Path)
    restore.add_argument("--snapshot", required=True)
    restore.add_argument("--target", required=True, type=Path)

    hardcover = subparsers.add_parser("hardcover")
    hardcover_commands = hardcover.add_subparsers(dest="hardcover_command", required=True)
    for command in ("configure", "auth-check", "install"):
        child = hardcover_commands.add_parser(command)
        child.add_argument("--config", type=Path)

    koreader_calibre = subparsers.add_parser("koreader-calibre")
    koreader_commands = koreader_calibre.add_subparsers(
        dest="koreader_calibre_command", required=True
    )
    preview = koreader_commands.add_parser("preview")
    preview.add_argument("--config", type=Path)
    mapping = koreader_commands.add_parser("map")
    mapping.add_argument("--config", type=Path)
    mapping.add_argument("--preview-id", required=True)
    mapping.add_argument("--sidecar-key", required=True)
    mapping.add_argument("--book-id", required=True, type=int)
    apply = koreader_commands.add_parser("apply")
    apply.add_argument("--config", type=Path)
    apply.add_argument("--preview-id", required=True)

    remote = subparsers.add_parser("remote")
    remote_commands = remote.add_subparsers(dest="remote_command", required=True)
    remote_configure = remote_commands.add_parser("configure")
    remote_configure.add_argument("--config", type=Path)
    remote_configure.add_argument("--repository", required=True)
    remote_configure.add_argument("--region", default="auto")
    remote_sync = remote_commands.add_parser("sync")
    remote_sync.add_argument("--config", type=Path)
    return parser


def _error_code(error: Exception) -> str:
    if isinstance(error, ServiceError):
        return error.reason_code
    if isinstance(error, _CommandError):
        return error.reason_code
    if isinstance(error, ConfigError):
        return "invalid_configuration"
    if isinstance(error, KoreaderCalibreError):
        return error.reason_code
    if isinstance(error, CalibreCatalogError):
        return "calibre_catalog_unavailable"
    if isinstance(error, SyncStateError):
        return "state_error"
    if isinstance(error, MtpCopyError):
        return f"kindle_{error.reason_code}"
    if isinstance(error, MtpAmbiguousError):
        return "multiple_kindles"
    if isinstance(error, MtpUnavailableError):
        return "kindle_unavailable"
    if isinstance(error, MtpError):
        return "kindle_adapter_error"
    if isinstance(error, ResticCredentialError):
        return error.reason_code
    if isinstance(error, SecretStoreError):
        return error.reason_code
    if isinstance(error, ResticTimeoutError):
        return "restic_timeout"
    if isinstance(error, ResticUnavailableError):
        return "restic_unavailable"
    if isinstance(error, ResticError):
        return "restic_error"
    if isinstance(error, RemoteSyncError):
        return error.reason_code
    if isinstance(error, HardcoverError | PluginInstallError):
        return f"hardcover_{error.reason_code}"
    if isinstance(error, BackupError | SafetyViolation):
        return "backup_or_restore_failed"
    return "operation_failed"


def main(argv: list[str] | None = None) -> int:
    args = _make_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        restic = ResticClient(config.restic_repository)
        services = ApplicationServices(config, restic, HardcoverClient())

        if args.command == "remote":
            if args.remote_command == "configure":
                store_s3_credentials()
                services.configure_remote_with_stored_credentials(
                    repository=args.repository,
                    region=args.region,
                    config_path=args.config,
                )
                _emit(
                    {
                        "status": "ok",
                        "remote_configured": True,
                        "credentials_stored": True,
                    }
                )
                return 0

            remote_result = services.sync_remote()
            _emit(
                {
                    "status": "ok",
                    "source_snapshot_count": remote_result.source_snapshot_count,
                    "remote_snapshot_count": remote_result.remote_snapshot_count,
                }
            )
            return 0

        if args.command == "doctor":
            report = services.doctor()
            _emit(report)
            return 0 if report["status"] == "ok" else 1

        if args.command == "status":
            _emit(
                {
                    "status": "ok",
                    "snapshots": list(services.status()),
                }
            )
            return 0

        if args.command == "hardcover":
            if args.hardcover_command == "configure":
                store_hardcover_token()
                services.configure_hardcover(load_hardcover_token())
                _emit({"status": "ok", "authentication": "verified"})
                return 0

            if args.hardcover_command == "auth-check":
                if not services.verify_hardcover():
                    raise ServiceError("hardcover_token_unavailable")
                _emit({"status": "ok", "authentication": "verified"})
                return 0

            if args.hardcover_command == "install":
                result = services.install_hardcover_plugin()
                _emit(
                    {
                        "status": "ok",
                        "plugin_version": "0.4.0",
                        "file_count": result.file_count,
                        "total_bytes": result.total_bytes,
                        "content_verified": True,
                    }
                )
                return 0

        if args.command == "koreader-calibre":
            if args.koreader_calibre_command == "preview":
                preview, report_path = services.preview_koreader_calibre()
                matched_count = sum(
                    item.match_status in {"matched", "manual"} for item in preview.items
                )
                pending_count = sum(
                    item.reason_code
                    not in {
                        "exact_calibre_uuid",
                        "exact_isbn",
                        "manual_mapping",
                        "already_current",
                    }
                    for item in preview.items
                )
                _emit(
                    {
                        "status": "ok",
                        "preview_id": preview.preview_id,
                        "sidecar_count": len(preview.items),
                        "matched_count": matched_count,
                        "change_count": len(preview.changes),
                        "pending_count": pending_count,
                        "report_path": str(report_path),
                    }
                )
                return 0
            if args.koreader_calibre_command == "map":
                services.map_koreader_sidecar(
                    args.preview_id,
                    args.sidecar_key,
                    args.book_id,
                )
                _emit(
                    {
                        "status": "ok",
                        "mapping_saved": True,
                        "preview_id": args.preview_id,
                        "sidecar_key": args.sidecar_key,
                        "book_id": args.book_id,
                        "next_step": "preview_again",
                    }
                )
                return 0
            if args.koreader_calibre_command == "apply":
                apply_result = services.apply_koreader_preview(args.preview_id, confirmed=True)
                _emit(
                    {
                        "status": "ok",
                        "snapshot_id": apply_result.snapshot_id,
                        "applied_book_count": apply_result.applied_book_count,
                        "applied_field_count": apply_result.applied_field_count,
                    }
                )
                return 0

        if args.command == "backup":
            print(
                "Confirme que a senha de recuperação do Restic está guardada em um "
                "gerenciador de senhas fora deste computador? [s/N] ",
                end="",
                file=sys.stderr,
                flush=True,
            )
            confirmation = input()
            if confirmation.strip().casefold() not in {"s", "sim"}:
                raise _CommandError("recovery_copy_unconfirmed")
            backup_result = services.create_backup(
                calibre_only=args.calibre_only,
                verify_restore=args.verify_restore,
                recovery_copy_confirmed=True,
            )
            payload: dict[str, Any] = {
                "status": "ok",
                "snapshot_id": backup_result.snapshot_id,
                "file_count": backup_result.file_count,
                "total_bytes": backup_result.total_bytes,
                "calibre_file_count": backup_result.calibre_file_count,
                "kindle_file_count": backup_result.kindle_file_count,
                "restore_verified": backup_result.restore_verified,
            }
            if backup_result.application_state_file_count:
                payload["application_state_file_count"] = backup_result.application_state_file_count
            _emit(payload)
            return 0

        if args.command == "restore":
            restore_result = services.validate_restore(args.snapshot, args.target)
            _emit(
                {
                    "status": "ok",
                    "snapshot_id": restore_result.snapshot_id,
                    "file_count": restore_result.file_count,
                    "total_bytes": restore_result.total_bytes,
                }
            )
            return 0

        raise _CommandError("unknown_command")
    except (
        _CommandError,
        ConfigError,
        MtpError,
        ResticError,
        RemoteSyncError,
        HardcoverError,
        CalibreCatalogError,
        KoreaderCalibreError,
        SyncStateError,
        PluginInstallError,
        BackupError,
        SecretStoreError,
        SafetyViolation,
        ServiceError,
    ) as exc:
        _emit({"status": "error", "reason_code": _error_code(exc)})
        return 2
    except OSError:
        _emit({"status": "error", "reason_code": "local_io_error"})
        return 2
    except KeyboardInterrupt:
        _emit({"status": "error", "reason_code": "interrupted"})
        return 130


if __name__ == "__main__":
    sys.exit(main())
