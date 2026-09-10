"""T 系统运行日志：按启动日期落盘，UTF-8 编码。"""
import logging
import re
from datetime import date, timedelta
from pathlib import Path

from .config import settings

LOGGER_NAME = "t_system"
_DAILY_LOG_NAME = re.compile(r"^t_system_(\d{4}-\d{2}-\d{2})\.log$")
_RETENTION_DAYS = 30


def _remove_expired_daily_logs(directory: Path) -> None:
    """仅删除超过保留期的本系统按日日志，不触及目录中的其他文件。"""
    cutoff = date.today() - timedelta(days=_RETENTION_DAYS)
    for candidate in directory.iterdir():
        matched = _DAILY_LOG_NAME.fullmatch(candidate.name)
        if not candidate.is_file() or not matched:
            continue
        try:
            log_date = date.fromisoformat(matched.group(1))
            if log_date < cutoff:
                candidate.unlink()
        except (OSError, ValueError):
            # 正在被外部程序占用或日期异常时保留文件，下一次启动再尝试。
            continue


def configure_runtime_logging() -> logging.Logger:
    """返回写入当天日志文件的应用 logger；重复调用时安全重建文件处理器。"""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        if getattr(handler, "_t_system_runtime_log", False):
            logger.removeHandler(handler)
            handler.close()

    directory = Path(settings.log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    _remove_expired_daily_logs(directory)
    filename = directory / f"t_system_{date.today():%Y-%m-%d}.log"
    handler = logging.FileHandler(filename, encoding="utf-8")
    handler._t_system_runtime_log = True
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def audit(event: str, **fields: object) -> None:
    """记录可检索的业务审计事件；换行符替换为空格，避免伪造日志行。"""
    values = " ".join(
        f"{key}={str(value).replace(chr(10), ' ').replace(chr(13), ' ')}"
        for key, value in fields.items()
    )
    logging.getLogger(LOGGER_NAME).info("event=%s %s", event, values)
