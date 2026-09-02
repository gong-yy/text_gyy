"""数据模型：兑换记忆库（memory_rule / hit_log / history）＋ T 本地订单 ＋ 用户/模板 ＋ mock ePortal。"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .util import utcnow


class MemoryRule(Base):
    """一条记忆 = 客户名 + 字段名 + 原值 → 修改值。"""
    __tablename__ = "memory_rule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_name: Mapped[str] = mapped_column(String(255), index=True)   # 客户名（锚定键）
    field_name: Mapped[str] = mapped_column(String(128), index=True)      # 字段名
    old_value: Mapped[str] = mapped_column(Text, default="")              # 原值
    new_value: Mapped[str] = mapped_column(Text, default="")              # 修改值
    rule_type: Mapped[str] = mapped_column(String(16), default="permanent")  # permanent 长期 / once 单次
    status: Mapped[str] = mapped_column(String(16), default="enabled", index=True)  # enabled / disabled
    effective_count: Mapped[int] = mapped_column(Integer, default=0)      # once 规则剩余可用次数
    hit_count: Mapped[int] = mapped_column(Integer, default=0)            # 累计命中次数
    last_hit_time: Mapped[datetime | None] = mapped_column(DateTime)      # 最近命中时间
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    source: Mapped[str] = mapped_column(String(32), default="")  # human_diff / once_modify / explicit_config / negative_feedback


class HitLog(Base):
    """命中日志：T1 每次字段查询均记录（命中与未命中）。"""
    __tablename__ = "hit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[int | None] = mapped_column(Integer, index=True)      # 未命中为空
    order_id: Mapped[int | None] = mapped_column(Integer, index=True)     # T 系统订单 ID
    form_id: Mapped[str | None] = mapped_column(String(64))              # ePortal 表单 ID
    hit: Mapped[bool] = mapped_column(Boolean, default=False)
    field_name: Mapped[str] = mapped_column(String(128))
    value_before: Mapped[str | None] = mapped_column(Text)                # 智眸原值
    value_after: Mapped[str | None] = mapped_column(Text)                 # 命中时为应用的记忆值；未命中为空
    hit_time: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class History(Base):
    """全量操作日志：操作人/时间/类型/关联订单/字段/前后值/规则，全链路可追溯。"""
    __tablename__ = "history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operator_id: Mapped[str] = mapped_column(String(64), default="")      # 操作人 ID
    operator_name: Mapped[str] = mapped_column(String(64), default="")    # 操作人姓名
    op_time: Mapped[datetime] = mapped_column(DateTime, default=utcnow)   # 操作时间
    op_type: Mapped[str] = mapped_column(String(32), index=True)
    # 人工修改 manual_modify / 规则覆盖 rule_override / 规则启停 rule_toggle /
    # 单次修改 once_modify / 规则删除 rule_delete / 记忆记入 memory_record / 规则新建 rule_create
    order_id: Mapped[int | None] = mapped_column(Integer, index=True)     # 关联订单 ID
    form_id: Mapped[str | None] = mapped_column(String(64))
    field_name: Mapped[str | None] = mapped_column(String(128))           # 字段名
    value_before: Mapped[str | None] = mapped_column(Text)                # 修改前值
    value_after: Mapped[str | None] = mapped_column(Text)                 # 修改后值
    rule_id: Mapped[int | None] = mapped_column(Integer, index=True)      # 关联规则 ID（如有）
    remark: Mapped[str | None] = mapped_column(String(500))               # 备注


class Order(Base):
    """T 本地订单：承接智眸流转 → T1 匹配 → 建单 → T2 修改 → 回写。"""
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zhimou_task_id: Mapped[str | None] = mapped_column(String(64))        # 智眸 taskID（留痕）
    customer_name: Mapped[str] = mapped_column(String(255), index=True)
    form_id: Mapped[str | None] = mapped_column(String(64), index=True)   # ePortal 表单 ID（建单响应取回）
    version: Mapped[int] = mapped_column(Integer, default=1)              # 回写幂等键：表单ID+版本号
    status: Mapped[str] = mapped_column(String(24), default="pending_create", index=True)
    # pending_create 待建单 / created 已建单草稿 / create_failed 建单失败 / synced 已回写 / sync_failed 回写失败
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    # {"fields": {字段: {value, source(zhimou/memory/human), rule_id?}}, "original": {智眸原值},
    #  "applied_memory": [{field, rule_id, original_value, applied_value}], "meta": {...}}
    pending_writeback: Mapped[dict | None] = mapped_column(JSON)          # 待回写 {fields: {字段: 新值}, version}
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_by: Mapped[str | None] = mapped_column(String(64))             # 并发编辑锁
    locked_by_name: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class User(Base):
    """用户（mock 模式：本地账号 + 静态令牌；sso 模式对接统一登录）。"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    password_hash: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(16), default="sales")  # admin / sales / service
    token: Mapped[str | None] = mapped_column(String(128), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class TemplateField(Base):
    """预订单模板字段：按客户配置展示字段（customer_name='*' 为默认模板）。"""
    __tablename__ = "template_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_name: Mapped[str] = mapped_column(String(255), default="*", index=True)
    field_name: Mapped[str] = mapped_column(String(128))
    label: Mapped[str] = mapped_column(String(128), default="")
    display_order: Mapped[int] = mapped_column(Integer, default=100)
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    editable: Mapped[bool] = mapped_column(Boolean, default=True)


class CorrectionCase(Base):
    """纠正案例：保存成功后按错误说明创建，后台交给内部 Agent 归纳学习。"""
    __tablename__ = "correction_case"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_ref: Mapped[str | None] = mapped_column(String(64), index=True)   # ePortal 订单 ID
    version: Mapped[int | None] = mapped_column(Integer)                    # 回写成功后的订单版本
    customer_name: Mapped[str] = mapped_column(String(255), default="")
    field_name: Mapped[str] = mapped_column(String(128))
    original_value: Mapped[str | None] = mapped_column(Text)                # 智眸原始值
    memory_value: Mapped[str | None] = mapped_column(Text)                  # T 自动替换值（如有）
    final_value: Mapped[str | None] = mapped_column(Text)                   # 人工最终值
    description: Mapped[str | None] = mapped_column(Text)                   # 错误说明原文
    operator_id: Mapped[str | None] = mapped_column(String(64))             # ePortal 操作人
    operator_name: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending/processed/failed/conflict
    agent_summary: Mapped[str | None] = mapped_column(Text)                 # 模型归纳
    agent_result: Mapped[dict | None] = mapped_column(JSON)                 # 模型原始结构化结果
    error: Mapped[str | None] = mapped_column(Text)                         # 处理失败原因
    rule_id: Mapped[int | None] = mapped_column(Integer, index=True)        # 产出的记忆规则
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)


class EportalOrder(Base):
    """mock ePortal 预订单（演示用；真实环境为外部系统，经适配器对接）。

    fields 为 schema 化结构：{字段: {value, type, editable, required, options, group}}，
    金额等计算字段 editable=False，由 ePortal 负责计算。
    """
    __tablename__ = "eportal_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id: Mapped[str] = mapped_column(String(64), unique=True)
    customer_name: Mapped[str] = mapped_column(String(255))
    fields: Mapped[dict] = mapped_column(JSON, default=dict)              # schema 化字段
    items: Mapped[list] = mapped_column(JSON, default=list)               # 产品行（含稳定 line_id）
    item_schema: Mapped[dict] = mapped_column(JSON, default=dict)         # {列: {editable, label}}
    attachments: Mapped[list] = mapped_column(JSON, default=list)         # 附件元数据 {id, name, removable}
    auto_modified: Mapped[dict] = mapped_column(JSON, default=dict)       # {字段: {rule_id}} 「由记忆自动修改」标注
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="draft")      # draft / submitted
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EportalWriteLog(Base):
    """mock ePortal 写入日志：幂等键 = 表单ID + 版本号（同键重放直接返回上次结果）。"""
    __tablename__ = "eportal_write_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = ({"sqlite_autoincrement": True},)
