"""Zhimu intake: standard JSON and legacy multipart compatibility."""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import IntakeRequest
from ..services.order import BizError, intake, order_detail

router = APIRouter(tags=["intake"])
logger = logging.getLogger("t_system")

_LEGACY_FIELD_MAP = {
    "customer_name": "Customer Name",
    "customer_id": "Customer ID",
    "sales_person": "Sales Person",
    "presales": "Presales",
    "quotation_ref": "Quotation Ref / PO No",
    "delivery_date": "Estimated Delivery Date To Customer",
    "customer_address": "Customer Delivery Address",
    "tax_structure": "Tax Structure",
    "customer_payment_term": "Customer Payment Term",
    "sales_bundling": "Sales Bundling",
    "sf_no": "SF No.",
    "exchange_rate": "Exchange Rate (for foreign currency)",
}


async def _read_intake_body(request: Request) -> tuple[IntakeRequest, list | None, list[dict]]:
    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" not in content_type:
        try:
            return IntakeRequest.model_validate(await request.json()), None, []
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="JSON request body is invalid") from exc

    form = await request.form()
    raw = form.get("data")
    if raw is None:
        raise HTTPException(status_code=422, detail="multipart request requires a data field")
    if not isinstance(raw, str):
        raw = await raw.read()
    try:
        legacy = json.loads(raw)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail="data must contain a JSON object") from exc
    if not isinstance(legacy, dict):
        raise HTTPException(status_code=422, detail="data must contain a JSON object")

    fields = {target: legacy[source] for source, target in _LEGACY_FIELD_MAP.items() if source in legacy}
    reserved = set(_LEGACY_FIELD_MAP) | {"task_id", "intellisight_id", "products"}
    fields.update({key: value for key, value in legacy.items() if key not in reserved})
    products = legacy.get("products")
    if products is not None and (not isinstance(products, list) or any(not isinstance(row, dict) for row in products)):
        raise HTTPException(status_code=422, detail="products must be an array of objects")
    attachments = []
    for field_name, value in form.multi_items():
        if field_name == "data" or not hasattr(value, "filename"):
            continue
        attachments.append({
            "field_name": field_name,
            "filename": value.filename or "attachment",
            "content_type": value.content_type or "application/octet-stream",
            "content": await value.read(),
        })

    return IntakeRequest(
        customer_name=str(legacy.get("customer_name") or ""),
        task_id=str(legacy.get("intellisight_id") or legacy.get("task_id") or "") or None,
        fields=fields,
        meta={"source_format": "zhimou_legacy_multipart"},
    ), products, attachments


@router.post("/api/intake")
@router.post("/")
async def receive_from_zhimou(request: Request, db: Session = Depends(get_db)):
    body, legacy_items, attachments = await _read_intake_body(request)
    logger.info(
        "智眸接入 task_id=%s customer=%s items=%s attachments=%s",
        body.intellisight_id or body.task_id, body.customer_name, len(legacy_items or []), len(attachments),
    )
    try:
        outcome = intake(
            db,
            customer_name=body.customer_name,
            fields=dict(body.fields),
            task_id=body.intellisight_id or body.task_id,
            meta=body.meta,
            items=legacy_items,
            attachments=attachments,
        )
    except BizError as exc:
        raise exc
    if outcome.eportal_response is not None:
        return outcome.eportal_response
    order = outcome.order
    if order is None:
        return {}
    detail = order_detail(db, order)
    logger.info("智眸建单完成 task_id=%s form_id=%s status=%s", detail["zhimou_task_id"], detail["form_id"], detail["status"])
    return {
        "order_id": detail["order_id"],
        "form_id": detail["form_id"],
        "status": detail["status"],
        "customer_name": detail["customer_name"],
        "applied_memory": detail["applied_memory"],
        "last_error": detail["last_error"],
        "message": "created" if detail["status"] == "created" else "create failed",
    }
