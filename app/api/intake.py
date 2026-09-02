"""智眸 → T 接入接口（接口 1：推送结构化字段 + 原值 + 客户名）。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..schemas import IntakeRequest
from ..services.order import BizError, intake, order_detail
from .deps import require_service

router = APIRouter(prefix="/api/intake", tags=["intake"])


@router.post("")
def receive_from_zhimou(body: IntakeRequest, user: User = Depends(require_service), db: Session = Depends(get_db)):
    """② 智眸识别后流转：T1 记忆匹配（命中改/未命中保持）→ ③ 建单送 ePortal。"""
    try:
        order = intake(
            db,
            customer_name=body.customer_name,
            fields=dict(body.fields),
            task_id=body.task_id,
            meta=body.meta,
        )
    except BizError as exc:
        raise exc
    detail = order_detail(db, order)
    return {
        "order_id": detail["order_id"],
        "form_id": detail["form_id"],
        "status": detail["status"],
        "customer_name": detail["customer_name"],
        "applied_memory": detail["applied_memory"],  # 命中归因（哪些字段被自动修改、依据哪条记忆）
        "last_error": detail["last_error"],
        "message": "已建单生成预订单草稿" if detail["status"] == "created" else "建单失败，订单已留在 T 待处理列表",
    }
