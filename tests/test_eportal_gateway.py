from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest

from app.adapters import eportal
from app.adapters.eportal import (
    EPortalConflictError,
    EPortalError,
    HttpEPortalAdapter,
    MockEPortalAdapter,
    issue_mock_ticket,
)


def test_mock_ticket_is_single_use_and_returns_operator_context():
    adapter = MockEPortalAdapter()
    ticket = issue_mock_ticket("SO-1", "sales1", "张销售", version=3)

    context = adapter.exchange_ticket(ticket)

    assert context.order_id == "SO-1"
    assert context.version == 3
    assert context.user_id == "sales1"
    assert context.user_name == "张销售"
    with pytest.raises(EPortalError):
        adapter.exchange_ticket(ticket)


def test_mock_ticket_can_only_be_exchanged_once_concurrently():
    adapter = MockEPortalAdapter()
    ticket = issue_mock_ticket("SO-1", "sales1", "张销售", version=3)
    start = Barrier(8)

    def exchange():
        start.wait()
        try:
            return adapter.exchange_ticket(ticket)
        except EPortalError:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: exchange(), range(8)))

    assert [result for result in results if result is not None] == [
        eportal.EditContext("SO-1", 3, "sales1", "张销售")
    ]


def test_issuing_a_ticket_removes_expired_ticket_entries():
    expired = issue_mock_ticket("SO-old", "sales1", "张销售", version=1, expires_in_seconds=-1)

    issue_mock_ticket("SO-new", "sales1", "张销售", version=2)

    assert expired not in eportal._MOCK_TICKETS


def test_http_adapter_uses_internal_ticket_endpoint_and_service_token(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={
            "user": {"id": "sales1", "name": "张销售"}, "order_id": "SO-1", "version": 3,
        })

    monkeypatch.setattr(eportal.httpx, "post", post)
    monkeypatch.setattr(eportal.settings, "eportal_base_url", "https://eportal.example")
    monkeypatch.setattr(eportal.settings, "eportal_service_token", "service-secret")

    context = HttpEPortalAdapter().exchange_ticket("opaque-ticket")

    assert context.order_id == "SO-1"
    assert calls == [("https://eportal.example/internal/t-system/tickets/exchange", {
        "json": {"ticket": "opaque-ticket"},
        "headers": {"Content-Type": "application/json", "Authorization": "Bearer service-secret"},
        "timeout": 15,
    })]


def test_http_adapter_translates_update_conflict(monkeypatch):
    monkeypatch.setattr(
        eportal.httpx, "patch", lambda *args, **kwargs: httpx.Response(409, text="newer version")
    )

    with pytest.raises(EPortalConflictError):
        HttpEPortalAdapter().update_order_for_edit(
            "SO-1", {"id": "sales1", "name": "张销售"}, 3, {"ship_to": "Shanghai"}
        )
