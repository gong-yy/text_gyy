from pathlib import Path

from openpyxl import Workbook


def _workbook(path: Path) -> None:
    wb = Workbook()
    fields = wb.active
    fields.title = "字段替换表"
    fields.append(["Customer Name", "字段名", "沉淀值", "替换值", "OA规则/意见"])
    fields.append(["甲公司", "Customer Payment Term", "60D", "90D", "默认90D"])
    fields.append(["甲公司", "Exchange Rate", "1", None, None])

    customers = wb.create_sheet("Sheet1")
    customers.append(["智眸原值", "ePortal目标值", "转换判断"])
    customers.append(["Acme Ltd.", "甲公司", "智眸值 → ePortal值"])
    customers.append([None, "忽略公司", "智眸值 → ePortal值"])
    customers.append(["Conflict Ltd.", "冲突甲", "智眸值 → ePortal值"])
    customers.append(["Conflict Ltd.", "冲突乙", "智眸值 → ePortal值"])
    wb.save(path)


def test_import_plan_uses_explicit_replacements_and_nonconflicting_customer_mappings(tmp_path):
    """缺少替换值、空原值和同原值冲突的行绝不能成为自动替换规则。"""
    from app.import_rules import build_import_plan

    source = tmp_path / "rules.xlsx"
    _workbook(source)

    plan = build_import_plan(source)

    assert [(r.customer_name, r.field_name, r.old_value, r.new_value) for r in plan.records] == [
        ("甲公司", "Customer Payment Term", "60D", "90D"),
        ("Acme Ltd.", "Customer Name", "Acme Ltd.", "甲公司"),
    ]
    assert plan.skipped_missing_replacement == 1
    assert plan.skipped_empty_source == 1
    assert plan.skipped_conflicts == 2


def test_import_rules_is_idempotent_and_never_overwrites_existing_rule(tmp_path, session):
    """重复导入或已有同键规则时，导入不得新增或覆盖既有规则。"""
    from app.import_rules import build_import_plan, import_plan
    from app.models import MemoryRule

    source = tmp_path / "rules.xlsx"
    _workbook(source)
    session.add(MemoryRule(
        customer_name="甲公司", field_name="Customer Payment Term", old_value="60D",
        new_value="已有值", rule_type="permanent", status="enabled",
    ))
    session.commit()

    result = import_plan(session, build_import_plan(source), dry_run=False)
    session.commit()

    assert result.inserted == 1
    assert result.skipped_existing == 1
    assert session.query(MemoryRule).filter_by(
        customer_name="甲公司", field_name="Customer Payment Term", old_value="60D"
    ).one().new_value == "已有值"
