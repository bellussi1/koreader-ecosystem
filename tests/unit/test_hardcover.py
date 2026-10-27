from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from koreader_ecosystem.hardcover import (
    HardcoverClient,
    HardcoverError,
    HardcoverRequestBudget,
    _NoRedirectHandler,
)


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, _: int = -1) -> bytes:
        return self.payload


def test_auth_check_sends_only_read_only_identity_query() -> None:
    captured: dict[str, object] = {}

    def opener(request, timeout: float):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse({"data": {"me": [{"id": 42}]}})

    HardcoverClient(opener=opener).verify_token("private-token")

    request = captured["request"]
    assert request.full_url == "https://api.hardcover.app/v1/graphql"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer private-token"
    assert json.loads(request.data) == {"query": "query { me { id } }"}
    assert captured["timeout"] == 15


def test_auth_check_refuses_redirects_before_forwarding_authorization() -> None:
    request = urllib.request.Request(
        "https://api.hardcover.app/v1/graphql",
        data=b"{}",
        headers={"Authorization": "Bearer private-token"},
        method="POST",
    )

    redirected = _NoRedirectHandler().redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "http://attacker.example/collect",
    )

    assert redirected is None


@pytest.mark.parametrize(
    "payload",
    [
        {"errors": [{"message": "private-token leaked here"}]},
        {"data": {"me": []}},
        {"data": {"me": [{"id": None}]}},
    ],
)
def test_auth_check_rejects_invalid_responses_without_echoing_body(payload) -> None:
    client = HardcoverClient(opener=lambda *_args, **_kwargs: FakeResponse(payload))

    with pytest.raises(HardcoverError) as error:
        client.verify_token("private-token")

    assert error.value.reason_code == "authentication_failed"
    assert "private-token" not in str(error.value)


def test_auth_check_sanitizes_http_error_body() -> None:
    body = b"private-token must not appear in errors"
    http_error = urllib.error.HTTPError(
        "https://api.hardcover.app/v1/graphql", 401, "unauthorized", {}, io.BytesIO(body)
    )
    client = HardcoverClient(opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(http_error))

    with pytest.raises(HardcoverError) as error:
        client.verify_token("private-token")

    assert error.value.reason_code == "authentication_failed"
    assert "private-token" not in str(error.value)


def test_request_budget_persists_usage_in_rolling_window(tmp_path, monkeypatch) -> None:
    now = 1_800_000_000
    monkeypatch.setattr("koreader_ecosystem.hardcover.time.time", lambda: now)
    budget = HardcoverRequestBudget(
        tmp_path / "state" / "hardcover-requests.sqlite3",
        daily_limit=3,
        per_minute_limit=2,
    )

    budget.reserve()
    budget.reserve()

    assert budget.usage() == {
        "requests_used": 2,
        "request_limit": 3,
        "requests_remaining": 1,
        "window_seconds": 24 * 60 * 60,
    }


def test_request_budget_enforces_daily_limit_without_counting_rejected_call(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("koreader_ecosystem.hardcover.time.time", lambda: 1_800_000_000)
    budget = HardcoverRequestBudget(tmp_path / "requests.sqlite3", daily_limit=1)
    budget.reserve()

    with pytest.raises(HardcoverError) as error:
        budget.reserve()

    assert error.value.reason_code == "daily_request_budget_reached"
    assert budget.usage()["requests_used"] == 1


def test_request_budget_releases_per_minute_capacity_after_sixty_seconds(
    tmp_path, monkeypatch
) -> None:
    now = 1_800_000_000
    monkeypatch.setattr("koreader_ecosystem.hardcover.time.time", lambda: now)
    budget = HardcoverRequestBudget(
        tmp_path / "requests.sqlite3", daily_limit=5, per_minute_limit=1
    )
    budget.reserve()

    with pytest.raises(HardcoverError) as error:
        budget.reserve()
    assert error.value.reason_code == "local_rate_limit"

    now += 60
    budget.reserve()
    assert budget.usage()["requests_used"] == 2


def test_request_budget_blocks_network_when_daily_limit_is_reached(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("koreader_ecosystem.hardcover.time.time", lambda: 1_800_000_000)
    budget = HardcoverRequestBudget(tmp_path / "requests.sqlite3", daily_limit=1)
    budget.reserve()
    opener_calls = 0

    def opener(*_args, **_kwargs):
        nonlocal opener_calls
        opener_calls += 1
        return FakeResponse({"data": {"me": [{"id": 42}]}})

    client = HardcoverClient(opener=opener)
    client.set_request_budget(budget)

    with pytest.raises(HardcoverError) as error:
        client.verify_token("private-token")

    assert error.value.reason_code == "daily_request_budget_reached"
    assert opener_calls == 0


def test_request_budget_fails_closed_when_database_is_unavailable(tmp_path) -> None:
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("occupied", encoding="utf-8")
    budget = HardcoverRequestBudget(parent_file / "requests.sqlite3")

    with pytest.raises(HardcoverError) as error:
        budget.reserve()

    assert error.value.reason_code == "request_budget_unavailable"
