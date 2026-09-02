"""ePortal 编辑会话接口：换票建会话（HttpOnly cookie）→ 会话内拉单 / 保存。

浏览器 URL 只携带一次性 opaque ticket；操作人身份只存在 T 服务端会话中。
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..adapters.eportal import EPortalError, get_adapter
from ..db import get_db
from ..edit_session import (COOKIE_NAME, SESSION_TTL_SECONDS, EditSession,
                            create_session, get_session)
from ..schemas import SaveOrderRequest, TicketExchangeRequest
from ..services.order import BizError, save_eportal_changes

router = APIRouter(prefix="/api/eportal", tags=["eportal-edit-session"])


@router.post("/session")
def begin_edit_session(body: TicketExchangeRequest, response: Response):
    """ticket（一次性）→ 服务端编辑会话 + HttpOnly cookie。"""
    try:
        context = get_adapter().exchange_ticket(body.ticket)
    except EPortalError:
        raise HTTPException(status_code=401, detail="ticket 无效、已使用或已过期，请从 ePortal 重新进入")
    session = create_session(context)
    response.set_cookie(
        COOKIE_NAME, session.session_id,
        max_age=SESSION_TTL_SECONDS, httponly=True, samesite="lax", path="/",
    )
    return {
        "operator": {"id": session.user_id, "name": session.user_name},
        "order_id": session.order_id,
        "version": session.version,
    }


def get_edit_session(request: Request) -> EditSession:
    """FastAPI 依赖：从 HttpOnly cookie 解析服务器端编辑会话。"""
    session = get_session(request.cookies.get(COOKIE_NAME))
    if session is None:
        raise HTTPException(status_code=401, detail="编辑会话无效或已过期，请从 ePortal 重新进入")
    return session


@router.get("/orders/current")
def current_order(session: EditSession = Depends(get_edit_session)):
    """当前会话绑定的订单详情（数据一律经 ePortal 适配器加载）。"""
    operator = {"id": session.user_id, "name": session.user_name}
    try:
        order = get_adapter().get_order_for_edit(session.order_id, session.user_id)
    except EPortalError as exc:
        return {"operator": operator, "order": None, "message": str(exc)}
    return {"operator": operator, "order": order}


@router.post("/orders/current/save")
def save_current_order(body: SaveOrderRequest, background_tasks: BackgroundTasks,
                       session: EditSession = Depends(get_edit_session),
                       db: Session = Depends(get_db)):
    """版本化保存：schema 校验 → 回写 ePortal（409 不覆盖）→ 留痕/记忆/纠正案例。"""
    try:
        return save_eportal_changes(db, background_tasks, session, body)
    except BizError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
