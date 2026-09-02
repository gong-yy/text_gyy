"""配置加载：环境变量 > config.ini > 内置默认。"""
import configparser
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_DEFAULTS = {
    "app": {"host": "127.0.0.1", "port": "8300"},
    "db": {"url": "sqlite:///" + (ROOT / "t_system.db").as_posix()},
    "eportal": {
        "mode": "mock",
        "base_url": "http://127.0.0.1:8400",
        "api_key": "",
        "service_token": "",
        "create_path": "/api/orders",
        "update_path": "/api/forms/{form_id}",
        "get_path": "/api/forms/{form_id}",
        "ticket_exchange_path": "/internal/t-system/tickets/exchange",
        "order_for_edit_path": "/internal/t-system/orders/{order_id}",
        "order_update_for_edit_path": "/internal/t-system/orders/{order_id}",
        "ticket_ttl_seconds": "300",
    },
    "auth": {"mode": "mock"},
    "agent": {"endpoint": "", "api_key": "", "timeout_seconds": "30"},
    "normalize": {"steps": "trim,full2half,lower,collapse_spaces,percent_decimal"},
    "lock": {"ttl_seconds": "300"},
    "writeback": {"max_retries": "3", "retry_backoff_seconds": "0.5"},
}


def _load() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read_dict(_DEFAULTS)
    path = os.environ.get("T_SYSTEM_CONFIG", str(ROOT / "config.ini"))
    if os.path.exists(path):
        cfg.read(path, encoding="utf-8")
    return cfg


_cfg = _load()


def _get(section: str, key: str, env: str) -> str:
    return os.environ.get(env) or _cfg.get(section, key, fallback="")


class Settings:
    def __init__(self) -> None:
        self.host = _get("app", "host", "T_SYSTEM_HOST")
        self.port = int(_get("app", "port", "T_SYSTEM_PORT") or 8300)
        self.db_url = _get("db", "url", "T_SYSTEM_DB_URL")
        self.eportal_mode = _get("eportal", "mode", "T_SYSTEM_EPORTAL_MODE")
        self.eportal_base_url = _get("eportal", "base_url", "T_SYSTEM_EPORTAL_BASE_URL")
        self.eportal_api_key = _get("eportal", "api_key", "T_SYSTEM_EPORTAL_API_KEY")
        self.eportal_service_token = _get("eportal", "service_token", "T_SYSTEM_EPORTAL_SERVICE_TOKEN")
        self.eportal_create_path = _get("eportal", "create_path", "T_SYSTEM_EPORTAL_CREATE_PATH")
        self.eportal_update_path = _get("eportal", "update_path", "T_SYSTEM_EPORTAL_UPDATE_PATH")
        self.eportal_get_path = _get("eportal", "get_path", "T_SYSTEM_EPORTAL_GET_PATH")
        self.eportal_ticket_exchange_path = _get(
            "eportal", "ticket_exchange_path", "T_SYSTEM_EPORTAL_TICKET_EXCHANGE_PATH"
        )
        self.eportal_order_for_edit_path = _get(
            "eportal", "order_for_edit_path", "T_SYSTEM_EPORTAL_ORDER_FOR_EDIT_PATH"
        )
        self.eportal_order_update_for_edit_path = _get(
            "eportal", "order_update_for_edit_path", "T_SYSTEM_EPORTAL_ORDER_UPDATE_FOR_EDIT_PATH"
        )
        self.ticket_ttl_seconds = int(_get("eportal", "ticket_ttl_seconds", "T_SYSTEM_EPORTAL_TICKET_TTL") or 300)
        self.agent_endpoint = _get("agent", "endpoint", "T_SYSTEM_AGENT_ENDPOINT")
        self.agent_api_key = _get("agent", "api_key", "T_SYSTEM_AGENT_API_KEY")
        self.agent_timeout = float(_get("agent", "timeout_seconds", "T_SYSTEM_AGENT_TIMEOUT") or 30)
        self.auth_mode = _get("auth", "mode", "T_SYSTEM_AUTH_MODE")
        steps = _get("normalize", "steps", "T_SYSTEM_NORMALIZE_STEPS")
        self.normalize_steps = [s.strip() for s in steps.split(",") if s.strip()]
        self.lock_ttl_seconds = int(_get("lock", "ttl_seconds", "T_SYSTEM_LOCK_TTL") or 300)
        self.max_retries = int(_get("writeback", "max_retries", "T_SYSTEM_WRITEBACK_RETRIES") or 3)
        self.retry_backoff = float(_get("writeback", "retry_backoff_seconds", "T_SYSTEM_RETRY_BACKOFF") or 0.5)


settings = Settings()
