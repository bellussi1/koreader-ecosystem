from __future__ import annotations

import getpass
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TextIO, TypeGuard, cast

import keyring


class SecretStoreError(RuntimeError):
    """Raised when a password cannot be obtained from a protected store."""

    def __init__(self, message: str, *, reason_code: str = "restic_password_unavailable") -> None:
        self.reason_code = reason_code
        super().__init__(message)


_SERVICE_NAME = "koreader-ecosystem"
_LEGACY_SERVICE_NAME = "kindle-reading-ecosystem"
_RESTIC_USERNAME = "restic"
_HARDCOVER_USERNAME = "hardcover"
_S3_ACCESS_KEY_ID = "s3-access-key-id"
_S3_SECRET_ACCESS_KEY = "s3-secret-access-key"
_HARDCOVER_FAILURE = "hardcover_token_unavailable"
_S3_FAILURE = "s3_credentials_unavailable"


@dataclass(frozen=True, slots=True)
class S3Credentials:
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)


def _credential_error(label: str, message: str) -> SecretStoreError:
    lowered = label.casefold()
    if lowered.startswith("hardcover"):
        reason_code = _HARDCOVER_FAILURE
    elif lowered.startswith("s3"):
        reason_code = _S3_FAILURE
    else:
        reason_code = "restic_password_unavailable"
    return SecretStoreError(message, reason_code=reason_code)


def _hidden_prompt(
    prompt: str, *, label: str = "Hardcover token", failure_code: str = _HARDCOVER_FAILURE
) -> str:
    stdin = sys.stdin
    stdout = sys.stdout
    if stdin is None or stdout is None:
        raise SecretStoreError(
            f"{label} requires an interactive hidden console",
            reason_code=failure_code,
        )
    if stdin is not sys.__stdin__ or stdout is not sys.__stdout__:
        raise SecretStoreError(
            f"{label} requires an interactive hidden console",
            reason_code=failure_code,
        )
    stdin = cast(TextIO, stdin)
    stdout = cast(TextIO, stdout)
    try:
        is_interactive = stdin.isatty() and stdout.isatty()
    except AttributeError, OSError:
        is_interactive = False
    if not is_interactive:
        raise SecretStoreError(
            f"{label} requires an interactive hidden console",
            reason_code=failure_code,
        )
    return getpass.getpass(prompt, stream=stdout)


def _windows_credential_backend() -> object:
    if sys.platform != "win32":
        raise SecretStoreError("Windows Credential Manager is required")
    try:
        from keyring.backends.Windows import WinVaultKeyring

        backend = keyring.get_keyring()
    except Exception:
        raise SecretStoreError("Windows Credential Manager is unavailable") from None
    if not isinstance(backend, WinVaultKeyring):
        raise SecretStoreError("An insecure credential backend is configured")
    return backend


def restic_password_is_stored() -> bool:
    """Report whether Credential Manager has the password, without returning it."""
    return _secret_is_stored(_RESTIC_USERNAME, "Restic")


def _secret_is_stored(username: str, label: str) -> bool:
    try:
        backend = _windows_credential_backend()
    except SecretStoreError as error:
        raise _credential_error(label, str(error)) from None
    try:
        password = _get_password(backend, username)
    except Exception:
        raise _credential_error(label, f"Could not inspect the {label} credential") from None
    return isinstance(password, str) and bool(password.strip())


def _get_password(backend: object, username: str) -> str | None:
    """Read the current credential or migrate its legacy service entry in place."""
    get_password = cast(
        Callable[[str, str], str | None],
        backend.get_password,  # type: ignore[attr-defined]
    )
    set_password = cast(
        Callable[[str, str, str], None],
        backend.set_password,  # type: ignore[attr-defined]
    )
    password = get_password(_SERVICE_NAME, username)
    if isinstance(password, str) and password.strip():
        return password
    legacy_password = get_password(_LEGACY_SERVICE_NAME, username)
    if not isinstance(legacy_password, str) or not legacy_password.strip():
        return None
    set_password(_SERVICE_NAME, username, legacy_password)
    migrated_password = get_password(_SERVICE_NAME, username)
    if migrated_password != legacy_password:
        raise RuntimeError("Credential migration could not be verified")
    return migrated_password


def load_restic_password(prompt_fn: Callable[[str], str] = getpass.getpass) -> str:
    """Read the Restic password from Windows Credential Manager or prompt once."""
    return _load_secret(
        _RESTIC_USERNAME,
        "Restic password",
        "Senha do repositório Restic (salve uma cópia no gerenciador de senhas): ",
        prompt_fn,
    )


def _load_secret(
    username: str,
    label: str,
    prompt_message: str,
    prompt_fn: Callable[[str], str],
) -> str:
    try:
        backend = _windows_credential_backend()
    except SecretStoreError as error:
        raise _credential_error(label, str(error)) from None
    try:
        password = _get_password(backend, username)
    except Exception:
        raise _credential_error(
            label, f"Could not read the {label} from Credential Manager"
        ) from None

    if isinstance(password, str) and password.strip():
        return password

    try:
        password = prompt_fn(prompt_message)
    except EOFError, KeyboardInterrupt:
        raise _credential_error(label, f"{label} prompt was cancelled") from None
    if not isinstance(password, str) or not password.strip():
        raise _credential_error(label, f"{label} is empty")

    try:
        backend.set_password(_SERVICE_NAME, username, password)  # type: ignore[attr-defined]
        stored = backend.get_password(_SERVICE_NAME, username)  # type: ignore[attr-defined]
    except Exception:
        raise _credential_error(
            label, f"Could not store the {label} in Credential Manager"
        ) from None
    if stored != password:
        raise _credential_error(label, f"{label} could not be verified in Credential Manager")
    return password


def hardcover_token_is_stored() -> bool:
    """Report whether the Hardcover token exists without returning it."""
    return _secret_is_stored(_HARDCOVER_USERNAME, "Hardcover")


def _valid_hardcover_token(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value.isascii()
        and len(value) <= 4096
        and value == value.strip()
        and not any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        )
    )


def _store_secret_value(username: str, label: str, value: str, *, valid: bool) -> None:
    try:
        backend = _windows_credential_backend()
    except SecretStoreError as error:
        raise _credential_error(label, str(error)) from None
    if not valid:
        raise _credential_error(label, f"{label} is invalid")
    try:
        backend.set_password(_SERVICE_NAME, username, value)  # type: ignore[attr-defined]
        stored = backend.get_password(_SERVICE_NAME, username)  # type: ignore[attr-defined]
    except Exception:
        raise _credential_error(
            label, f"Could not store the {label} in Credential Manager"
        ) from None
    if stored != value:
        raise _credential_error(label, f"{label} could not be verified in Credential Manager")


def save_restic_password(password: str) -> None:
    """Save a password entered by the desktop UI in Windows Credential Manager."""
    valid = isinstance(password, str) and bool(password.strip()) and len(password) <= 4096
    _store_secret_value(_RESTIC_USERNAME, "Restic password", password, valid=valid)


def load_hardcover_token() -> str:
    """Read the Hardcover API token from Windows Credential Manager."""
    try:
        backend = _windows_credential_backend()
    except SecretStoreError as error:
        raise _credential_error("Hardcover", str(error)) from None
    try:
        token = _get_password(backend, _HARDCOVER_USERNAME)
    except Exception:
        raise _credential_error(
            "Hardcover", "Could not read the Hardcover token from Credential Manager"
        ) from None
    if not isinstance(token, str) or not token.strip():
        raise _credential_error("Hardcover", "Hardcover token is not stored in Credential Manager")
    return token


def store_hardcover_token(prompt_fn: Callable[[str], str] | None = None) -> None:
    """Collect a token without echo and persist/verify it in Credential Manager."""
    try:
        prompt = (
            prompt_fn
            if prompt_fn is not None
            else lambda message: _hidden_prompt(
                message, label="Hardcover token", failure_code=_HARDCOVER_FAILURE
            )
        )
        token = prompt("Token da API Hardcover (entrada oculta): ")
    except EOFError, KeyboardInterrupt:
        raise _credential_error("Hardcover", "Hardcover token prompt was cancelled") from None
    save_hardcover_token(token)


def save_hardcover_token(token: str) -> None:
    """Save a token collected by a masked desktop field without exposing it."""
    _store_secret_value(
        _HARDCOVER_USERNAME,
        "Hardcover token",
        token,
        valid=_valid_hardcover_token(token),
    )


def _valid_s3_credential(value: object) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and value.isascii()
        and len(value) <= 4096
        and not any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        )
    )


def store_s3_credentials(prompt_fn: Callable[[str], str] | None = None) -> None:
    """Collect S3 keys without echo and persist both in Windows Credential Manager."""
    prompt = (
        prompt_fn
        if prompt_fn is not None
        else lambda message: _hidden_prompt(
            message, label="S3 credentials", failure_code=_S3_FAILURE
        )
    )
    try:
        access_key_id = prompt("Chave de acesso S3 (entrada oculta): ")
        secret_access_key = prompt("Chave secreta S3 (entrada oculta): ")
    except EOFError, KeyboardInterrupt:
        raise _credential_error("S3 credentials", "S3 credential prompt was cancelled") from None
    save_s3_credentials(access_key_id, secret_access_key)


def save_s3_credentials(access_key_id: str, secret_access_key: str) -> None:
    """Save S3 credentials collected by masked desktop fields."""
    try:
        backend = _windows_credential_backend()
    except SecretStoreError as error:
        raise _credential_error("S3 credentials", str(error)) from None
    if not _valid_s3_credential(access_key_id) or not _valid_s3_credential(secret_access_key):
        raise _credential_error("S3 credentials", "S3 credentials are invalid")
    try:
        backend.set_password(_SERVICE_NAME, _S3_ACCESS_KEY_ID, access_key_id)  # type: ignore[attr-defined]
        backend.set_password(_SERVICE_NAME, _S3_SECRET_ACCESS_KEY, secret_access_key)  # type: ignore[attr-defined]
        stored_access_key_id = backend.get_password(_SERVICE_NAME, _S3_ACCESS_KEY_ID)  # type: ignore[attr-defined]
        stored_secret_access_key = backend.get_password(_SERVICE_NAME, _S3_SECRET_ACCESS_KEY)  # type: ignore[attr-defined]
    except Exception:
        raise _credential_error(
            "S3 credentials", "Could not store S3 credentials in Credential Manager"
        ) from None
    if stored_access_key_id != access_key_id or stored_secret_access_key != secret_access_key:
        raise _credential_error(
            "S3 credentials", "S3 credentials could not be verified in Credential Manager"
        )


def load_s3_credentials() -> S3Credentials:
    """Read S3 keys from Windows Credential Manager without prompting."""
    try:
        backend = _windows_credential_backend()
    except SecretStoreError as error:
        raise _credential_error("S3 credentials", str(error)) from None
    try:
        access_key_id = _get_password(backend, _S3_ACCESS_KEY_ID)
        secret_access_key = _get_password(backend, _S3_SECRET_ACCESS_KEY)
    except Exception:
        raise _credential_error(
            "S3 credentials", "Could not read S3 credentials from Credential Manager"
        ) from None
    if not _valid_s3_credential(access_key_id) or not _valid_s3_credential(secret_access_key):
        raise _credential_error("S3 credentials", "S3 credentials are unavailable")
    return S3Credentials(access_key_id, secret_access_key)
