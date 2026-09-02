"""服务器端编辑会话：ticket 换来的操作人/订单上下文只存 T 内存，
浏览器后续仅携带 HttpOnly 会话 cookie，不在 URL 暴露任何身份或令牌。
"""
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import settings
from .util import utcnow

COOKIE_NAME = "t_edit_session"

# 会话有效期不高于 ticket 有效期（ePortal 约定 2~5 分钟）
SESSION_TTL_SECONDS = min(settings.ticket_ttl_seconds, 300)

STORE: dict[str, "EditSession"] = {}


@dataclass(frozen=True)
class EditSession:
    session_id: str
    order_id: str
    version: int
    user_id: str
    user_name: str
    created_at: datetime
    expires_at: datetime


def _prune_expired() -> None:
    now = utcnow()
    for key in [k for k, s in STORE.items() if s.expires_at <= now]:
        STORE.pop(key, None)


def create_session(context) -> EditSession:
    """由 EditContext 创建短时编辑会话。"""
    _prune_expired()
    session_id = secrets.token_urlsafe(32)
    now = utcnow()
    session = EditSession(
        session_id=session_id,
        order_id=context.order_id,
        version=context.version,
        user_id=context.user_id,
        user_name=context.user_name,
        created_at=now,
        expires_at=now + timedelta(seconds=SESSION_TTL_SECONDS),
    )
    STORE[session_id] = session
    return session


def get_session(session_id: str | None) -> EditSession | None:
    if not session_id:
        return None
    session = STORE.get(session_id)
    if session is None:
        return None
    if session.expires_at <= utcnow():
        STORE.pop(session_id, None)
        return None
    return session


def drop_session(session_id: str | None) -> None:
    if session_id:
        STORE.pop(session_id, None)
