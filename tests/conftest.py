"""测试夹具：环境变量先行，临时 SQLite 库，逐用例清库。"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="t_system_test_"))
os.environ["T_SYSTEM_DB_URL"] = "sqlite:///" + (_TMP / "test.db").as_posix()
os.environ["T_SYSTEM_EPORTAL_MODE"] = "mock"
os.environ["T_SYSTEM_RETRY_BACKOFF"] = "0"
os.environ["T_SYSTEM_LOCK_TTL"] = "300"
os.environ["T_SYSTEM_WRITEBACK_RETRIES"] = "3"

import pytest  # noqa: E402

TOKENS = {
    "admin": "tok-admin-001",
    "sales1": "tok-sales-001",
    "sales2": "tok-sales-002",
    "zhimou": "tok-zhimou-001",
}


def headers(user: str) -> dict:
    return {"X-User-Token": TOKENS[user]}


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import create_app, init_db

    init_db()
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_db(client):
    """每个用例独立数据：清空业务表（保留用户与模板），复位故障注入与 ticket/会话存储。"""
    import app.models as m
    from app.adapters.eportal import FAULT, _MOCK_TICKETS, _MOCK_TICKETS_LOCK
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        for name in ("EportalWriteLog", "EportalOrder", "CorrectionCase",
                     "HitLog", "History", "MemoryRule", "Order"):
            model = getattr(m, name, None)
            if model is not None:
                db.query(model).delete()
        db.commit()
    finally:
        db.close()
    FAULT["update_fail_times"] = 0
    with _MOCK_TICKETS_LOCK:
        _MOCK_TICKETS.clear()
    try:
        from app.edit_session import STORE
        STORE.clear()
    except ImportError:
        pass
    yield
    FAULT["update_fail_times"] = 0
    with _MOCK_TICKETS_LOCK:
        _MOCK_TICKETS.clear()


@pytest.fixture
def session():
    """直连数据库会话（Agent/案例类测试用）。"""
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- 常用操作封装 ----------

def intake(client, customer: str, fields: dict, task_id: str | None = None):
    resp = client.post("/api/intake", json={"customer_name": customer, "fields": fields, "task_id": task_id},
                       headers=headers("zhimou"))
    assert resp.status_code == 200, resp.text
    return resp.json()


def create_rule(client, customer: str, field: str, old: str, new: str, rule_type: str = "permanent",
                effective_count: int = 1) -> dict:
    resp = client.post("/api/rules", json={
        "customer_name": customer, "field_name": field, "old_value": old, "new_value": new,
        "rule_type": rule_type, "effective_count": effective_count,
    }, headers=headers("admin"))
    assert resp.status_code == 200, resp.text
    return resp.json()


def get_order(client, order_id: int) -> dict:
    resp = client.get(f"/api/orders/{order_id}", headers=headers("sales1"))
    assert resp.status_code == 200, resp.text
    return resp.json()


def lock(client, order_id: int, user: str):
    return client.post(f"/api/orders/{order_id}/lock", json={}, headers=headers(user))


def save(client, order_id: int, changes: dict, memory_choices: dict | None = None,
         feedback_choices: dict | None = None, user: str = "sales1"):
    return client.post(f"/api/orders/{order_id}/save", json={
        "changes": changes, "memory_choices": memory_choices or {}, "feedback_choices": feedback_choices or {},
    }, headers=headers(user))


def eportal_form(client, form_id: str) -> dict:
    resp = client.get(f"/api/mock/eportal/orders/{form_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # 展平 schema 化字段，便于旧断言直接比较值
    data["fields"] = {k: (v["value"] if isinstance(v, dict) else v) for k, v in (data.get("fields") or {}).items()}
    return data


# ---------- ePortal ticket / 编辑会话 / 结构化订单助手 ----------

def create_mock_eportal_order(client, customer: str = "Acme", fields: dict | None = None,
                              items: list | None = None, attachments: list | None = None,
                              auto_modified: dict | None = None, version: int = 1) -> dict:
    """通过 mock ePortal 创建 schema 化订单，返回 {order_id, version, ...}。"""
    resp = client.post("/api/mock/eportal/orders", json={
        "customer_name": customer,
        "fields": fields if fields is not None else {"tax_structure": "13%"},
        "items": items, "attachments": attachments, "auto_modified": auto_modified or {},
        "version": version,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def start_edit_session(client, order: dict, user: tuple[str, str] = ("sales1", "张销售")) -> dict:
    """给订单签发一次性 ticket 并换取 T 编辑会话（cookie 由 TestClient 保持）。"""
    from app.adapters.eportal import issue_mock_ticket

    ticket = issue_mock_ticket(order["order_id"], user[0], user[1], version=order["version"])
    resp = client.post("/api/eportal/session", json={"ticket": ticket})
    assert resp.status_code == 200, resp.text
    return resp.json()


def valid_save_payload(version: int, changes: dict | None = None, **extra) -> dict:
    payload = {"expected_version": version, "changes": changes or {"tax_structure": "CN_VAT13"},
               "items": None, "attachments": None, "error_descriptions": {}}
    payload.update(extra)
    return payload
