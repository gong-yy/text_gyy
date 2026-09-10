"""T2 人工修改界面相关接口：订单列表/详情、编辑锁、保存回写、失败重发。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..logging_config import audit
from ..lock import acquire, release
from ..models import Order, User
from ..schemas import SaveChangesRequest
from ..services.order import BizError, order_detail, resend, save_changes
from ..services.zhimou_callback import resend_final_result
from .deps import get_t2_operator

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _get_order(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"订单不存在：{order_id}")
    return order


@router.get("/lookup")
def lookup_order_by_intellisight_id(
    intellisight_id: str = Query(..., min_length=1),
    user: User = Depends(get_t2_operator),
    db: Session = Depends(get_db),
):
    """供 ePortal 用智眸订单号打开 T2 时，解析为 T 系统本地订单编号。"""
    order = db.query(Order).filter(Order.zhimou_task_id == intellisight_id).one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail=f"未找到 intellisight_id 对应的本地订单：{intellisight_id}")
    return {
        "order_id": order.id,
        "intellisight_id": order.zhimou_task_id,
        "form_id": order.form_id,
    }


@router.get("")
def list_orders(
    status: str | None = Query(None),
    customer: str | None = Query(None),
    user: User = Depends(get_t2_operator),
    db: Session = Depends(get_db),
):
    q = db.query(Order)
    if status:
        q = q.filter(Order.status == status)
    if customer:
        q = q.filter(Order.customer_name.contains(customer))
    rows = q.order_by(Order.id.desc()).limit(200).all()
    return [
        {
            "order_id": o.id,
            "form_id": o.form_id,
            "customer_name": o.customer_name,
            "status": o.status,
            "version": o.version,
            "holder": o.locked_by_name if o.locked_by else None,
            "updated_at": o.updated_at.isoformat(sep=" ") if o.updated_at else None,
        }
        for o in rows
    ]


@router.get("/{order_id}")
def get_order(order_id: int, user: User = Depends(get_t2_operator), db: Session = Depends(get_db)):
    return order_detail(db, _get_order(db, order_id))


@router.post("/{order_id}/lock")
def lock_order(order_id: int, user: User = Depends(get_t2_operator), db: Session = Depends(get_db)):
    """进入 T2 时加锁/续期；并发冲突返回 423 + 「当前正被 XXX 编辑」。"""
    ok, holder = acquire(db, _get_order(db, order_id), user)
    if not ok:
        audit("t2_lock_rejected", order_id=order_id, operator=user.username, holder=holder)
        raise HTTPException(status_code=423, detail=f"当前正被 {holder} 编辑")
    audit("t2_lock", order_id=order_id, operator=user.username)
    return {"ok": True, "holder": user.display_name or user.username}


@router.post("/{order_id}/unlock")
def unlock_order(order_id: int, user: User = Depends(get_t2_operator), db: Session = Depends(get_db)):
    release(db, _get_order(db, order_id), user)
    audit("t2_unlock", order_id=order_id, operator=user.username)
    return {"ok": True}


@router.post("/{order_id}/save")
def save_order(order_id: int, body: SaveChangesRequest, user: User = Depends(get_t2_operator),
               db: Session = Depends(get_db)):
    """T2 保存：记忆确认对话框选项 + 负反馈选项随修改提交，保存后同步回写 ePortal。"""
    try:
        audit("t2_save_requested", order_id=order_id, operator=user.username,
              changed_fields=",".join(sorted(body.changes)))
        result = save_changes(
            db,
            _get_order(db, order_id),
            user,
            changes=dict(body.changes),
            items=body.items,
            memory_choices=dict(body.memory_choices),
            feedback_choices=dict(body.feedback_choices),
        )
        audit("t2_save_completed", order_id=order_id, operator=user.username,
              status=result.get("status"), form_id=result.get("form_id"))
        return result
    except BizError as exc:
        audit("t2_save_rejected", order_id=order_id, operator=user.username, status=exc.status_code, reason=exc.message)
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.post("/{order_id}/resend")
def resend_order(order_id: int, user: User = Depends(get_t2_operator), db: Session = Depends(get_db)):
    """回写失败后手动重发（T2 数据本地留存不丢失）。"""
    try:
        audit("t2_resend_requested", order_id=order_id, operator=user.username)
        result = resend(db, _get_order(db, order_id))
        audit("t2_resend_completed", order_id=order_id, operator=user.username, status=result.get("status"))
        return result
    except BizError as exc:
        audit("t2_resend_rejected", order_id=order_id, operator=user.username, status=exc.status_code, reason=exc.message)
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.post("/{order_id}/zhimou-callback/resend")
def resend_zhimou_callback(
    order_id: int,
    user: User = Depends(get_t2_operator),
    db: Session = Depends(get_db),
):
    """重新发送已记录的智眸最终建单结果，不会再次调用 ePortal。"""
    order = _get_order(db, order_id)
    try:
        ok = resend_final_result(db, order)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.refresh(order)
    return {"ok": ok, "callback": (order.payload or {}).get("zhimou_callback")}
