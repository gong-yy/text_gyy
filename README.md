# T 系统（ePortal ticket 驱动的订单修改 + 内部 Agent 学习 + 兑换记忆库）

智眸（识别，已上线）→ **T 系统**（新建）→ ePortal（订单门户，已上线，订单真源）。

- **智眸 intake + 记忆匹配**：智眸推送结构化字段 → T1 查兑换记忆库（命中改/未命中保持）→ 经 ePortal API 创建预订单，返回 order_id/version，命中与未命中均记录 hit_log。
- **ePortal ticket 驱动的 T2**：已登录业务员在 ePortal 点【修改】→ ePortal 签发**一次性 opaque ticket**（绑定操作人/订单/版本，2~5 分钟）→ 浏览器跳 `/t2?ticket=...`（URL 不含用户信息/订单内容/长期令牌）→ T 用**服务端凭证**换票，把操作人上下文存进**仅 T 可见的短时服务器端编辑会话**，浏览器此后只带 HttpOnly 会话 cookie。
- **版本化回写**：T2 保存携带 `expected_version`；ePortal 409 冲突时 T 绝不覆盖新版本，提示刷新重新进入。
- **结构化订单（canonical COSTING SHEET）**：`app/eportal_schema.py` 按真实 ePortal COSTING SHEET 全量建模——表头 31 字段（基本信息/收货/税务付款/商务信息 + 9 个只读合计字段，类型 text/date/boolean/select）、产品行 19 列（Node ID、BiZ Category、币种下拉、Qty、Unit Price 等，Unit Cost/Total Cost/Total Price/Tax Payable/GP/GP% 为计算列只读）、4 个命名附件槽（*合同/报价单/ePO、*J-FORM、*J-FORM (Approval)、GCF，文件内容归 ePortal，T 仅元数据）。建单时全量模板生成，智眸没提取的字段留空待人工/记忆补全。
- **内部 Agent 学习**：每个修改字段可填「错误说明（供系统学习）」；回写成功后为每条非空说明创建**纠正案例**（pending），后台调用内部大模型归纳；模型结果须 `rule_type=permanent` 且 `correct_value` 与人工最终值完全一致，且**同键无启用长期规则**时才新建规则——**Agent 只建新规则，绝不覆盖既有规则**；调用/解析失败记 failed，不影响已保存订单。
- **ground_truth 实测**：`tests/test_ground_truth.py` 读取 `D:/GK/T/data/ground_truth/*.csv`（智眸 B 层导出，30 单）批量 intake，验证全量表单生成、产品行拆分（描述含分号不误拆）、税率规则命中、T2 补齐 Sales Person 后第二张同客户新单**空值自动填充**闭环。目录缺失时自动跳过。
- **审计**：history 记录 ePortal 操作人（user_id/user_name）、字段修改、规则变化、回写冲突与 Agent 结果。

实施计划与设计文档：`docs/superpowers/plans/2026-09-01-eportal-integrated-t-system.md`、`docs/superpowers/specs/2026-09-01-eportal-integrated-t-system-design.md`。

## 技术栈与运行

- Python 3.12 + FastAPI + SQLAlchemy 2 + Pydantic 2 + httpx；前端原生 HTML/JS
- 开发/演示默认 SQLite；生产 MySQL（`sql/schema.mysql.sql`，启动亦可自动建表）

```bash
cd D:\GK\T\data\t-system
python run.py                                     # http://127.0.0.1:8300
python -m pytest tests                            # 全量测试（40 条）
```

> 若从旧版本升级且使用 SQLite：表结构有变更（eportal_orders 新增 items/item_schema/attachments，新增 correction_case），删除旧 `t_system.db` 重新生成即可；MySQL 执行增量 DDL 或重建。

## 页面

| 地址 | 说明 |
|---|---|
| `/` | **记忆库管理界面**（管理员）：规则列表/筛选/新建/启停/删除、命中明细、变更历史、全量操作历史 |
| `/t2?ticket=...` | **T2 修改界面**：ticket 启动（无登录页）；按 ePortal schema 渲染文本/日期/布尔/下拉/只读字段；产品行与附件编辑；每个修改字段附「错误说明（供系统学习）」与记忆方式选择（长期/单次/不记忆；长期记忆被改写时 覆盖/单次）；409 时明确提示刷新 |
| `/intake` | 模拟智眸推送：体验 T1 命中归因与建单 |
| `/eportal` | 模拟 ePortal：预订单列表（含「由记忆自动修改」标注与只读标识）、【修改】签发 ticket 跳 T2、提交订单、回写故障注入 |

演示账号（管理员用）：`admin/admin123`；服务账号：`zhimou/zhimou123`。种子演示规则：客户 `Acme Trading Co.` 的 `Tax Structure: 13% → CN VAT13`、`签约公司: shanghai → 上海`。

## ePortal 内部接口契约（真实对接）

T 以服务端凭证（`Authorization: Bearer <service_token>`）调用 ePortal 三个内部接口：

| 接口 | 方法与路径 | 说明 |
|---|---|---|
| 换取入口上下文 | `POST /internal/t-system/tickets/exchange`，body `{"ticket": ...}` | 响应 `{user:{id,name}, order_id, version}`；ticket 一次性、2~5 分钟失效 |
| 拉取完整订单 | `GET /internal/t-system/orders/{order_id}`（头 `X-Operator-Id`） | 返回 order_id/version/客户/schema 化字段/items(line_id)/attachments/分组与计算字段标识 |
| 版本化回写 | `PATCH /internal/t-system/orders/{order_id}` | body 含 operator、expected_version、changes、items、attachments；版本不匹配返回 **409** + 最新版本/摘要；成功返回完整订单与新版本 |

相关配置（`config.ini` / 环境变量）：

| 配置 | 环境变量 | 说明 |
|---|---|---|
| `[eportal] mode` | `T_SYSTEM_EPORTAL_MODE` | `mock`（演示）/ `http`（真实） |
| `[eportal] service_token` | `T_SYSTEM_EPORTAL_SERVICE_TOKEN` | 服务端凭证，只在服务端使用，绝不下发浏览器 |
| `[eportal] ticket_exchange_path / order_for_edit_path / order_update_for_edit_path` | `T_SYSTEM_EPORTAL_TICKET_EXCHANGE_PATH` 等 | 内部接口路径 |
| `[eportal] ticket_ttl_seconds` | `T_SYSTEM_EPORTAL_TICKET_TTL` | ticket 有效期；T 编辑会话有效期不高于此值 |
| `[agent] endpoint / api_key / timeout_seconds` | `T_SYSTEM_AGENT_ENDPOINT` 等 | 内部大模型端点；留空则案例保持 pending 不调度 |

内部模型请求/响应契约：请求为字段级上下文 `{customer_name, field_name, original_value, memory_value, final_value, description, order_id, operator}`；响应必须为结构化 JSON：`error_type`、`normalized_original_value`、`correct_value`、`rule_type`、`summary`。

## 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/intake` | 智眸 → T：结构化字段+原值+客户名（服务账号），T1 匹配+建单 |
| POST | `/api/eportal/session` | 一次性 ticket → 服务端编辑会话 + HttpOnly cookie `t_edit_session`（无效/已用/过期 401） |
| GET | `/api/eportal/orders/current` | 会话内拉取当前订单（schema 化，经 ePortal 适配器） |
| POST | `/api/eportal/orders/current/save` | 版本化保存：`expected_version`+changes+items+attachments+error_descriptions(+memory/feedback choices)；409 冲突不覆盖；成功后留痕/记忆/纠正案例 |
| GET/POST/PATCH/DELETE | `/api/rules…` | 记忆规则管理（管理员） |
| GET | `/api/rules/{id}/hits` · `/api/rules/{id}/history` · `/api/history?form_id=&order_id=&op_type=` | 命中明细 / 规则历史 / 全量历史 |
| POST | `/api/auth/login` | 管理员登录（记忆库管理用；T 不维护业务员账号/登录页） |
| POST/GET | `/api/mock/eportal/…` | 演示用 mock ePortal：建单/列表/详情/签发 ticket/提交/故障注入 |

## 关键约束（全局）

1. T 不维护 ePortal 业务员密码或独立业务员登录页。
2. 浏览器 URL 仅传一次性 opaque ticket。
3. ePortal 是订单真源；T 不写计算字段（金额/税额/合计只读）。
4. 保存必须带 `expected_version`；409 不覆盖 ePortal 新版本。
5. Agent 只在回写成功后后台处理；只新建规则，不自动覆盖既有长期规则（同键冲突记 `conflict`）。
6. 旧 intake/记忆场景（命中、长期/单次规则、负反馈、回写重试、操作留痕）全部保留并持续测试。

## 验收测试（tests/）

| 文件 | 覆盖 |
|---|---|
| `test_eportal_gateway.py` | ticket 一次性/并发仅一次成功/过期清理、Http 适配器内部接口契约与 409 翻译 |
| `test_eportal_session.py` | ticket → HttpOnly 会话、无效/伪造 cookie 401、多会话隔离、数据只经适配器加载 |
| `test_order_editing.py` | 只读计算字段 422、未知字段/下拉选项/必填校验、409 不覆盖、成功保存版本递增且 history 记 ePortal 操作人、结构化 items/attachments |
| `test_t2_page.py` | 页面 ticket 启动（无 token）、错误说明控件、按类型渲染、无登录控件 |
| `test_full_schema.py` | canonical COSTING SHEET：31 表头字段/19 产品列/4 附件槽、计算列只读（含 mock 服务端拒绝）、必填附件不可删、智眸产品行拆分 |
| `test_ground_truth.py` | ground_truth 30 单批量 intake、税率规则命中标注、T2 补人工字段 → 空值填充二次命中、同 PO 多税率样本 |
| `test_correction_agent.py` | 有效结果建新长期规则（source=agent）、同键冲突不覆盖、结果不一致/非 permanent 拒绝、Agent 失败不影响订单 |
| `test_acceptance.py` | 原验收 1~11（命中/未命中、长期规则二次命中、单次消费、格式归一、并发、history、回写重试等）＋ ticket 端到端 |
