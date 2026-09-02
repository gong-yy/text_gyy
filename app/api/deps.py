"""API 依赖：身份令牌解析与角色校验。"""
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..auth import resolve_token
from ..db import get_db
from ..models import User


def get_current_user(
    authorization: str | None = Header(None),
    x_user_token: str | None = Header(None, alias="X-User-Token"),
    db: Session = Depends(get_db),
) -> User:
    raw = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    raw = raw or (x_user_token.strip() if x_user_token else None)
    if not raw:
        raise HTTPException(status_code=401, detail="缺少身份令牌")
    user = resolve_token(db, raw)
    if not user:
        raise HTTPException(status_code=401, detail="身份令牌无效或已停用")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def require_service(user: User = Depends(get_current_user)) -> User:
    """智眸等服务账号或管理员。"""
    if user.role not in ("service", "admin"):
        raise HTTPException(status_code=403, detail="需要服务账号权限")
    return user
