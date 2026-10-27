from __future__ import annotations

import io
import sys

import keyring
import pytest
from keyring.backends.Windows import WinVaultKeyring

from koreader_ecosystem.secrets import (
    S3Credentials,
    SecretStoreError,
    hardcover_token_is_stored,
    load_hardcover_token,
    load_restic_password,
    load_s3_credentials,
    restic_password_is_stored,
    save_hardcover_token,
    save_s3_credentials,
    store_hardcover_token,
    store_s3_credentials,
)


class MemoryWindowsKeyring(WinVaultKeyring):
    def __init__(self) -> None:
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password


def test_load_restic_password_prompts_once_and_persists_to_windows_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemoryWindowsKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    prompts: list[str] = []

    def prompt(message: str) -> str:
        prompts.append(message)
        return "a valid restic passphrase"

    password = load_restic_password(prompt_fn=prompt)

    assert password == "a valid restic passphrase"
    assert prompts == ["Senha do repositório Restic (salve uma cópia no gerenciador de senhas): "]
    assert len(backend.passwords) == 1
    assert next(iter(backend.passwords.values())) == password


def test_load_restic_password_uses_stored_value_without_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemoryWindowsKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    backend.set_password("koreader-ecosystem", "restic", "stored-passphrase")

    def prompt(_: str) -> str:
        raise AssertionError("a stored password must not prompt")

    assert load_restic_password(prompt_fn=prompt) == "stored-passphrase"


def test_load_restic_password_migrates_legacy_credential_without_deleting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemoryWindowsKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    backend.set_password("kindle-reading-ecosystem", "restic", "legacy-passphrase")

    def prompt(_: str) -> str:
        raise AssertionError("a legacy password must be migrated without prompting")

    assert load_restic_password(prompt_fn=prompt) == "legacy-passphrase"
    assert backend.passwords[("koreader-ecosystem", "restic")] == "legacy-passphrase"
    assert backend.passwords[("kindle-reading-ecosystem", "restic")] == "legacy-passphrase"


@pytest.mark.parametrize("password", ["", "   "])
def test_load_restic_password_rejects_empty_prompt(
    monkeypatch: pytest.MonkeyPatch, password: str
) -> None:
    backend = MemoryWindowsKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)

    with pytest.raises(SecretStoreError) as error:
        load_restic_password(prompt_fn=lambda _: password)

    assert str(error.value) == "Restic password is empty"
    assert backend.passwords == {}


def test_load_restic_password_refuses_an_insecure_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(keyring, "get_keyring", lambda: object())

    with pytest.raises(SecretStoreError):
        load_restic_password(prompt_fn=lambda _: "must-not-be-stored")


def test_keyring_errors_are_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenBackend(MemoryWindowsKeyring):
        def get_password(self, service: str, username: str) -> str | None:
            raise RuntimeError("private-passphrase-must-not-leak")

    monkeypatch.setattr(keyring, "get_keyring", lambda: BrokenBackend())

    with pytest.raises(SecretStoreError) as error:
        load_restic_password(prompt_fn=lambda _: "unused")

    assert "private-passphrase-must-not-leak" not in str(error.value)


def test_restic_password_status_does_not_reveal_the_stored_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemoryWindowsKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)

    assert not restic_password_is_stored()
    backend.set_password("koreader-ecosystem", "restic", "private-passphrase")
    assert restic_password_is_stored()


def test_store_hardcover_token_prompts_hidden_and_persists_to_windows_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemoryWindowsKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    prompts: list[str] = []

    def prompt(message: str) -> str:
        prompts.append(message)
        return "hc_pat_example_token"

    store_hardcover_token(prompt_fn=prompt)

    assert prompts == ["Token da API Hardcover (entrada oculta): "]
    assert backend.passwords == {("koreader-ecosystem", "hardcover"): "hc_pat_example_token"}
    assert hardcover_token_is_stored()
    assert load_hardcover_token() == "hc_pat_example_token"


def test_store_hardcover_token_refuses_noninteractive_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemoryWindowsKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    monkeypatch.setattr(sys, "stdin", io.StringIO("hc_pat_must_not_be_echoed"))

    with pytest.raises(SecretStoreError, match="interactive hidden console"):
        store_hardcover_token()

    assert backend.passwords == {}


@pytest.mark.parametrize("token", ["", "   ", "token with spaces", "token\nnewline", "tøkén"])
def test_store_hardcover_token_rejects_invalid_prompt(
    monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    backend = MemoryWindowsKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)

    with pytest.raises(SecretStoreError):
        store_hardcover_token(prompt_fn=lambda _: token)

    assert backend.passwords == {}


def test_load_hardcover_token_missing_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = MemoryWindowsKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)

    with pytest.raises(SecretStoreError) as error:
        load_hardcover_token()

    assert str(error.value) == "Hardcover token is not stored in Credential Manager"


def test_hardcover_status_does_not_reveal_token(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = MemoryWindowsKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    backend.set_password("koreader-ecosystem", "hardcover", "private-token")

    assert hardcover_token_is_stored()


def test_save_hardcover_token_value_from_masked_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = MemoryWindowsKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)

    save_hardcover_token("hc_pat_replacement_token")

    assert load_hardcover_token() == "hc_pat_replacement_token"


@pytest.mark.parametrize("token", ["", "token with spaces", "token\nnewline", "tøkén"])
def test_save_hardcover_token_rejects_invalid_ui_value(
    monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    backend = MemoryWindowsKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)

    with pytest.raises(SecretStoreError):
        save_hardcover_token(token)

    assert backend.passwords == {}


def test_s3_credentials_prompt_hidden_and_persist_in_windows_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemoryWindowsKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    prompts: list[str] = []

    def prompt(message: str) -> str:
        prompts.append(message)
        return "access-id-example" if len(prompts) == 1 else "secret-key-example"

    store_s3_credentials(prompt_fn=prompt)
    credentials = load_s3_credentials()

    assert prompts == [
        "Chave de acesso S3 (entrada oculta): ",
        "Chave secreta S3 (entrada oculta): ",
    ]
    assert credentials == S3Credentials("access-id-example", "secret-key-example")
    assert "secret-key-example" not in repr(credentials)
    assert backend.passwords == {
        ("koreader-ecosystem", "s3-access-key-id"): "access-id-example",
        ("koreader-ecosystem", "s3-secret-access-key"): "secret-key-example",
    }


@pytest.mark.parametrize("invalid", ["", " ", "contains whitespace", "key\nline"])
def test_s3_credentials_reject_invalid_values(
    monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    backend = MemoryWindowsKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    calls = 0

    def prompt(_: str) -> str:
        nonlocal calls
        calls += 1
        return "access-id-example" if calls == 1 else invalid

    with pytest.raises(SecretStoreError):
        store_s3_credentials(prompt_fn=prompt)

    assert not any(value == invalid for value in backend.passwords.values())


def test_s3_credentials_missing_are_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = MemoryWindowsKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)

    with pytest.raises(SecretStoreError) as error:
        load_s3_credentials()

    assert error.value.reason_code == "s3_credentials_unavailable"
    assert "secret" not in str(error.value).casefold()


def test_save_s3_credentials_values_from_masked_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = MemoryWindowsKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)

    save_s3_credentials("access-id-ui", "secret-key-ui")

    assert load_s3_credentials() == S3Credentials("access-id-ui", "secret-key-ui")
