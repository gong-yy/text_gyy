"""认证：mock 模式（本地账号+静态令牌）；sso 模式预留统一登录对接点。"""
import hashlib

from sqlalchemy.orm import Session

from .config import settings
from .models import User


def hash_password(password: str) -> str:
    return hashlib.sha256(("t-system::" + password).encode("utf-8")).hexdigest()


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.query(User).filter(User.username == username, User.enabled == True).first()  # noqa: E712
    if user and user.password_hash == hash_password(password):
        return user
    return None


def resolve_token(db: Session, token: str) -> User | None:
    if not token:
        return None
    return db.query(User).filter(User.token == token, User.enabled == True).first()  # noqa: E712


def sso_exchange(db: Session, sso_token: str) -> User | None:
    """ePortal 跳转 T2 携带的身份令牌 → T 系统操作人（免登）。

    mock 模式：令牌即本地静态令牌；真实 SSO 对接时替换此实现（令牌互认/换票）。
    """
    if settings.auth_mode != "mock":
        raise NotImplementedError("SSO 模式未配置：请实现 sso_exchange 的真实对接")
    return resolve_token(db, sso_token)
