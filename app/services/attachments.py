"""智眸附件留存：以任务号分目录，保留原文件名供 ePortal 转发和审计。"""
from pathlib import Path

from ..config import settings


def store_intake_attachments(attachments: list[dict], task_id: str | None, order_id: int) -> list[dict]:
    """将上传内容落盘，返回不含文件字节的可审计元数据。"""
    if not attachments:
        return []
    # task_id 来自外部系统，不能直接作为路径；无 task_id 时以本地订单编号隔离。
    group = _safe_component(task_id or f"order-{order_id}")
    directory = Path(settings.attachment_storage_dir) / group
    directory.mkdir(parents=True, exist_ok=True)
    stored = []
    for attachment in attachments:
        filename = _safe_filename(str(attachment.get("filename") or "attachment"))
        path = directory / filename
        path.write_bytes(attachment["content"])
        stored.append({
            "field_name": str(attachment["field_name"]),
            "filename": filename,
            "size": len(attachment["content"]),
            "content_type": str(attachment.get("content_type") or "application/octet-stream"),
            "path": str(path),
        })
    return stored


def _safe_component(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in value) or "unknown"


def _safe_filename(value: str) -> str:
    return Path(value).name or "attachment"
