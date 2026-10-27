from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from datetime import date, datetime
from typing import Any

import pytest

from koreader_ecosystem.hardcover import HardcoverClient, HardcoverError
from koreader_ecosystem.hardcover_models import parse_library_entry


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, _: int = -1) -> bytes:
        return self.payload


def _library_entry(*, reads: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": 101,
        "book_id": 202,
        "status_id": 2,
        "edition_id": 303,
        "rating": 4.5,
        "book": {
            "title": "A Reader's Example",
            "cached_contributors": [{"name": "A. Author"}],
            "cached_image": {"url": "https://assets.hardcover.app/covers/example.jpg"},
        },
        "edition": {"id": 303, "pages": 360, "edition_format": "Kindle Edition"},
        "user_book_reads": reads
        if reads is not None
        else [
            {
                "id": 404,
                "started_at": "2026-10-10",
                "finished_at": None,
                "progress_pages": 151,
                "edition_id": 303,
            },
            {
                "id": 405,
                "started_at": "2026-10-18",
                "finished_at": None,
                "progress_pages": 42,
                "edition_id": 303,
            },
        ],
    }


def test_library_entry_extracts_a_valid_hardcover_cover_url() -> None:
    entry = parse_library_entry(_library_entry())

    assert entry.cover_url == "https://assets.hardcover.app/covers/example.jpg"


def test_library_entry_ignores_untrusted_cover_urls() -> None:
    value = _library_entry()
    value["book"]["cached_image"] = {"url": "https://unexpected.example/cover.jpg"}

    entry = parse_library_entry(value)

    assert entry.cover_url is None


def _responses(*payloads: dict[str, Any]) -> Callable[..., FakeResponse]:
    responses = iter(FakeResponse(payload) for payload in payloads)

    def opener(*_args: object, **_kwargs: object) -> FakeResponse:
        return next(responses)

    return opener


class HardcoverMutationFake:
    """Stateful GraphQL stub that exposes post-mutation reads like the API."""

    def __init__(self, entry: dict[str, Any] | None = None) -> None:
        self.entry = deepcopy(entry if entry is not None else _library_entry(reads=[]))
        self.next_read_id = 406
        self.next_journal_id = 900
        self.ignore_progress_write = False
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request, timeout: float) -> FakeResponse:
        body = json.loads(request.data.decode("utf-8"))
        self.requests.append(body)
        query = body["query"]
        variables = body.get("variables", {})

        if "query UserBookById" in query:
            return FakeResponse({"data": {"user_books": [deepcopy(self.entry)]}})
        if "query UserBookReadById" in query:
            read = next(
                item for item in self.entry["user_book_reads"] if item["id"] == variables["id"]
            )
            read_with_parent = deepcopy(read)
            read_with_parent["user_book"] = {
                "id": self.entry["id"],
                "status_id": self.entry["status_id"],
            }
            return FakeResponse({"data": {"user_book_reads": [read_with_parent]}})
        if "mutation UpdateUserBookStatus" in query:
            self.entry["status_id"] = variables["statusId"]
            return FakeResponse(
                {
                    "data": {
                        "update_user_book": {
                            "error": None,
                            "user_book": {
                                "id": self.entry["id"],
                                "status_id": self.entry["status_id"],
                            },
                        }
                    }
                }
            )
        if "mutation UpdateUserBookRating" in query:
            self.entry["rating"] = variables["rating"]
            return FakeResponse(
                {
                    "data": {
                        "update_user_book": {
                            "error": None,
                            "user_book": {"id": self.entry["id"], "rating": self.entry["rating"]},
                        }
                    }
                }
            )
        if "mutation InsertUserBookRead" in query:
            created = {
                "id": self.next_read_id,
                "started_at": variables["startedAt"],
                "finished_at": None,
                "progress_pages": variables["pages"],
                "edition_id": variables["editionId"],
            }
            self.next_read_id += 1
            self.entry["user_book_reads"].append(created)
            return FakeResponse(
                {
                    "data": {
                        "insert_user_book_read": {
                            "error": None,
                            "user_book_read": {
                                **created,
                                "user_book": {
                                    "id": self.entry["id"],
                                    "status_id": self.entry["status_id"],
                                },
                            },
                        }
                    }
                }
            )
        if "update_user_book_read(" in query:
            read = next(
                item for item in self.entry["user_book_reads"] if item["id"] == variables["id"]
            )
            if "mutation FinishUserBookRead" in query:
                read["finished_at"] = variables["finishedAt"]
            elif not self.ignore_progress_write:
                read["progress_pages"] = variables["pages"]
                read["edition_id"] = variables["editionId"]
                read["started_at"] = variables["startedAt"]
            return FakeResponse(
                {
                    "data": {
                        "update_user_book_read": {
                            "error": None,
                            "user_book_read": {
                                **deepcopy(read),
                                "user_book": {
                                    "id": self.entry["id"],
                                    "status_id": self.entry["status_id"],
                                },
                            },
                        }
                    }
                }
            )
        if "mutation InsertReadingJournalEntry" in query:
            entry = deepcopy(variables["object"])
            entry["id"] = self.next_journal_id
            self.next_journal_id += 1
            return FakeResponse({"data": {"insert_reading_journal": {"reading_journal": entry}}})
        raise AssertionError("Unexpected Hardcover GraphQL operation")


def test_list_user_books_parses_latest_read_and_uses_bounded_page_variables() -> None:
    captured: list[dict[str, Any]] = []

    def opener(request, timeout: float) -> FakeResponse:
        captured.append(
            {
                "query": json.loads(request.data.decode("utf-8")),
                "timeout": timeout,
            }
        )
        if len(captured) == 1:
            return FakeResponse({"data": {"me": [{"id": 77}]}})
        return FakeResponse({"data": {"user_books": [_library_entry()]}})

    books = HardcoverClient(opener=opener).list_user_books("private-token", limit=20, offset=40)

    assert len(books) == 1
    book = books[0]
    assert (book.user_book_id, book.book_id, book.title) == (101, 202, "A Reader's Example")
    assert book.authors == ("A. Author",)
    assert (book.status_id, book.rating, book.edition_id, book.edition_pages) == (2, 4.5, 303, 360)
    assert book.edition_format == "Kindle Edition"
    assert len(book.reads) == 2
    assert book.reads[-1].id == 405
    assert book.reads[-1].started_at == date(2026, 10, 18)
    assert book.reads[-1].finished_at is None
    assert book.reads[-1].progress_pages == 42
    assert book.reads[-1].edition_id == 303
    assert "edition_format" in captured[1]["query"]["query"]
    assert captured[1]["query"]["variables"] == {"userId": 77, "limit": 20, "offset": 40}
    assert captured[1]["timeout"] == 15


def test_list_user_books_accepts_books_without_read_records() -> None:
    responses = _responses(
        {"data": {"me": [{"id": 77}]}},
        {"data": {"user_books": [_library_entry(reads=[])]}},
    )

    books = HardcoverClient(opener=responses).list_user_books("private-token")

    assert len(books) == 1
    assert books[0].reads == ()


@pytest.mark.parametrize(
    "entry",
    [
        {**_library_entry(), "id": "not-an-id"},
        {**_library_entry(), "book": {"title": "", "cached_contributors": []}},
        {
            **_library_entry(),
            "user_book_reads": [
                {
                    "id": 404,
                    "started_at": "not-a-date",
                    "finished_at": None,
                    "progress_pages": 1,
                    "edition_id": 303,
                }
            ],
        },
    ],
)
def test_list_user_books_rejects_malformed_records(entry: dict[str, Any]) -> None:
    client = HardcoverClient(
        opener=_responses({"data": {"me": [{"id": 77}]}}, {"data": {"user_books": [entry]}})
    )

    with pytest.raises(HardcoverError) as error:
        client.list_user_books("private-token")

    assert error.value.reason_code == "invalid_response"


def test_list_user_books_sanitizes_graphql_errors() -> None:
    client = HardcoverClient(
        opener=_responses(
            {"data": {"me": [{"id": 77}]}},
            {"errors": [{"message": "private-token and private book details"}]},
        )
    )

    with pytest.raises(HardcoverError) as error:
        client.list_user_books("private-token")

    assert error.value.reason_code == "remote_unavailable"
    assert "private-token" not in str(error.value)
    assert "private book details" not in str(error.value)


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (101, 0), (20, -1)])
def test_list_user_books_rejects_invalid_pagination_without_request(
    limit: int, offset: int
) -> None:
    calls = 0

    def opener(*_args: object, **_kwargs: object) -> FakeResponse:
        nonlocal calls
        calls += 1
        return FakeResponse({"data": {"me": [{"id": 77}]}})

    with pytest.raises(HardcoverError) as error:
        HardcoverClient(opener=opener).list_user_books("private-token", limit=limit, offset=offset)

    assert error.value.reason_code == "invalid_request"
    assert calls == 0


def test_start_read_creates_a_new_record_after_finished_read_and_is_idempotent() -> None:
    entry = _library_entry(
        reads=[
            {
                "id": 405,
                "started_at": "2026-10-01",
                "finished_at": "2026-10-15",
                "progress_pages": 360,
                "edition_id": 303,
            }
        ]
    )
    entry["status_id"] = 3
    fake = HardcoverMutationFake(entry)
    client = HardcoverClient(opener=fake)

    first = client.start_read(
        "private-token",
        user_book_id=101,
        edition_id=303,
        page=1,
        started_at=date(2026, 10, 23),
    )
    second = client.start_read(
        "private-token",
        user_book_id=101,
        edition_id=303,
        page=1,
        started_at=date(2026, 10, 23),
    )

    assert first.id == second.id == 406
    assert first.started_at == date(2026, 10, 23)
    assert first.progress_pages == 1
    assert fake.entry["status_id"] == 2
    assert [read["id"] for read in fake.entry["user_book_reads"]] == [405, 406]
    assert any("mutation InsertUserBookRead" in request["query"] for request in fake.requests)


def test_start_read_reuses_an_existing_active_record_without_resetting_progress() -> None:
    active = {
        "id": 405,
        "started_at": "2026-10-10",
        "finished_at": None,
        "progress_pages": 151,
        "edition_id": 303,
    }
    fake = HardcoverMutationFake(_library_entry(reads=[active]))

    read = HardcoverClient(opener=fake).start_read(
        "private-token",
        user_book_id=101,
        edition_id=303,
        page=1,
        started_at=date(2026, 10, 23),
    )

    assert read.id == 405
    assert read.progress_pages == 151
    assert len(fake.entry["user_book_reads"]) == 1
    assert not any("mutation InsertUserBookRead" in request["query"] for request in fake.requests)


def test_update_read_writes_current_position_and_reads_it_back() -> None:
    active = {
        "id": 405,
        "started_at": "2026-10-10",
        "finished_at": None,
        "progress_pages": 151,
        "edition_id": 303,
    }
    fake = HardcoverMutationFake(_library_entry(reads=[active]))

    read = HardcoverClient(opener=fake).update_read(
        "private-token",
        read_id=405,
        edition_id=303,
        page=112,
        started_at=date(2026, 10, 10),
    )

    assert read.progress_pages == 112
    assert read.started_at == date(2026, 10, 10)
    assert fake.requests[1]["variables"] == {
        "id": 405,
        "pages": 112,
        "editionId": 303,
        "startedAt": "2026-10-10",
    }
    assert "finished_at" not in fake.requests[1]["query"]


def test_update_read_does_not_change_a_finished_read() -> None:
    finished = {
        "id": 405,
        "started_at": "2026-10-10",
        "finished_at": "2026-10-20",
        "progress_pages": 360,
        "edition_id": 303,
    }
    fake = HardcoverMutationFake(_library_entry(reads=[finished]))

    with pytest.raises(HardcoverError) as error:
        HardcoverClient(opener=fake).update_read(
            "private-token",
            read_id=405,
            edition_id=303,
            page=200,
            started_at=date(2026, 10, 10),
        )

    assert error.value.reason_code == "read_finished"
    assert len(fake.requests) == 1


def test_update_read_reports_when_server_does_not_persist_the_page() -> None:
    active = {
        "id": 405,
        "started_at": "2026-10-10",
        "finished_at": None,
        "progress_pages": 151,
        "edition_id": 303,
    }
    fake = HardcoverMutationFake(_library_entry(reads=[active]))
    fake.ignore_progress_write = True

    with pytest.raises(HardcoverError) as error:
        HardcoverClient(opener=fake).update_read(
            "private-token",
            read_id=405,
            edition_id=303,
            page=112,
            started_at=date(2026, 10, 10),
        )

    assert error.value.reason_code == "write_unconfirmed"


def test_finish_read_sets_date_and_finished_status_idempotently() -> None:
    active = {
        "id": 405,
        "started_at": "2026-10-10",
        "finished_at": None,
        "progress_pages": 360,
        "edition_id": 303,
    }
    fake = HardcoverMutationFake(_library_entry(reads=[active]))
    client = HardcoverClient(opener=fake)

    first = client.finish_read("private-token", read_id=405, finished_at=date(2026, 10, 23))
    requests_after_first_finish = len(fake.requests)
    second = client.finish_read("private-token", read_id=405, finished_at=date(2026, 10, 24))

    assert first.finished_at == second.finished_at == date(2026, 10, 23)
    assert fake.entry["status_id"] == 3
    assert len(fake.requests) == requests_after_first_finish + 1


def test_update_status_and_rating_require_server_readback() -> None:
    fake = HardcoverMutationFake()
    client = HardcoverClient(opener=fake)

    updated = client.update_status("private-token", user_book_id=101, status_id=5)
    rated = client.update_rating("private-token", user_book_id=101, rating=0.5)
    cleared = client.update_rating("private-token", user_book_id=101, rating=None)

    assert updated.status_id == 5
    assert rated.rating == 0.5
    assert cleared.rating is None
    assert fake.entry["rating"] is None


def test_add_journal_entry_uses_variables_and_confirms_created_entry() -> None:
    fake = HardcoverMutationFake()
    client = HardcoverClient(opener=fake)

    journal_id = client.add_journal_entry(
        "private-token",
        book_id=202,
        entry="Minha nota privada",
        privacy_setting_id=3,
    )

    request = fake.requests[0]
    assert journal_id == 900
    assert request["variables"]["object"] == {
        "book_id": 202,
        "event": "note",
        "entry": "Minha nota privada",
        "privacy_setting_id": 3,
        "tags": [],
    }
    assert "Minha nota privada" not in request["query"]


@pytest.mark.parametrize(
    ("method_name", "kwargs"),
    [
        (
            "start_read",
            {
                "user_book_id": 0,
                "edition_id": 303,
                "page": 1,
                "started_at": date(2026, 1, 1),
            },
        ),
        (
            "update_read",
            {
                "read_id": 0,
                "edition_id": 303,
                "page": 1,
                "started_at": date(2026, 1, 1),
            },
        ),
        (
            "start_read",
            {
                "user_book_id": 101,
                "edition_id": 303,
                "page": 1,
                "started_at": datetime(2026, 1, 1),
            },
        ),
        (
            "update_read",
            {
                "read_id": 405,
                "edition_id": 303,
                "page": -1,
                "started_at": date(2026, 1, 1),
            },
        ),
        ("finish_read", {"read_id": 405, "finished_at": "2026-10-23"}),
        ("update_status", {"user_book_id": 101, "status_id": 4}),
        ("update_rating", {"user_book_id": 101, "rating": 3.3}),
        ("update_rating", {"user_book_id": 101, "rating": True}),
        ("add_journal_entry", {"book_id": 202, "entry": "  ", "privacy_setting_id": 1}),
        ("add_journal_entry", {"book_id": 202, "entry": "Nota", "privacy_setting_id": 9}),
    ],
)
def test_mutations_reject_invalid_inputs_before_network(
    method_name: str, kwargs: dict[str, Any]
) -> None:
    fake = HardcoverMutationFake()
    method = getattr(HardcoverClient(opener=fake), method_name)

    with pytest.raises(HardcoverError) as error:
        method("private-token", **kwargs)

    assert error.value.reason_code == "invalid_request"
    assert fake.requests == []
