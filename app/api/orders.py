"""T2 人工修改界面相关接口：订单列表/详情、编辑锁、保存回写、失败重发。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..lock import acquire, release
from ..models import Order, User
from ..schemas import SaveChangesRequest
from ..services.order import BizError, order_detail, resend, save_changes
from .deps import get_current_user

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _get_order(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"订单不存在：{order_id}")
    return order


@router.get("")
def list_orders(
    status: str | None = Query(None),
    customer: str | None = Query(None),
    user: User = Depends(get_current_user),
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
def get_order(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return order_detail(db, _get_order(db, order_id))


@router.post("/{order_id}/lock")
def lock_order(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """进入 T2 时加锁/续期；并发冲突返回 423 + 「当前正被 XXX 编辑」。"""
    ok, holder = acquire(db, _get_order(db, order_id), user)
    if not ok:
        raise HTTPException(status_code=423, detail=f"当前正被 {holder} 编辑")
    return {"ok": True, "holder": user.display_name or user.username}


@router.post("/{order_id}/unlock")
def unlock_order(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    release(db, _get_order(db, order_id), user)
    return {"ok": True}


@router.post("/{order_id}/save")
def save_order(order_id: int, body: SaveChangesRequest, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """T2 保存：记忆确认对话框选项 + 负反馈选项随修改提交，保存后同步回写 ePortal。"""
    try:
        return save_changes(
            db,
            _get_order(db, order_id),
            user,
            changes=dict(body.changes),
            memory_choices=dict(body.memory_choices),
            feedback_choices=dict(body.feedback_choices),
        )
    except BizError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.post("/{order_id}/resend")
def resend_order(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """回写失败后手动重发（T2 数据本地留存不丢失）。"""
    try:
        return resend(db, _get_order(db, order_id))
    except BizError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
