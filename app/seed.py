"""初始化种子数据：用户、默认预订单模板、演示记忆规则（仅空库时写入）。"""
from sqlalchemy.orm import Session

from .auth import hash_password
from .models import MemoryRule, TemplateField, User

USERS = [
    # username, password, display_name, role, token
    ("admin", "admin123", "管理员", "admin", "tok-admin-001"),
    ("sales1", "sales123", "张销售", "sales", "tok-sales-001"),
    ("sales2", "sales123", "李销售", "sales", "tok-sales-002"),
    ("zhimou", "zhimou123", "智眸服务", "service", "tok-zhimou-001"),
]

DEMO_RULES = [
    # 演示规则：供 /intake 演示页一键体验命中；真实规则由 T2 人工修改沉淀或管理员预置
    ("Acme Trading Co.", "Tax Structure", "13%", "CN VAT13"),
    ("Acme Trading Co.", "签约公司", "shanghai", "上海"),
]


def seed_all() -> None:
    from .db import SessionLocal
    from .eportal_schema import HEADER_FIELDS

    db = SessionLocal()
    try:
        if db.query(User.id).count() == 0:
            for username, password, display_name, role, token in USERS:
                db.add(
                    User(
                        username=username,
                        display_name=display_name,
                        password_hash=hash_password(password),
                        role=role,
                        token=token,
                    )
                )
        if db.query(TemplateField.id).count() == 0:
            for idx, (name, label, _type, required, editable, _options, _group) in enumerate(HEADER_FIELDS):
                if editable:
                    db.add(TemplateField(customer_name="*", field_name=name, label=label,
                                         display_order=(idx + 1) * 10, visible=True, editable=True))
        if db.query(MemoryRule.id).count() == 0:
            for customer, field, old, new in DEMO_RULES:
                db.add(MemoryRule(customer_name=customer, field_name=field, old_value=old, new_value=new,
                                  rule_type="permanent", status="enabled", source="explicit_config",
                                  created_by="admin", updated_by="admin"))
        db.commit()
    finally:
        db.close()
