"""向智眸回传 ePortal 最终建单结果。"""
from datetime import datetime

import httpx
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..config import settings
from ..models import Order


def report_final_result(
    db: Session,
    order: Order,
    *,
    success: bool,
    msg: str,
    eportal_id: str | None = None,
) -> bool:
    """发送一次回调并记录结果；任何回调故障均不影响本地建单状态。"""
    callback = dict((order.payload or {}).get("zhimou_callback") or {})
    attempts = int(callback.get("attempts") or 0) + 1
    request = {
        "task_id": order.zhimou_task_id,
        "success": success,
        "msg": msg,
    }
    if success:
        request["eportal_id"] = eportal_id or order.form_id or ""

    callback.update({
        "attempts": attempts,
        "request": request,
        "last_attempt_at": datetime.now().isoformat(timespec="seconds"),
    })
    if not order.zhimou_task_id:
        callback.update({"status": "skipped", "last_error": "缺少智眸 task_id"})
        _save_callback_state(db, order, callback)
        return False
    if success and not request["eportal_id"]:
        callback.update({"status": "failed", "last_error": "成功回调缺少 ePortal 单号"})
        _save_callback_state(db, order, callback)
        return False

    try:
        response = httpx.post(
            settings.zhimou_callback_url,
            json=request,
            headers={"Content-Type": "application/json; charset=UTF-8"},
            timeout=settings.zhimou_callback_timeout,
        )
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"智眸回调返回 HTTP {response.status_code}: {response.text[:200]}")
        body = response.json()
        if not isinstance(body, dict) or body.get("success") is not True:
            raise RuntimeError(f"智眸回调未确认成功: {body}")
    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        callback.update({"status": "failed", "last_error": str(exc)})
        _save_callback_state(db, order, callback)
        return False

    callback.update({"status": "succeeded", "last_error": None, "response": body})
    _save_callback_state(db, order, callback)
    return True


def resend_final_result(db: Session, order: Order) -> bool:
    """按订单已落库的最终结果重新发送智眸回调，不会重新创建 ePortal 单据。"""
    if order.status in ("created", "synced") and order.form_id:
        return report_final_result(
            db,
            order,
            success=True,
            msg="ePortal 创单成功",
            eportal_id=order.form_id,
        )
    if order.status == "create_failed":
        return report_final_result(
            db,
            order,
            success=False,
            msg=order.last_error or "ePortal 创单失败",
        )
    raise ValueError(f"订单状态 {order.status} 尚无可回传的最终建单结果")


def _save_callback_state(db: Session, order: Order, callback: dict) -> None:
    payload = dict(order.payload or {})
    payload["zhimou_callback"] = callback
    order.payload = payload
    flag_modified(order, "payload")
    db.commit()
