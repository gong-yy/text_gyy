from datetime import datetime, timezone


def utcnow() -> datetime:
    """统一使用无时区 UTC 时间，兼容 SQLite / MySQL。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)
