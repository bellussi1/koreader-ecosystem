from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TextIO, cast

from koreader_ecosystem.backup import BackupError
from koreader_ecosystem.calibre_catalog import (
    CalibreBook,
    CalibreCatalogError,
    load_calibre_catalog,
)
from koreader_ecosystem.calibre_library import CalibreLibraryError
from koreader_ecosystem.config import AppConfig, ConfigError, load_config
from koreader_ecosystem.hardcover import (
    HARDCOVER_APP_DAILY_REQUEST_LIMIT,
    HARDCOVER_REQUEST_WINDOW_SECONDS,
    HardcoverError,
)
from koreader_ecosystem.hardcover_models import HardcoverLibraryEntry, HardcoverRead
from koreader_ecosystem.mtp import (
    MtpAmbiguousError,
    MtpError,
    MtpUnavailableError,
)
from koreader_ecosystem.platform_csv import CSV_MAPPING_FIELDS
from koreader_ecosystem.reading_history_projection import CalibreReadingProjectionPreview
from koreader_ecosystem.secrets import SecretStoreError, restic_password_is_stored
from koreader_ecosystem.services import (
    ApplicationServices,
    KoreaderReadingHistoryImportPreview,
    ServiceError,
)

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 1024 * 1024
MAX_REQUEST_ID_LENGTH = 128

type Dispatcher = Callable[[str, dict[str, object]], object]
type ConfigLoader = Callable[[], AppConfig]
type CatalogLoader = Callable[[Path], tuple[CalibreBook, ...]]

_SAFE_MESSAGES = {
    "invalid_json": "A solicitação não contém JSON válido.",
    "invalid_request": "A solicitação não corresponde ao protocolo esperado.",
    "unsupported_protocol": "A versão do protocolo não é compatível.",
    "request_too_large": "A solicitação ultrapassa o limite de tamanho.",
    "operation_not_allowed": "A operação solicitada não está disponível.",
    "invalid_payload": "Os parâmetros da operação são inválidos.",
    "configuration_unavailable": "A configuração local não está disponível.",
    "calibre_open": "Feche o Calibre antes de salvar alterações.",
    "calibre_book_not_found": (
        "O livro não está mais na biblioteca. Atualize a lista e tente novamente."
    ),
    "calibre_duplicate_review": "Revise a duplicata antes de adicionar este arquivo.",
    "calibre_format_exists": "Esse livro já possui este formato. Nenhum arquivo foi substituído.",
    "calibre_write_unconfirmed": (
        "O Calibre não confirmou a alteração. Atualize a ficha antes de repetir."
    ),
    "calibre_invalid_metadata": "Revise os metadados informados.",
    "calibre_import_invalid": "O arquivo selecionado não é um formato de livro compatível.",
    "calibre_import_unavailable": "O arquivo selecionado não está mais disponível.",
    "calibre_import_metadata": "Não foi possível ler os metadados desse arquivo.",
    "calibre_unavailable": "A biblioteca ou as ferramentas do Calibre estão indisponíveis.",
    "calibre_export_failed": "Não foi possível preparar esse formato para o Kindle.",
    "application_state_unavailable": "O banco local da aplicação não está disponível.",
    "platform_csv_invalid_source": "Escolha Goodreads, StoryGraph ou Maratona como origem do CSV.",
    "platform_csv_invalid_file": "Não foi possível validar o arquivo CSV selecionado.",
    "platform_csv_file_too_large": "O CSV ultrapassa o limite de 50 MiB.",
    "platform_csv_file_unavailable": "O CSV selecionado não está mais disponível.",
    "platform_csv_export_failed": "Não foi possível salvar o CSV exportado.",
    "platform_csv_export_too_large": "O resultado ultrapassa o limite de exportação de 50 MiB.",
    "platform_csv_import_not_applied": "Confirme a importação antes de exportá-la.",
    "platform_csv_confirmation_required": "Confirme a ação antes de continuar.",
    "platform_csv_import_not_found": "A importação local não foi encontrada.",
    "platform_csv_source_changed": "A prévia está desatualizada. Importe o arquivo novamente.",
    "platform_csv_invalid_mapping": "Revise o mapeamento das colunas e tente novamente.",
    "platform_csv_mapping_conflict": (
        "Este CSV já tem uma importação salva com outro ou antigo mapeamento. "
        "Ela foi mantida; não foi criado outro lote."
    ),
    "platform_csv_import_not_pending": "Esta importação não está aguardando confirmação.",
    "platform_csv_invalid_pagination": "A página solicitada não é válida.",
    "platform_csv_stored_data_invalid": (
        "Os dados locais desta importação não passaram na validação."
    ),
    "platform_csv_unavailable": "O intercâmbio CSV não está disponível agora.",
    "backup_unavailable": "Não foi possível criar e verificar o backup antes da alteração.",
    "backup_in_progress": "Já existe uma operação de backup em andamento.",
    "backup_lock_unavailable": "Não foi possível coordenar o backup com segurança.",
    "scheduled_task_unavailable": "Não foi possível configurar a tarefa diária do Windows.",
    "scheduled_backup_disabled": "A tarefa diária de backup está desativada.",
    "backup_automation_invalid": "As preferências de automação de backup são inválidas.",
    "backup_time_invalid": "Informe o horário no formato de 24 horas, como 20:00.",
    "restic_password_unavailable": (
        "A senha Restic não está disponível no Gerenciador de Credenciais do Windows."
    ),
    "restic_password_invalid": (
        "A senha não abriu o repositório. A credencial anterior foi mantida."
    ),
    "restic_repository_unavailable": (
        "O repositório local de backup não está disponível ou não pôde ser aberto."
    ),
    "restore_target_invalid": (
        "Escolha uma pasta nova fora da biblioteca Calibre e do repositório de backup."
    ),
    "restore_failed": (
        "A restauração não foi concluída ou os arquivos restaurados não puderam ser validados."
    ),
    "kindle_unavailable": "Conecte o Kindle por USB e mantenha-o na tela de conexão USB.",
    "kindle_multiple_devices": (
        "Mais de um Kindle foi encontrado. Deixe somente o dispositivo desejado conectado."
    ),
    "kindle_destination_exists": (
        "Já existe um arquivo com esse nome no Kindle. Escolha outro nome; nada foi substituído."
    ),
    "kindle_transfer_uncertain": (
        "A cópia começou, mas não pôde ser verificada. Confira o Kindle antes de tentar novamente."
    ),
    "kindle_transfer_failed": (
        "A transferência não foi concluída. Nenhum arquivo existente foi substituído."
    ),
    "kindle_content_mismatch": "A cópia no Kindle não passou na verificação de integridade.",
    "kindle_invalid_format": "Esse formato não pode ser enviado ao Kindle pela aplicação.",
    "kindle_invalid_name": "Escolha um nome de arquivo válido para o Kindle.",
    "reading_history_unavailable": (
        "Não foi possível ler o histórico do KOReader. Confira a conexão USB e tente novamente."
    ),
    "reading_history_unsupported": (
        "A versão do histórico do KOReader não é compatível com esta versão da aplicação."
    ),
    "reading_mapping_unavailable": (
        "Não foi possível salvar o vínculo. Confira a biblioteca Calibre e tente novamente."
    ),
    "reading_projection_unavailable": (
        "Não foi possível preparar a revisão dos dados para o Calibre."
    ),
    "hardcover_unconfigured": "Cadastre uma chave válida do Hardcover em Configuração.",
    "hardcover_authentication_failed": "A chave do Hardcover não foi aceita.",
    "hardcover_unavailable": "Não foi possível consultar o Hardcover agora.",
    "hardcover_daily_budget_reached": (
        "Limite diário local do Hardcover atingido. A sincronização será liberada "
        "conforme as chamadas completarem 24 horas."
    ),
    "hardcover_rate_limited": (
        "O Hardcover pediu para reduzir a frequência. Aguarde antes de tentar novamente."
    ),
    "hardcover_budget_unavailable": (
        "O contador local de chamadas não está disponível; a consulta foi bloqueada por segurança."
    ),
    "hardcover_confirmation_required": "Confirme a reconciliação antes de reenviar ações seguras.",
    "hardcover_review_not_allowed": (
        "Esta ação não está mais disponível para revisão. Atualize a lista."
    ),
    "hardcover_token_invalid": "A chave informada não é válida.",
    "operation_failed": "A operação não pôde ser concluída.",
}


class IpcOperationError(RuntimeError):
    """An operation failure with a stable, non-sensitive public code."""

    def __init__(self, code: str, *, request_id: str | None = None) -> None:
        if code not in _SAFE_MESSAGES:
            code = "operation_failed"
        self.code = code
        self.request_id = request_id
        super().__init__(_SAFE_MESSAGES[code])


def _write_response(
    output_stream: TextIO,
    request_id: str | None,
    *,
    result: object | None = None,
    error_code: str | None = None,
) -> None:
    response: dict[str, object] = {
        "version": PROTOCOL_VERSION,
        "id": request_id,
        "ok": error_code is None,
    }
    if error_code is None:
        response["result"] = result
    else:
        safe_code = error_code if error_code in _SAFE_MESSAGES else "operation_failed"
        response["error"] = {
            "code": safe_code,
            "message": _SAFE_MESSAGES[safe_code],
        }
    output_stream.write(json.dumps(response, ensure_ascii=True, separators=(",", ":")) + "\n")
    output_stream.flush()


def _discard_remainder(input_stream: TextIO) -> None:
    while True:
        remainder = input_stream.readline(MAX_REQUEST_BYTES + 2)
        if not remainder or remainder.endswith("\n"):
            return


def _read_request_line(input_stream: TextIO) -> tuple[str | None, bool]:
    line = input_stream.readline(MAX_REQUEST_BYTES + 2)
    if not line:
        return None, False

    content = line.rstrip("\r\n")
    oversized = len(content) > MAX_REQUEST_BYTES
    if not oversized:
        try:
            oversized = len(content.encode("utf-8")) > MAX_REQUEST_BYTES
        except UnicodeEncodeError:
            return content, False

    if not line.endswith("\n") and len(line) >= MAX_REQUEST_BYTES + 2:
        oversized = True
        _discard_remainder(input_stream)
    return content, oversized


def _validated_request(line: str) -> tuple[str, str, dict[str, object]]:
    try:
        request = json.loads(line)
    except json.JSONDecodeError, RecursionError:
        raise IpcOperationError("invalid_json") from None

    if not isinstance(request, dict):
        raise IpcOperationError("invalid_request")
    if set(request) != {"version", "id", "operation", "payload"}:
        raise IpcOperationError("invalid_request")

    request_id = request["id"]
    if (
        not isinstance(request_id, str)
        or not request_id
        or len(request_id) > MAX_REQUEST_ID_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in request_id)
    ):
        raise IpcOperationError("invalid_request")

    version = request["version"]
    if type(version) is not int or version != PROTOCOL_VERSION:
        raise IpcOperationError("unsupported_protocol", request_id=request_id)

    operation = request["operation"]
    payload = request["payload"]
    if (
        not isinstance(operation, str)
        or not operation
        or len(operation) > 128
        or not isinstance(payload, dict)
        or not all(isinstance(key, str) for key in payload)
    ):
        raise IpcOperationError("invalid_request", request_id=request_id)
    return request_id, operation, payload


def serve_jsonl(
    input_stream: TextIO,
    output_stream: TextIO,
    dispatcher: Dispatcher,
) -> None:
    """Process newline-delimited requests until the input stream reaches EOF."""
    while True:
        line, oversized = _read_request_line(input_stream)
        if line is None:
            return
        if oversized:
            _write_response(output_stream, None, error_code="request_too_large")
            continue

        request_id: str | None = None
        try:
            request_id, operation, payload = _validated_request(line)
            result = dispatcher(operation, payload)
        except IpcOperationError as error:
            _write_response(
                output_stream,
                error.request_id if error.request_id is not None else request_id,
                error_code=error.code,
            )
        except Exception:
            _write_response(output_stream, request_id, error_code="operation_failed")
        else:
            try:
                _write_response(output_stream, request_id, result=result)
            except TypeError, ValueError, RecursionError:
                _write_response(output_stream, request_id, error_code="operation_failed")


def build_dispatcher(
    config_loader: ConfigLoader = load_config,
    catalog_loader: CatalogLoader = load_calibre_catalog,
    services_factory: Callable[[AppConfig], ApplicationServices] = ApplicationServices,
) -> Dispatcher:
    """Build the narrowly allowlisted operation dispatcher for the desktop shell."""

    def dispatch(operation: str, payload: dict[str, object]) -> object:
        allowed = {
            "system.status",
            "local-library.list",
            "local-library.get",
            "local-library.add",
            "local-library.save",
            "calibre.list",
            "calibre.preview-import",
            "calibre.add-import",
            "calibre.edit",
            "calibre.remove",
            "kindle.status",
            "kindle.send",
            "hardcover.status",
            "hardcover.list",
            "hardcover.current-reading",
            "hardcover.configure",
            "hardcover.reconcile",
            "hardcover.outbox.list",
            "hardcover.outbox.resolve",
            "koreader.readings.preview",
            "koreader.readings.events",
            "koreader.readings.confirm",
            "koreader.readings.map",
            "calibre.readings.preview",
            "calibre.readings.apply",
            "backup.list",
            "backup.create",
            "backup.automation.status",
            "backup.automation.configure",
            "backup.restore",
            "backup.credentials",
            "backup.configure-password",
            "profile-recovery.preview",
            "profile-recovery.apply",
            "platform-csv.inspect",
            "platform-csv.preview-mapped",
            "platform-csv.page",
            "platform-csv.list",
            "platform-csv.confirm",
            "platform-csv.export",
        }
        if operation not in allowed:
            raise IpcOperationError("operation_not_allowed")
        if operation == "system.status":
            if payload:
                raise IpcOperationError("invalid_payload")
            return {"status": "ready", "protocol_version": PROTOCOL_VERSION}

        files: list[str] | None = None
        file_path: str | None = None
        import_mode: str | None = None
        target_book_id: int | None = None
        book_id: int | None = None
        changes: dict[str, object] | None = None
        cover_path: str | None = None
        batch_book_ids: tuple[int, ...] | None = None
        batch_add_tags: tuple[str, ...] = ()
        batch_remove_tags: tuple[str, ...] = ()
        selected_format: str | None = None
        target_filename: str | None = None
        confirmed = False
        backup_calibre_only = True
        backup_verify_restore = False
        backup_snapshot_id: str | None = None
        backup_target: Path | None = None
        backup_password: str | None = None
        profile_restore_root: Path | None = None
        profile_fingerprint: str | None = None
        reading_import_id: str | None = None
        reading_source_digest: str | None = None
        reading_projection_id: str | None = None
        reading_book_id: int | None = None
        reading_calibre_book_id: int | None = None
        reading_events_limit = 100
        reading_events_offset = 0
        platform_csv_path: Path | None = None
        platform_csv_source: str | None = None
        platform_csv_column_mapping: dict[str, str | None] | None = None
        platform_csv_expected_digest: str | None = None
        platform_csv_import_id: str | None = None
        platform_csv_source_digest: str | None = None
        platform_csv_destination: Path | None = None
        platform_csv_include_reviews = False
        platform_csv_limit = 100
        platform_csv_offset = 0
        platform_csv_list_source: str | None = None
        hardcover_review_operation_id: str | None = None
        hardcover_review_resolution: Literal["confirmed_remote", "closed_locally"] | None = None
        if operation == "calibre.list" and payload:
            raise IpcOperationError("invalid_payload")
        if operation == "calibre.preview-import":
            value = payload.get("files")
            if (
                set(payload) != {"files"}
                or not isinstance(value, list)
                or not 1 <= len(value) <= 20
                or not all(isinstance(item, str) and item and "\x00" not in item for item in value)
            ):
                raise IpcOperationError("invalid_payload")
            files = value
        if operation == "calibre.add-import":
            file_value = payload.get("path")
            mode_value = payload.get("mode")
            target_value = payload.get("target_book_id")
            if (
                set(payload) - {"path", "mode", "target_book_id", "recovery_copy_confirmed"}
                or not {"path", "mode", "recovery_copy_confirmed"}.issubset(payload)
                or not isinstance(file_value, str)
                or not file_value
                or "\x00" in file_value
                or not isinstance(mode_value, str)
                or mode_value not in {"new", "attach"}
                or (
                    target_value is not None
                    and (isinstance(target_value, bool) or not isinstance(target_value, int))
                )
                or (mode_value == "attach" and target_value is None)
                or (mode_value == "new" and target_value is not None)
                or payload.get("recovery_copy_confirmed") is not True
            ):
                raise IpcOperationError("invalid_payload")
            file_path = file_value
            import_mode = mode_value
            target_book_id = target_value if isinstance(target_value, int) else None
            confirmed = True
        if operation == "calibre.edit":
            book_value = payload.get("book_id")
            changes_value = payload.get("changes")
            cover_value = payload.get("cover_path")
            if (
                set(payload) - {"book_id", "changes", "cover_path", "recovery_copy_confirmed"}
                or not {"book_id", "changes", "recovery_copy_confirmed"}.issubset(payload)
                or isinstance(book_value, bool)
                or not isinstance(book_value, int)
                or book_value < 1
                or not isinstance(changes_value, dict)
                or (not changes_value and cover_value is None)
                or len(changes_value) > 16
                or not all(isinstance(key, str) for key in changes_value)
                or (
                    cover_value is not None
                    and (
                        not isinstance(cover_value, str)
                        or not cover_value
                        or "\x00" in cover_value
                        or not Path(cover_value).is_absolute()
                    )
                )
                or payload.get("recovery_copy_confirmed") is not True
            ):
                raise IpcOperationError("invalid_payload")
            book_id = book_value
            changes = changes_value
            cover_path = cover_value if isinstance(cover_value, str) else None
            confirmed = True
        if operation == "calibre.bulk-tags":
            ids_value = payload.get("book_ids")
            add_value = payload.get("add_tags")
            remove_value = payload.get("remove_tags")
            if (
                set(payload)
                != {"book_ids", "add_tags", "remove_tags", "confirmed", "recovery_copy_confirmed"}
                or not isinstance(ids_value, list)
                or not 1 <= len(ids_value) <= 100
                or not all(
                    isinstance(value, int) and not isinstance(value, bool) and value > 0
                    for value in ids_value
                )
                or len(set(ids_value)) != len(ids_value)
                or not isinstance(add_value, list)
                or not isinstance(remove_value, list)
                or len(add_value) > 128
                or len(remove_value) > 128
                or not all(isinstance(value, str) for value in (*add_value, *remove_value))
                or payload.get("confirmed") is not True
                or payload.get("recovery_copy_confirmed") is not True
            ):
                raise IpcOperationError("invalid_payload")
            batch_book_ids = tuple(ids_value)
            batch_add_tags = tuple(add_value)
            batch_remove_tags = tuple(remove_value)
            confirmed = True
        if operation == "calibre.remove":
            book_value = payload.get("book_id")
            if (
                set(payload) != {"book_id", "confirmed", "recovery_copy_confirmed"}
                or isinstance(book_value, bool)
                or not isinstance(book_value, int)
                or book_value < 1
                or payload.get("confirmed") is not True
                or payload.get("recovery_copy_confirmed") is not True
            ):
                raise IpcOperationError("invalid_payload")
            book_id = book_value
            confirmed = True
        if operation == "kindle.status" and payload:
            raise IpcOperationError("invalid_payload")
        if operation == "hardcover.reconcile":
            if set(payload) != {"confirmed"} or payload.get("confirmed") is not True:
                raise IpcOperationError("invalid_payload")
            confirmed = True
        if operation == "hardcover.outbox.list" and payload:
            raise IpcOperationError("invalid_payload")
        if operation == "hardcover.outbox.resolve":
            operation_id_value = payload.get("operation_id")
            resolution_value = payload.get("resolution")
            if (
                set(payload) != {"operation_id", "resolution", "confirmed"}
                or not isinstance(operation_id_value, str)
                or not re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    operation_id_value,
                )
                or resolution_value not in {"confirmed_remote", "closed_locally"}
                or payload.get("confirmed") is not True
            ):
                raise IpcOperationError("invalid_payload")
            hardcover_review_operation_id = operation_id_value
            if resolution_value == "confirmed_remote":
                hardcover_review_resolution = "confirmed_remote"
            else:
                hardcover_review_resolution = "closed_locally"
            confirmed = True
        if operation == "kindle.send":
            book_value = payload.get("book_id")
            format_value = payload.get("format")
            filename_value = payload.get("filename")
            if (
                set(payload) != {"book_id", "format", "filename", "recovery_copy_confirmed"}
                or isinstance(book_value, bool)
                or not isinstance(book_value, int)
                or book_value < 1
                or not isinstance(format_value, str)
                or format_value.upper()
                not in {"AZW", "AZW3", "CBZ", "EPUB", "FB2", "MOBI", "PDF", "TXT"}
                or not isinstance(filename_value, str)
                or payload.get("recovery_copy_confirmed") is not True
            ):
                raise IpcOperationError("invalid_payload")
            book_id = book_value
            selected_format = format_value.upper()
            target_filename = filename_value
            confirmed = True
        if operation == "koreader.readings.preview" and payload:
            raise IpcOperationError("invalid_payload")
        if operation == "koreader.readings.events":
            if not set(payload).issubset({"limit", "offset"}):
                raise IpcOperationError("invalid_payload")
            limit_value = payload.get("limit", 100)
            offset_value = payload.get("offset", 0)
            if (
                isinstance(limit_value, bool)
                or not isinstance(limit_value, int)
                or not 1 <= limit_value <= 100
                or isinstance(offset_value, bool)
                or not isinstance(offset_value, int)
                or offset_value < 0
            ):
                raise IpcOperationError("invalid_payload")
            reading_events_limit = limit_value
            reading_events_offset = offset_value
        if operation == "koreader.readings.confirm":
            import_value = payload.get("import_id")
            digest_value = payload.get("source_digest")
            if (
                set(payload) != {"import_id", "source_digest", "confirmed"}
                or not isinstance(import_value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", import_value)
                or not isinstance(digest_value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest_value)
                or payload.get("confirmed") is not True
            ):
                raise IpcOperationError("invalid_payload")
            reading_import_id = import_value
            reading_source_digest = digest_value
            confirmed = True
        if operation == "koreader.readings.map":
            import_value = payload.get("import_id")
            koreader_value = payload.get("koreader_book_id")
            calibre_value = payload.get("calibre_book_id")
            if (
                set(payload)
                != {
                    "import_id",
                    "koreader_book_id",
                    "calibre_book_id",
                    "confirmed",
                }
                or not isinstance(import_value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", import_value)
                or isinstance(koreader_value, bool)
                or not isinstance(koreader_value, int)
                or koreader_value < 1
                or isinstance(calibre_value, bool)
                or not isinstance(calibre_value, int)
                or calibre_value < 1
                or payload.get("confirmed") is not True
            ):
                raise IpcOperationError("invalid_payload")
            reading_import_id = import_value
            reading_book_id = koreader_value
            reading_calibre_book_id = calibre_value
            confirmed = True
        if operation in {"platform-csv.inspect", "platform-csv.preview-mapped"}:
            path_value = payload.get("path")
            source_value = payload.get("source")
            required = (
                {"path", "source"}
                if operation == "platform-csv.inspect"
                else {"path", "source", "source_digest", "column_mapping"}
            )
            if (
                set(payload) != required
                or not isinstance(path_value, str)
                or not path_value
                or "\x00" in path_value
                or not Path(path_value).is_absolute()
                or not isinstance(source_value, str)
                or source_value not in {"goodreads", "storygraph", "maratona"}
            ):
                raise IpcOperationError("invalid_payload")
            platform_csv_path = Path(path_value)
            platform_csv_source = source_value
            if operation == "platform-csv.preview-mapped":
                digest_value = payload.get("source_digest")
                mapping_value = payload.get("column_mapping")
                if (
                    not isinstance(digest_value, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", digest_value)
                    or not isinstance(mapping_value, dict)
                    or set(mapping_value) != set(CSV_MAPPING_FIELDS)
                    or any(
                        value is not None and not isinstance(value, str)
                        for value in mapping_value.values()
                    )
                ):
                    raise IpcOperationError("invalid_payload")
                selected_headers = [value for value in mapping_value.values() if value is not None]
                if (
                    mapping_value.get("title") is None
                    or mapping_value.get("author") is None
                    or len(selected_headers) != len(set(selected_headers))
                ):
                    raise IpcOperationError("invalid_payload")
                platform_csv_expected_digest = digest_value
                platform_csv_column_mapping = mapping_value
        if operation in {"platform-csv.page", "platform-csv.list"}:
            allowed_page_fields = (
                {"import_id", "limit", "offset"}
                if operation == "platform-csv.page"
                else {"limit", "offset", "source"}
            )
            if not set(payload).issubset(allowed_page_fields):
                raise IpcOperationError("invalid_payload")
            raw_limit = payload.get("limit", 100)
            raw_offset = payload.get("offset", 0)
            import_value = payload.get("import_id")
            source_value = payload.get("source")
            if (
                isinstance(raw_limit, bool)
                or not isinstance(raw_limit, int)
                or not 1 <= raw_limit <= 100
                or isinstance(raw_offset, bool)
                or not isinstance(raw_offset, int)
                or not 0 <= raw_offset <= 50_000
                or (
                    operation == "platform-csv.page"
                    and (
                        not isinstance(import_value, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", import_value)
                    )
                )
                or (
                    source_value is not None
                    and (
                        not isinstance(source_value, str)
                        or source_value not in {"goodreads", "storygraph", "maratona"}
                    )
                )
            ):
                raise IpcOperationError("invalid_payload")
            platform_csv_limit = raw_limit
            platform_csv_offset = raw_offset
            if isinstance(import_value, str):
                platform_csv_import_id = import_value
            if isinstance(source_value, str):
                platform_csv_list_source = source_value
        if operation == "platform-csv.confirm":
            import_value = payload.get("import_id")
            digest_value = payload.get("source_digest")
            if (
                set(payload) != {"import_id", "source_digest", "confirmed"}
                or not isinstance(import_value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", import_value)
                or not isinstance(digest_value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest_value)
                or payload.get("confirmed") is not True
            ):
                raise IpcOperationError("invalid_payload")
            platform_csv_import_id = import_value
            platform_csv_source_digest = digest_value
            confirmed = True
        if operation == "platform-csv.export":
            import_value = payload.get("import_id")
            path_value = payload.get("path")
            include_reviews_value = payload.get("include_reviews")
            if (
                set(payload) != {"import_id", "path", "include_reviews", "confirmed"}
                or not isinstance(import_value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", import_value)
                or not isinstance(path_value, str)
                or not path_value
                or "\x00" in path_value
                or not Path(path_value).is_absolute()
                or not isinstance(include_reviews_value, bool)
                or payload.get("confirmed") is not True
            ):
                raise IpcOperationError("invalid_payload")
            platform_csv_import_id = import_value
            platform_csv_destination = Path(path_value)
            platform_csv_include_reviews = include_reviews_value
            confirmed = True
        if operation == "calibre.readings.preview":
            import_value = payload.get("import_id")
            if (
                set(payload) != {"import_id"}
                or not isinstance(import_value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", import_value)
            ):
                raise IpcOperationError("invalid_payload")
            reading_import_id = import_value
        if operation == "calibre.readings.apply":
            import_value = payload.get("import_id")
            projection_value = payload.get("projection_id")
            if (
                set(payload) != {"import_id", "projection_id", "confirmed"}
                or not isinstance(import_value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", import_value)
                or not isinstance(projection_value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", projection_value)
                or payload.get("confirmed") is not True
            ):
                raise IpcOperationError("invalid_payload")
            reading_import_id = import_value
            reading_projection_id = projection_value
            confirmed = True
        if operation == "backup.list" and payload:
            raise IpcOperationError("invalid_payload")
        if operation == "backup.automation.status" and payload:
            raise IpcOperationError("invalid_payload")
        if operation == "backup.automation.configure" and (
            set(payload)
            != {
                "daily_backup_enabled",
                "daily_backup_time",
                "backup_on_kindle_connect",
            }
            or type(payload.get("daily_backup_enabled")) is not bool
            or not isinstance(payload.get("daily_backup_time"), str)
            or type(payload.get("backup_on_kindle_connect")) is not bool
        ):
            raise IpcOperationError("invalid_payload")
        if operation == "backup.create":
            calibre_only_value = payload.get("calibre_only")
            verify_restore_value = payload.get("verify_restore")
            if (
                set(payload) != {"calibre_only", "verify_restore", "recovery_copy_confirmed"}
                or type(calibre_only_value) is not bool
                or type(verify_restore_value) is not bool
                or payload.get("recovery_copy_confirmed") is not True
            ):
                raise IpcOperationError("invalid_payload")
            backup_calibre_only = calibre_only_value
            backup_verify_restore = verify_restore_value
            confirmed = True
        if operation == "backup.restore":
            snapshot_value = payload.get("snapshot_id")
            target_value = payload.get("target")
            if (
                set(payload) != {"snapshot_id", "target", "recovery_copy_confirmed"}
                or not isinstance(snapshot_value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", snapshot_value)
                or not isinstance(target_value, str)
                or not target_value
                or "\x00" in target_value
                or not Path(target_value).is_absolute()
                or payload.get("recovery_copy_confirmed") is not True
            ):
                raise IpcOperationError("invalid_payload")
            backup_snapshot_id = snapshot_value
            backup_target = Path(target_value)
            confirmed = True
        if operation == "backup.credentials" and payload:
            raise IpcOperationError("invalid_payload")
        if operation == "backup.configure-password":
            password_value = payload.get("password")
            if (
                set(payload) != {"password"}
                or not isinstance(password_value, str)
                or not password_value.strip()
                or len(password_value) > 4096
            ):
                raise IpcOperationError("invalid_payload")
            backup_password = password_value
        if operation == "profile-recovery.preview":
            restore_root_value = payload.get("restore_root")
            if (
                set(payload) != {"restore_root"}
                or not isinstance(restore_root_value, str)
                or not restore_root_value
                or "\x00" in restore_root_value
                or not Path(restore_root_value).is_absolute()
            ):
                raise IpcOperationError("invalid_payload")
            profile_restore_root = Path(restore_root_value)
        if operation == "profile-recovery.apply":
            restore_root_value = payload.get("restore_root")
            fingerprint_value = payload.get("source_fingerprint")
            if (
                set(payload) != {"restore_root", "source_fingerprint", "confirmed"}
                or not isinstance(restore_root_value, str)
                or not restore_root_value
                or "\x00" in restore_root_value
                or not Path(restore_root_value).is_absolute()
                or not isinstance(fingerprint_value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", fingerprint_value)
                or payload.get("confirmed") is not True
            ):
                raise IpcOperationError("invalid_payload")
            profile_restore_root = Path(restore_root_value)
            profile_fingerprint = fingerprint_value
        if (
            operation in {"hardcover.status", "hardcover.current-reading", "kindle.status"}
            and payload
        ):
            raise IpcOperationError("invalid_payload")

        limit = 100
        offset = 0
        if operation == "hardcover.list":
            if not set(payload).issubset({"limit", "offset"}):
                raise IpcOperationError("invalid_payload")
            raw_limit = payload.get("limit", 100)
            raw_offset = payload.get("offset", 0)
            if (
                isinstance(raw_limit, bool)
                or not isinstance(raw_limit, int)
                or not 1 <= raw_limit <= 100
                or isinstance(raw_offset, bool)
                or not isinstance(raw_offset, int)
                or raw_offset < 0
            ):
                raise IpcOperationError("invalid_payload")
            limit, offset = raw_limit, raw_offset

        token: str | None = None
        if operation == "hardcover.configure":
            raw_token = payload.get("token")
            if (
                set(payload) != {"token"}
                or not isinstance(raw_token, str)
                or not raw_token.strip()
                or len(raw_token) > 4096
            ):
                raise IpcOperationError("invalid_payload")
            token = raw_token

        try:
            config = config_loader()
        except ConfigError:
            raise IpcOperationError("configuration_unavailable") from None

        if operation.startswith("local-library."):
            from koreader_ecosystem.application_state import ApplicationState
            from koreader_ecosystem.local_library import LocalLibrary, LocalLibraryError

            services = services_factory(config)
            library = LocalLibrary(ApplicationState(services.application_state_database))
            try:
                if operation == "local-library.list" and set(payload).issubset({"offset"}):
                    local_offset = payload.get("offset", 0)
                    if type(local_offset) is not int or local_offset < 0:
                        raise IpcOperationError("invalid_payload")
                    return {
                        "books": [asdict(book) for book in library.list_books(offset=local_offset)]
                    }
                if operation == "local-library.get" and set(payload) == {"book_id"}:
                    local_id = payload["book_id"]
                    if not isinstance(local_id, str) or not re.fullmatch(r"[0-9a-f]{32}", local_id):
                        raise IpcOperationError("invalid_payload")
                    return asdict(library.get_book(local_id))
                if operation == "local-library.add" and set(payload) == {"title", "author"}:
                    title, author = payload["title"], payload["author"]
                    if not isinstance(title, str) or not isinstance(author, str):
                        raise IpcOperationError("invalid_payload")
                    with services._operation_lock():
                        return asdict(library.add_book(title=title, author=author))
                fields = {
                    "book_id",
                    "status",
                    "progress",
                    "rating",
                    "started_on",
                    "finished_on",
                    "notes",
                }
                if operation != "local-library.save" or set(payload) != fields:
                    raise IpcOperationError("invalid_payload")
                local_book_id, local_status, notes = (
                    payload["book_id"],
                    payload["status"],
                    payload["notes"],
                )
                if not isinstance(local_book_id, str) or not re.fullmatch(
                    r"[0-9a-f]{32}", local_book_id
                ):
                    raise IpcOperationError("invalid_payload")
                if (
                    not isinstance(local_status, str)
                    or local_status not in {"to_read", "reading", "finished", "stopped"}
                    or not isinstance(notes, str)
                ):
                    raise IpcOperationError("invalid_payload")
                progress, rating = payload["progress"], payload["rating"]
                started_on, finished_on = payload["started_on"], payload["finished_on"]
                if any(
                    value is not None
                    and (isinstance(value, bool) or not isinstance(value, (int, float)))
                    for value in (progress, rating)
                ):
                    raise IpcOperationError("invalid_payload")
                if any(
                    value is not None and not isinstance(value, str)
                    for value in (started_on, finished_on)
                ):
                    raise IpcOperationError("invalid_payload")
                with services._operation_lock():
                    return asdict(
                        library.save_reading(
                            local_book_id,
                            status=cast(
                                Literal["to_read", "reading", "finished", "stopped"], local_status
                            ),
                            progress=cast(float | None, progress),
                            rating=cast(float | None, rating),
                            started_on=cast(str | None, started_on),
                            finished_on=cast(str | None, finished_on),
                            notes=notes,
                        )
                    )
            except LocalLibraryError:
                raise IpcOperationError("invalid_payload") from None

        if operation.startswith("profile-recovery."):
            if profile_restore_root is None:
                raise IpcOperationError("invalid_payload")
            services = services_factory(config)
            try:
                if operation == "profile-recovery.preview":
                    return services.preview_profile_recovery(profile_restore_root)
                if profile_fingerprint is None:
                    raise IpcOperationError("invalid_payload")
                return services.apply_profile_recovery(
                    profile_restore_root,
                    profile_fingerprint,
                    confirmed=True,
                )
            except ServiceError as error:
                raise _profile_recovery_service_error(error.reason_code) from None

        if operation.startswith("platform-csv."):
            services = services_factory(config)
            try:
                if operation == "platform-csv.inspect":
                    if platform_csv_path is None or platform_csv_source is None:
                        raise IpcOperationError("invalid_payload")
                    return services.inspect_platform_csv(
                        platform_csv_path,
                        source_name=platform_csv_source,
                    )
                if operation == "platform-csv.preview-mapped":
                    if (
                        platform_csv_path is None
                        or platform_csv_source is None
                        or platform_csv_column_mapping is None
                        or platform_csv_expected_digest is None
                    ):
                        raise IpcOperationError("invalid_payload")
                    return _serialize_platform_csv_preview(
                        services.preview_platform_csv(
                            platform_csv_path,
                            source_name=platform_csv_source,
                            column_mapping=platform_csv_column_mapping,
                            expected_digest=platform_csv_expected_digest,
                        )
                    )
                if operation == "platform-csv.page":
                    if platform_csv_import_id is None:
                        raise IpcOperationError("invalid_payload")
                    return _serialize_platform_csv_page(
                        services.get_platform_csv_preview_page(
                            platform_csv_import_id,
                            offset=platform_csv_offset,
                            limit=platform_csv_limit,
                        )
                    )
                if operation == "platform-csv.list":
                    csv_imports_page = services.list_platform_csv_imports(
                        offset=platform_csv_offset,
                        limit=platform_csv_limit,
                        source_name=platform_csv_list_source,
                    )
                    return {
                        "batches": [asdict(batch) for batch in csv_imports_page.batches],
                        "has_more": csv_imports_page.has_more,
                    }
                if operation == "platform-csv.confirm":
                    if (
                        not confirmed
                        or platform_csv_import_id is None
                        or platform_csv_source_digest is None
                    ):
                        raise IpcOperationError("invalid_payload")
                    return asdict(
                        services.confirm_platform_csv_import(
                            platform_csv_import_id,
                            platform_csv_source_digest,
                            confirmed=True,
                        )
                    )
                if (
                    operation != "platform-csv.export"
                    or not confirmed
                    or platform_csv_import_id is None
                    or platform_csv_destination is None
                ):
                    raise IpcOperationError("invalid_payload")
                return services.export_platform_csv(
                    platform_csv_import_id,
                    platform_csv_destination,
                    include_reviews=platform_csv_include_reviews,
                    confirmed=True,
                )
            except ServiceError as error:
                raise _platform_csv_service_error(error.reason_code) from None

        if operation.startswith(("koreader.readings.", "calibre.readings.")):
            services = services_factory(config)
            try:
                if operation == "koreader.readings.preview":
                    return _serialize_koreader_reading_preview(
                        services.preview_koreader_reading_history()
                    )
                if operation == "koreader.readings.events":
                    events_page = services.get_koreader_reading_events(
                        limit=reading_events_limit,
                        offset=reading_events_offset,
                    )
                    serialized = [asdict(event) for event in events_page.events]
                    return {
                        "events": serialized,
                        "has_more": events_page.has_more,
                    }
                if operation == "koreader.readings.confirm":
                    if not confirmed or reading_import_id is None or reading_source_digest is None:
                        raise IpcOperationError("invalid_payload")
                    batch = services.confirm_koreader_reading_history(
                        reading_import_id,
                        reading_source_digest,
                        confirmed=True,
                    )
                    return asdict(batch)
                if operation == "koreader.readings.map":
                    if (
                        not confirmed
                        or reading_import_id is None
                        or reading_book_id is None
                        or reading_calibre_book_id is None
                    ):
                        raise IpcOperationError("invalid_payload")
                    services.map_koreader_reading_book(
                        reading_import_id,
                        reading_book_id,
                        reading_calibre_book_id,
                        confirmed=True,
                    )
                    return {"mapped": True}
                if operation == "calibre.readings.preview":
                    if reading_import_id is None:
                        raise IpcOperationError("invalid_payload")
                    return _serialize_calibre_reading_projection(
                        services.preview_calibre_reading_projection(reading_import_id)
                    )
                if (
                    operation != "calibre.readings.apply"
                    or not confirmed
                    or reading_import_id is None
                    or reading_projection_id is None
                ):
                    raise IpcOperationError("invalid_payload")
                return asdict(
                    services.apply_calibre_reading_projection(
                        reading_import_id,
                        reading_projection_id,
                        confirmed=True,
                    )
                )
            except ServiceError as error:
                raise _reading_service_error(error.reason_code) from None

        if operation.startswith("hardcover."):
            services = services_factory(config)
            if operation == "hardcover.status":
                try:
                    request_usage = _hardcover_request_usage(services)
                except HardcoverError as error:
                    raise _hardcover_service_error(error.reason_code) from None
                try:
                    sync_summary = services.hardcover_sync_summary()
                except ServiceError as error:
                    raise _hardcover_service_error(error.reason_code) from None
                return {
                    "configured": services.hardcover_ready,
                    **request_usage,
                    **sync_summary,
                }
            if operation == "hardcover.configure":
                if token is None:
                    raise IpcOperationError("invalid_payload")
                try:
                    services.configure_hardcover(token)
                except ServiceError as error:
                    raise _hardcover_service_error(error.reason_code) from None
                except HardcoverError as error:
                    raise _hardcover_service_error(error.reason_code) from None
                try:
                    request_usage = _hardcover_request_usage(services)
                except HardcoverError as error:
                    raise _hardcover_service_error(error.reason_code) from None
                try:
                    sync_summary = services.hardcover_sync_summary()
                except ServiceError as error:
                    raise _hardcover_service_error(error.reason_code) from None
                return {"configured": True, **request_usage, **sync_summary}
            if operation == "hardcover.outbox.list":
                try:
                    return services.list_hardcover_manual_reviews()
                except ServiceError as error:
                    raise _hardcover_service_error(error.reason_code) from None
            if operation == "hardcover.outbox.resolve":
                if hardcover_review_operation_id is None or hardcover_review_resolution is None:
                    raise IpcOperationError("invalid_payload")
                try:
                    return services.resolve_hardcover_manual_review(
                        hardcover_review_operation_id,
                        resolution=hardcover_review_resolution,
                        confirmed=confirmed,
                    )
                except ServiceError as error:
                    raise _hardcover_service_error(error.reason_code) from None
            if not services.hardcover_ready:
                raise IpcOperationError("hardcover_unconfigured")
            try:
                if operation == "hardcover.reconcile":
                    return services.reconcile_hardcover_outbox(confirmed=confirmed)
                if operation == "hardcover.current-reading":
                    reading = services.current_hardcover_reading()
                    return {
                        "book": (
                            _serialize_hardcover_entry(reading.book)
                            if reading.book is not None
                            else None
                        ),
                        "source": reading.source,
                        "fetched_at": reading.fetched_at.isoformat(),
                    }
                hardcover_page = services.list_hardcover_books(limit=limit, offset=offset)
            except ServiceError as error:
                raise _hardcover_service_error(error.reason_code) from None
            except HardcoverError as error:
                raise _hardcover_service_error(error.reason_code) from None
            return {
                "books": [_serialize_hardcover_entry(entry) for entry in hardcover_page.books],
                "has_more": len(hardcover_page.books) == limit,
                "source": hardcover_page.source,
                "fetched_at": hardcover_page.fetched_at.isoformat(),
            }

        if operation.startswith("backup."):
            services = services_factory(config)
            try:
                if operation == "backup.automation.status":
                    return services.backup_automation_status()
                if operation == "backup.automation.configure":
                    return services.configure_backup_automation(
                        daily_backup_enabled=payload["daily_backup_enabled"],  # type: ignore[arg-type]
                        daily_backup_time=payload["daily_backup_time"],  # type: ignore[arg-type]
                        backup_on_kindle_connect=payload["backup_on_kindle_connect"],  # type: ignore[arg-type]
                    )
                if operation == "backup.credentials":
                    return {"password_configured": restic_password_is_stored()}
                if operation == "backup.configure-password":
                    if backup_password is None:
                        raise IpcOperationError("invalid_payload")
                    services.configure_restic_password(backup_password)
                    return {"password_configured": True}
                if operation == "backup.list":
                    return list(services.backup_snapshots())
                if operation == "backup.create":
                    if not confirmed:
                        raise IpcOperationError("invalid_payload")
                    backup_result = services.create_backup(
                        calibre_only=backup_calibre_only,
                        verify_restore=backup_verify_restore,
                        recovery_copy_confirmed=True,
                    )
                    return {
                        "snapshot_id": backup_result.snapshot_id,
                        "file_count": backup_result.file_count,
                        "total_bytes": backup_result.total_bytes,
                        "calibre_file_count": backup_result.calibre_file_count,
                        "kindle_file_count": backup_result.kindle_file_count,
                        "application_state_file_count": backup_result.application_state_file_count,
                        "restore_verified": backup_result.restore_verified,
                    }
                if (
                    operation != "backup.restore"
                    or not confirmed
                    or backup_snapshot_id is None
                    or backup_target is None
                ):
                    raise IpcOperationError("invalid_payload")
                restored_backup = services.validate_restore(backup_snapshot_id, backup_target)
                return {
                    "snapshot_id": restored_backup.snapshot_id,
                    "file_count": restored_backup.file_count,
                    "total_bytes": restored_backup.total_bytes,
                    "profile_recovery_available": _manifest_has_profile(restored_backup.manifest),
                }
            except ServiceError as error:
                if error.reason_code == "restic_password_unavailable":
                    raise IpcOperationError("restic_password_unavailable") from None
                if error.reason_code == "restic_repository_unavailable":
                    raise IpcOperationError("restic_repository_unavailable") from None
                if error.reason_code == "restic_password_invalid":
                    raise IpcOperationError("restic_password_invalid") from None
                if error.reason_code in {
                    "backup_in_progress",
                    "backup_lock_unavailable",
                    "scheduled_task_unavailable",
                    "scheduled_backup_disabled",
                    "backup_automation_invalid",
                    "backup_time_invalid",
                }:
                    raise IpcOperationError(error.reason_code) from None
                raise IpcOperationError("backup_unavailable") from None
            except BackupError as error:
                raise _backup_error(error) from None
            except MtpAmbiguousError:
                raise IpcOperationError("kindle_multiple_devices") from None
            except MtpUnavailableError:
                raise IpcOperationError("kindle_unavailable") from None
            except MtpError:
                raise IpcOperationError("backup_unavailable") from None
            except SecretStoreError:
                raise IpcOperationError("restic_password_unavailable") from None

        if operation.startswith("calibre.") and operation != "calibre.list":
            services = services_factory(config)
            try:
                if operation == "calibre.preview-import":
                    previews = services.preview_calibre_import(tuple(files or ()))
                    return {"files": [asdict(preview) for preview in previews]}
                if operation == "calibre.add-import":
                    if not confirmed or file_path is None or import_mode is None:
                        raise IpcOperationError("invalid_payload")
                    book = services.add_calibre_file(
                        file_path,
                        mode=import_mode,
                        target_book_id=target_book_id,
                        recovery_copy_confirmed=True,
                    )
                    return {"book": asdict(book)}
                if operation == "calibre.remove":
                    if not confirmed or book_id is None:
                        raise IpcOperationError("invalid_payload")
                    services.remove_calibre_book(book_id, recovery_copy_confirmed=True)
                    return {"removed": True}
                if operation == "calibre.bulk-tags":
                    if not confirmed or batch_book_ids is None:
                        raise IpcOperationError("invalid_payload")
                    books = services.edit_calibre_tags_batch(
                        book_ids=batch_book_ids,
                        add_tags=batch_add_tags,
                        remove_tags=batch_remove_tags,
                        recovery_copy_confirmed=True,
                    )
                    return {"books": [asdict(book) for book in books]}
                if book_id is None or changes is None or not confirmed:
                    raise IpcOperationError("invalid_payload")
                book = services.edit_calibre_metadata(
                    book_id=book_id,
                    changes=changes,
                    cover_path=cover_path,
                    recovery_copy_confirmed=True,
                )
                return {"book": asdict(book)}
            except CalibreLibraryError as error:
                raise _calibre_library_error(error.reason_code) from None
            except ServiceError as error:
                raise _calibre_service_error(error.reason_code) from None
            except CalibreCatalogError:
                raise IpcOperationError("calibre_unavailable") from None

        if operation.startswith("kindle."):
            services = services_factory(config)
            if operation == "kindle.status":
                return services.kindle_presence()
            if (
                book_id is None
                or selected_format is None
                or target_filename is None
                or not confirmed
            ):
                raise IpcOperationError("invalid_payload")
            try:
                transfer_result = services.send_calibre_book_to_kindle(
                    book_id=book_id,
                    format_name=selected_format,
                    filename=target_filename,
                    recovery_copy_confirmed=True,
                )
            except ServiceError as error:
                raise _kindle_service_error(error.reason_code) from None
            return asdict(transfer_result)

        try:
            books = catalog_loader(config.calibre_library)
        except CalibreCatalogError:
            raise IpcOperationError("calibre_unavailable") from None
        return {"books": [asdict(book) for book in books], "total": len(books)}

    return dispatch


def _serialize_platform_csv_preview(preview: Any) -> dict[str, object]:
    return {
        "batch": asdict(preview.batch),
        "record_count": preview.record_count,
        "issue_count": preview.issue_count,
        "warning_counts": dict(preview.warning_counts),
    }


def _serialize_platform_csv_page(page: Any) -> dict[str, object]:
    entries = []
    for entry in page.entries:
        record = entry.record
        entries.append(
            {
                "row_number": entry.row_number,
                "row_type": entry.row_type,
                "record": (
                    _serialize_public_platform_csv_record(record, entry.row_number)
                    if isinstance(record, dict)
                    else None
                ),
                "reason_code": entry.reason_code,
            }
        )
    return {"entries": entries, "has_more": page.has_more}


def _serialize_public_platform_csv_record(
    record: dict[str, object], row_number: int
) -> dict[str, object]:
    """Bound renderer-facing CSV values and exclude reviews/source fields."""
    truncated = False

    def text(value: object, maximum: int) -> str | None:
        nonlocal truncated
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        if len(value) > maximum:
            truncated = True
            return value[:maximum]
        return value

    def strings(value: object, *, count: int, length: int) -> list[str]:
        nonlocal truncated
        if not isinstance(value, (list, tuple)):
            return []
        if len(value) > count:
            truncated = True
        result = []
        for item in value[:count]:
            bounded = text(item, length)
            if bounded is not None:
                result.append(bounded)
        return result

    warnings = strings(record.get("warnings", ()), count=32, length=80)
    public_record: dict[str, object] = {
        "row_number": row_number,
        "external_id": text(record.get("external_id"), 256),
        "title": text(record.get("title"), 512) or "",
        "authors": strings(record.get("authors", ()), count=10, length=256),
        "isbn_10": text(record.get("isbn_10"), 32),
        "isbn_13": text(record.get("isbn_13"), 32),
        "rating": record.get("rating") if isinstance(record.get("rating"), (int, float)) else None,
        "exclusive_shelf": text(record.get("exclusive_shelf"), 128),
        "reading_status": text(record.get("reading_status"), 64),
        "shelves": strings(record.get("shelves", ()), count=20, length=128),
        "read_count": record.get("read_count")
        if isinstance(record.get("read_count"), int)
        else None,
        "date_added": text(record.get("date_added"), 32),
        "date_read": text(record.get("date_read"), 32),
        "warnings": warnings,
    }
    if truncated and "preview_truncated" not in warnings:
        warnings.append("preview_truncated")
    return public_record


def _hardcover_service_error(reason_code: str) -> IpcOperationError:
    if reason_code == "hardcover_confirmation_required":
        return IpcOperationError("hardcover_confirmation_required")
    if reason_code == "hardcover_review_not_allowed":
        return IpcOperationError("hardcover_review_not_allowed")
    if reason_code == "application_state_unavailable":
        return IpcOperationError("application_state_unavailable")
    if reason_code in {"hardcover_token_unavailable", "hardcover_unconfigured"}:
        return IpcOperationError("hardcover_unconfigured")
    if reason_code in {"authentication_failed", "hardcover_authentication_failed"}:
        return IpcOperationError("hardcover_authentication_failed")
    if reason_code == "hardcover_token_invalid":
        return IpcOperationError("hardcover_token_invalid")
    if reason_code == "daily_request_budget_reached":
        return IpcOperationError("hardcover_daily_budget_reached")
    if reason_code in {"local_rate_limit", "rate_limited"}:
        return IpcOperationError("hardcover_rate_limited")
    if reason_code == "request_budget_unavailable":
        return IpcOperationError("hardcover_budget_unavailable")
    return IpcOperationError("hardcover_unavailable")


def _platform_csv_service_error(reason_code: str) -> IpcOperationError:
    mapping = {
        "application_state_unavailable": "application_state_unavailable",
        "platform_csv_invalid_source": "platform_csv_invalid_source",
        "platform_csv_invalid_file": "platform_csv_invalid_file",
        "platform_csv_file_too_large": "platform_csv_file_too_large",
        "platform_csv_file_unavailable": "platform_csv_file_unavailable",
        "platform_csv_export_failed": "platform_csv_export_failed",
        "platform_csv_export_too_large": "platform_csv_export_too_large",
        "platform_csv_import_not_applied": "platform_csv_import_not_applied",
        "platform_csv_confirmation_required": "platform_csv_confirmation_required",
        "platform_csv_import_not_found": "platform_csv_import_not_found",
        "platform_csv_source_changed": "platform_csv_source_changed",
        "platform_csv_invalid_mapping": "platform_csv_invalid_mapping",
        "platform_csv_mapping_conflict": "platform_csv_mapping_conflict",
        "platform_csv_import_not_pending": "platform_csv_import_not_pending",
        "platform_csv_invalid_pagination": "platform_csv_invalid_pagination",
        "platform_csv_stored_data_invalid": "platform_csv_stored_data_invalid",
    }
    return IpcOperationError(mapping.get(reason_code, "platform_csv_unavailable"))


def _manifest_has_profile(manifest: tuple[Any, ...]) -> bool:
    paths = {
        entry.path.replace("\\", "/").rstrip("/")
        for entry in manifest
        if isinstance(getattr(entry, "path", None), str)
    }
    for path in paths:
        if path.rsplit("/", 1)[-1] != "application-state.sqlite3":
            continue
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        if f"{parent}/config.json" in paths:
            return True
    return False


def _profile_recovery_service_error(reason_code: str) -> IpcOperationError:
    allowed = {
        "application_state_unavailable",
        "profile_restore_marker_invalid",
        "profile_restore_marker_write_failed",
        "profile_restore_unverified",
        "profile_recovery_confirmation_required",
        "profile_recovery_preview_invalid",
        "profile_recovery_source_changed",
        "profile_recovery_source_invalid",
        "profile_recovery_profile_incomplete",
        "profile_recovery_ambiguous",
        "profile_recovery_config_invalid",
        "profile_recovery_database_invalid",
        "profile_recovery_schema_unsupported",
        "profile_recovery_active_state_invalid",
        "profile_recovery_checkpoint_failed",
        "profile_recovery_install_failed",
        "profile_recovery_rollback_failed",
    }
    return IpcOperationError(
        reason_code if reason_code in allowed else "profile_recovery_unavailable"
    )


def _reading_service_error(reason_code: str) -> IpcOperationError:
    if reason_code in {"kindle_unavailable", "kindle_multiple_devices"}:
        return IpcOperationError(reason_code)
    if reason_code == "calibre_open":
        return IpcOperationError("calibre_open")
    if reason_code in {
        "calibre_integrity_failed",
        "calibre_catalog_unavailable",
        "manual_target_missing",
    }:
        return IpcOperationError("calibre_unavailable")
    if reason_code in {"backup_unavailable", "backup_failed"}:
        return IpcOperationError("backup_unavailable")
    if reason_code in {"calibre_apply_backup_unverified", "calibre_integrity_failed"}:
        return IpcOperationError("calibre_unavailable")
    if reason_code == "koreader_schema_unsupported":
        return IpcOperationError("reading_history_unsupported")
    if reason_code in {
        "mapping_exists",
        "mapping_target_conflict",
        "mapping_save_failed",
        "identity_conflict",
        "invalid_mapping",
    }:
        return IpcOperationError("reading_mapping_unavailable")
    if reason_code.startswith("projection_") or reason_code in {
        "calibre_columns_unavailable",
        "calibredb_unavailable",
        "calibredb_command_failed",
        "calibre_book_changed",
        "calibre_uuid_ambiguous",
        "column_type_mismatch",
        "column_verification_failed",
        "concurrent_change",
        "verification_failed",
        "state_checkpoint_failed",
    }:
        return IpcOperationError("reading_projection_unavailable")
    return IpcOperationError("reading_history_unavailable")


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _serialize_koreader_reading_preview(
    value: KoreaderReadingHistoryImportPreview,
) -> dict[str, object]:
    batch = value.batch
    preview = value.preview
    books = preview.books
    sidecars = preview.sidecars
    sidecars_by_id = {sidecar.sidecar_id: sidecar for sidecar in sidecars}
    serialized_books: list[dict[str, object]] = []
    title_by_id: dict[int, str] = {}
    for book in books:
        book_id = book.koreader_book_id
        title = book.title or "Título indisponível"
        title_by_id[book_id] = title
        book_sidecars = [
            sidecars_by_id[sidecar_id]
            for sidecar_id in book.sidecar_ids
            if sidecar_id in sidecars_by_id
        ]
        statistics_progress = (
            book.total_read_pages / book.pages
            if (
                book.pages is not None
                and book.pages > 0
                and book.total_read_pages is not None
                and 0 <= book.total_read_pages <= book.pages
            )
            else None
        )
        book_warnings = sorted(
            {
                "progress_conflict"
                for sidecar in book_sidecars
                if (
                    sidecar.progress is not None
                    and statistics_progress is not None
                    and abs(sidecar.progress - statistics_progress) > 0.01
                )
            }
        )
        serialized_books.append(
            {
                "koreader_book_id": book_id,
                "title": title,
                "authors": book.authors,
                "series": book.series,
                "language": book.language,
                "pages": book.pages,
                "last_opened_at": _timestamp(book.last_opened_at),
                "statistics_progress": statistics_progress,
                "warnings": book_warnings,
                "sidecars": [
                    {
                        "progress": sidecar.progress,
                        "status": sidecar.status,
                        "rating": sidecar.rating,
                        "has_note": bool(sidecar.note),
                        "annotation_count": len(sidecar.annotations),
                        "warnings": list(sidecar.warnings),
                    }
                    for sidecar in book_sidecars
                ],
            }
        )

    sessions = preview.sessions
    recent_sessions = sessions[-30:]
    serialized_sessions = [
        {
            "session_id": session.session_id,
            "koreader_book_id": session.koreader_book_id,
            "book_title": title_by_id.get(session.koreader_book_id, "Título indisponível"),
            "started_at": _timestamp(session.started_at),
            "ended_at": _timestamp(session.ended_at),
            "page_event_count": session.page_event_count,
            "duration_seconds": session.duration_seconds,
            "first_page": session.first_page,
            "last_page": session.last_page,
            "estimated": True,
        }
        for session in reversed(recent_sessions)
    ]
    return {
        "batch": asdict(batch),
        "source_digest": preview.source_digest,
        "observed_at": _timestamp(preview.observed_at),
        "summary": {
            "book_count": len(books),
            "page_event_count": len(preview.events),
            "estimated_session_count": len(sessions),
            "sidecar_count": len(sidecars),
            "warnings": list(preview.warnings),
        },
        "books": serialized_books,
        "unmatched_sidecars": [
            {
                "sidecar_id": sidecar.sidecar_id,
                "title": sidecar.title,
                "title_source": sidecar.title_source,
                "authors": sidecar.authors,
                "progress": sidecar.progress,
                "status": sidecar.status,
                "rating": sidecar.rating,
                "has_note": bool(sidecar.note),
                "annotation_count": len(sidecar.annotations),
                "warnings": list(sidecar.warnings),
            }
            for sidecar in sidecars
            if sidecar.koreader_book_id is None
        ],
        "estimated_sessions": serialized_sessions,
        "sessions_truncated": len(sessions) > len(recent_sessions),
    }


def _serialize_calibre_reading_projection(
    value: CalibreReadingProjectionPreview,
) -> dict[str, object]:
    return {
        "projection_id": value.projection_id,
        "import_id": value.import_id,
        "source_digest": value.source_digest,
        "items": [asdict(item) for item in value.items],
        "issues": [asdict(issue) for issue in value.issues],
    }


def _hardcover_request_usage(services: ApplicationServices) -> dict[str, int]:
    request_usage = getattr(services.hardcover, "request_usage", None)
    if not callable(request_usage):
        return {
            "requests_used": 0,
            "request_limit": HARDCOVER_APP_DAILY_REQUEST_LIMIT,
            "requests_remaining": HARDCOVER_APP_DAILY_REQUEST_LIMIT,
            "window_seconds": HARDCOVER_REQUEST_WINDOW_SECONDS,
        }

    usage = request_usage()
    if not isinstance(usage, dict):
        raise HardcoverError("request_budget_unavailable")
    values: dict[str, int] = {}
    for key in ("requests_used", "request_limit", "requests_remaining", "window_seconds"):
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise HardcoverError("request_budget_unavailable")
        values[key] = value
    if values["request_limit"] < 1 or values["window_seconds"] < 1:
        raise HardcoverError("request_budget_unavailable")
    return values


def _calibre_library_error(reason_code: str) -> IpcOperationError:
    if reason_code == "calibre_open":
        return IpcOperationError("calibre_open")
    if reason_code in {"book_not_found", "invalid_book_id"}:
        return IpcOperationError("calibre_book_not_found")
    if reason_code in {"duplicate_review_required", "invalid_import_decision"}:
        return IpcOperationError("calibre_duplicate_review")
    if reason_code == "format_already_exists":
        return IpcOperationError("calibre_format_exists")
    if reason_code == "write_unconfirmed":
        return IpcOperationError("calibre_write_unconfirmed")
    if reason_code == "invalid_metadata":
        return IpcOperationError("calibre_invalid_metadata")
    if reason_code in {"invalid_export_format", "format_unavailable"}:
        return IpcOperationError("kindle_invalid_format")
    if reason_code == "invalid_name":
        return IpcOperationError("kindle_invalid_name")
    if reason_code == "calibre_export_unconfirmed":
        return IpcOperationError("calibre_export_failed")
    if reason_code == "invalid_import_file":
        return IpcOperationError("calibre_import_invalid")
    if reason_code == "import_file_unavailable":
        return IpcOperationError("calibre_import_unavailable")
    if reason_code in {
        "ebook_meta_unavailable",
        "ebook_metadata_unavailable",
        "ebook_metadata_invalid",
        "calibredb_command_failed",
    }:
        return IpcOperationError("calibre_import_metadata")
    return IpcOperationError("calibre_unavailable")


def _calibre_service_error(reason_code: str) -> IpcOperationError:
    if reason_code in {
        "backup_unavailable",
        "calibre_remove_backup_unverified",
        "recovery_copy_unconfirmed",
    }:
        return IpcOperationError("backup_unavailable")
    return _calibre_library_error(reason_code)


def _kindle_service_error(reason_code: str) -> IpcOperationError:
    if reason_code in {"device_not_found", "storage_not_found", "kindle_unavailable"}:
        return IpcOperationError("kindle_unavailable")
    if reason_code in {"multiple_devices", "multiple_storages"}:
        return IpcOperationError("kindle_multiple_devices")
    if reason_code == "destination_exists":
        return IpcOperationError("kindle_destination_exists")
    if reason_code == "content_mismatch":
        return IpcOperationError("kindle_content_mismatch")
    if reason_code in {"invalid_name", "kindle_invalid_name"}:
        return IpcOperationError("kindle_invalid_name")
    if reason_code in {"invalid_export_format", "format_unavailable", "kindle_invalid_format"}:
        return IpcOperationError("kindle_invalid_format")
    if reason_code == "kindle_send_uncertain":
        return IpcOperationError("kindle_transfer_uncertain")
    if reason_code == "calibre_open":
        return IpcOperationError("calibre_open")
    if reason_code in {"calibre_catalog_unavailable", "book_not_found"}:
        return IpcOperationError("calibre_book_not_found")
    if reason_code in {
        "backup_unavailable",
        "kindle_send_backup_unverified",
        "staging_unavailable",
    }:
        return IpcOperationError("backup_unavailable")
    return IpcOperationError("kindle_transfer_failed")


def _backup_error(error: BackupError) -> IpcOperationError:
    message = str(error)
    if "Close Calibre" in message:
        return IpcOperationError("calibre_open")
    if "password" in message.casefold():
        return IpcOperationError("restic_password_unavailable")
    if "repository" in message.casefold():
        return IpcOperationError("restic_repository_unavailable")
    if (
        "Restore target" in message
        or "target is existing" in message
        or "target must be absolute" in message
    ):
        return IpcOperationError("restore_target_invalid")
    if "Snapshot identifier is invalid" in message:
        return IpcOperationError("invalid_payload")
    if "restore" in message.casefold():
        return IpcOperationError("restore_failed")
    return IpcOperationError("backup_unavailable")


def _serialize_hardcover_read(read: HardcoverRead) -> dict[str, object]:
    return {
        "id": read.id,
        "started_at": read.started_at.isoformat() if read.started_at else None,
        "finished_at": read.finished_at.isoformat() if read.finished_at else None,
        "progress_pages": read.progress_pages,
        "edition_id": read.edition_id,
    }


def _serialize_hardcover_entry(entry: HardcoverLibraryEntry) -> dict[str, object]:
    return {
        "user_book_id": entry.user_book_id,
        "book_id": entry.book_id,
        "title": entry.title,
        "authors": list(entry.authors),
        "status_id": entry.status_id,
        "rating": entry.rating,
        "edition_id": entry.edition_id,
        "edition_pages": entry.edition_pages,
        "reads": [_serialize_hardcover_read(read) for read in entry.reads],
        "cover_url": entry.cover_url,
        "edition_format": entry.edition_format,
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--scheduled-backup"]:
        try:
            ApplicationServices(load_config()).run_scheduled_backup()
        except Exception:
            return 1
        return 0
    if arguments:
        return 2
    serve_jsonl(sys.stdin, sys.stdout, build_dispatcher())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
