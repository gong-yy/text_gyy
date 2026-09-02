"""Pydantic 请求模型。"""
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class SsoExchangeRequest(BaseModel):
    sso_token: str


class TicketExchangeRequest(BaseModel):
    """ePortal 一次性 opaque ticket → T 编辑会话。"""
    ticket: str


class IntakeRequest(BaseModel):
    """智眸 → T：结构化字段 + 原值 + 客户名（JSON）。"""
    customer_name: str
    fields: dict[str, object] = Field(default_factory=dict)
    task_id: str | None = None
    meta: dict | None = None


class LockRequest(BaseModel):
    pass


class SaveChangesRequest(BaseModel):
    """T2 保存：字段新值 + 记忆确认选项 + 负反馈选项。"""
    changes: dict[str, object] = Field(default_factory=dict)          # {字段: 新值}
    # 记忆确认（默认长期规则）：permanent 长期 / once 仅本次单次 / none 不记忆
    memory_choices: dict[str, str] = Field(default_factory=dict)
    # 负反馈（长期记忆命中字段被人工改写时必选）：override 覆盖原规则 / once 仅本次单次
    feedback_choices: dict[str, str] = Field(default_factory=dict)


class RuleCreateRequest(BaseModel):
    customer_name: str
    field_name: str
    old_value: str = ""
    new_value: str
    rule_type: str = "permanent"          # permanent / once
    effective_count: int = 1              # once 规则可用次数


class RuleStatusRequest(BaseModel):
    status: str                            # enabled / disabled


class FaultRequest(BaseModel):
    update_fail_times: int = 0             # mock ePortal：接下来 N 次更新返回失败（演示回写重试）


class MockOrderCreateRequest(BaseModel):
    """mock ePortal 建单（演示/测试）：fields 值可为标量或完整 schema 条目。"""
    customer_name: str
    fields: dict = Field(default_factory=dict)
    items: list | None = None
    attachments: list | None = None
    item_schema: dict | None = None
    auto_modified: dict = Field(default_factory=dict)
    version: int = 1


class SaveOrderRequest(BaseModel):
    """T2 保存（ePortal 编辑会话）：版本化回写 + 结构化产品行/附件 + 错误说明。"""
    expected_version: int
    changes: dict = Field(default_factory=dict)                # 标量字段变更 {字段: 新值}
    items: list | None = None                                  # 产品行（结构化，None=不变更）
    attachments: list | None = None                            # 附件元数据（None=不变更）
    error_descriptions: dict = Field(default_factory=dict)     # {字段: 错误说明（供内部 Agent 学习）}
    memory_choices: dict = Field(default_factory=dict)         # {字段: permanent|once|none}，默认 permanent
    feedback_choices: dict = Field(default_factory=dict)       # {字段: override|once}（长期记忆被改写时必选）
