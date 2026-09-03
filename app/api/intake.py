"""Zhimu intake: standard JSON and legacy multipart compatibility."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..schemas import IntakeRequest
from ..services.order import BizError, intake, order_detail
from .deps import require_service

router = APIRouter(tags=["intake"])

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


async def _read_intake_body(request: Request) -> tuple[IntakeRequest, list | None]:
    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" not in content_type:
        try:
            return IntakeRequest.model_validate(await request.json()), None
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
    reserved = set(_LEGACY_FIELD_MAP) | {"task_id", "products"}
    fields.update({key: value for key, value in legacy.items() if key not in reserved})
    products = legacy.get("products")
    if products is not None and (not isinstance(products, list) or any(not isinstance(row, dict) for row in products)):
        raise HTTPException(status_code=422, detail="products must be an array of objects")
    return IntakeRequest(
        customer_name=str(legacy.get("customer_name") or ""),
        task_id=str(legacy["task_id"]) if legacy.get("task_id") is not None else None,
        fields=fields,
        meta={"source_format": "zhimou_legacy_multipart"},
    ), products


@router.post("/api/intake")
@router.post("/")
async def receive_from_zhimou(request: Request, user: User = Depends(require_service), db: Session = Depends(get_db)):
    body, legacy_items = await _read_intake_body(request)
    try:
        order = intake(
            db,
            customer_name=body.customer_name,
            fields=dict(body.fields),
            task_id=body.task_id,
            meta=body.meta,
            items=legacy_items,
        )
    except BizError as exc:
        raise exc
    detail = order_detail(db, order)
    return {
        "order_id": detail["order_id"],
        "form_id": detail["form_id"],
        "status": detail["status"],
        "customer_name": detail["customer_name"],
        "applied_memory": detail["applied_memory"],
        "last_error": detail["last_error"],
        "message": "created" if detail["status"] == "created" else "create failed",
    }
