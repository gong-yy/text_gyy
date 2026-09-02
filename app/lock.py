"""并发编辑锁：同一订单不允许两人同时编辑，后进入者提示「当前正被 XXX 编辑」。"""
from sqlalchemy.orm import Session

from .config import settings
from .models import Order, User
from .util import utcnow


def lock_holder(order: Order) -> str | None:
    if not order.locked_by:
        return None
    stale = order.locked_at and (utcnow() - order.locked_at).total_seconds() >= settings.lock_ttl_seconds
    if stale:
        return None
    return order.locked_by_name or order.locked_by


def is_locked_by_other(order: Order, username: str) -> bool:
    holder_id = order.locked_by
    if not holder_id or holder_id == username:
        return False
    return lock_holder(order) is not None


def acquire(db: Session, order: Order, user: User) -> tuple[bool, str | None]:
    """获取/续期锁。被他人持有时返回 (False, 持有者姓名)。"""
    if is_locked_by_other(order, user.username):
        return False, lock_holder(order)
    order.locked_by = user.username
    order.locked_by_name = user.display_name or user.username
    order.locked_at = utcnow()
    db.commit()
    return True, None


def release(db: Session, order: Order, user: User) -> None:
    if order.locked_by in (None, user.username):
        order.locked_by = None
        order.locked_by_name = None
        order.locked_at = None
        db.commit()
