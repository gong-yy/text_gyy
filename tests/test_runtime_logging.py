def test_runtime_logger_writes_utf8_daily_log(monkeypatch, tmp_path):
    from app.config import settings
    from app.logging_config import configure_runtime_logging

    monkeypatch.setattr(settings, "log_dir", str(tmp_path))
    logger = configure_runtime_logging()
    logger.info("intake task_id=T001532 accepted")
    for handler in logger.handlers:
        handler.flush()

    files = list(tmp_path.glob("t_system_*.log"))
    assert len(files) == 1
    assert "task_id=T001532 accepted" in files[0].read_text(encoding="utf-8")


def test_runtime_logger_records_structured_audit_event(monkeypatch, tmp_path):
    from app.config import settings
    from app.logging_config import audit, configure_runtime_logging

    monkeypatch.setattr(settings, "log_dir", str(tmp_path))
    logger = configure_runtime_logging()
    audit("t2_save", order_id=7, intellisight_id="T001533", status="synced")
    for handler in logger.handlers:
        handler.flush()

    content = next(tmp_path.glob("t_system_*.log")).read_text(encoding="utf-8")
    assert "event=t2_save" in content
    assert "order_id=7" in content
    assert "intellisight_id=T001533" in content


def test_runtime_logger_removes_only_expired_daily_logs(monkeypatch, tmp_path):
    from app.config import settings
    from app.logging_config import configure_runtime_logging

    expired = date.today() - timedelta(days=31)
    retained = date.today() - timedelta(days=30)
    (tmp_path / f"t_system_{expired:%Y-%m-%d}.log").write_text("expired", encoding="utf-8")
    (tmp_path / f"t_system_{retained:%Y-%m-%d}.log").write_text("retained", encoding="utf-8")
    unrelated = tmp_path / "other.log"
    unrelated.write_text("keep", encoding="utf-8")

    monkeypatch.setattr(settings, "log_dir", str(tmp_path))
    configure_runtime_logging()

    assert not (tmp_path / f"t_system_{expired:%Y-%m-%d}.log").exists()
    assert (tmp_path / f"t_system_{retained:%Y-%m-%d}.log").exists()
    assert unrelated.exists()
from datetime import date, timedelta
