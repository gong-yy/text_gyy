"""认证接口：登录 / SSO 令牌互换（ePortal 跳转 T2 免登）/ 当前用户。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import authenticate, resolve_token, sso_exchange
from ..db import get_db
from ..models import User
from ..schemas import LoginRequest, SsoExchangeRequest
from .deps import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_payload(user: User) -> dict:
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role,
            "token": user.token}


@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate(db, body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return _user_payload(user)


@router.post("/sso")
def sso(body: SsoExchangeRequest, db: Session = Depends(get_db)):
    """ePortal【修改】按钮跳转携带身份令牌 → T 系统识别操作人并免登。"""
    user = sso_exchange(db, body.sso_token)
    if not user:
        raise HTTPException(status_code=401, detail="SSO 令牌无效")
    return _user_payload(user)


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _user_payload(user)
