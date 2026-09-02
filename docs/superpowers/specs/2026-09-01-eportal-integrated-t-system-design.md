# ePortal 集成 T 系统设计

## 目标

T 系统不维护业务员账号或独立登录页。智眸向 T 推送识别结果，T 完成记忆匹配并通过 ePortal API 创建预订单；已登录 ePortal 的用户从 ePortal 进入 T 的修改页，在 T 中修改 ePortal 订单全部可编辑内容。T 记录 ePortal 提供的操作人信息，并将修改按版本安全地回写 ePortal。

## 系统边界

- 智眸负责识别，向 T 的 intake 接口发送客户、识别字段和任务标识。
- T 负责记忆匹配、人工修改、记忆沉淀、修改历史和 ePortal 回写。
- ePortal 是订单真源，负责用户登录、订单归属/访问控制、订单校验、金额重新计算、附件管理和最终提交。
- T 不存储 ePortal 用户密码，不提供业务员登录页面，不对订单分配作二次授权判断。

## ePortal 到 T 的身份与入口

1. 已登录用户在 ePortal 点击“修改”。
2. ePortal 创建一个随机 opaque ticket，绑定 `user_id`、`user_name`、`order_id` 与当前订单 `version`；ticket 仅能使用一次，失效时间为 2–5 分钟。
3. 浏览器跳转到 `https://t.example/t2?ticket=<ticket>`。URL 不包含用户信息、订单内容或长期访问令牌。
4. T 后端使用服务端凭证调用 ePortal 的 ticket exchange 接口。
5. ePortal 返回操作人、订单 ID 和版本。T 将它存放在仅 T 可见的短时服务器端编辑会话中，浏览器后续只携带 T 的 HttpOnly 会话 cookie。
6. T 后端以服务身份从 ePortal 拉取订单完整详情并渲染修改页。

T 必须拒绝已使用、过期、签名/状态无效的 ticket。ePortal 必须在换票时完成其自身的订单访问控制；T 信任成功换票结果。

## ePortal 内部接口契约

### 1. 换取入口上下文

`POST /internal/t-system/tickets/exchange`

请求使用 T 系统服务端凭证；请求体为 `{ "ticket": "..." }`。响应：

```json
{
  "user": { "id": "louis.lu", "name": "Louis Lu" },
  "order_id": "SO-20260824-001",
  "version": 12,
  "expires_at": "2026-09-01T10:31:00Z"
}
```

### 2. 拉取完整订单

`GET /internal/t-system/orders/{order_id}`

请求使用 T 系统服务端凭证，并带上换票得到的操作人 ID。响应包含：

- `order_id`、`version`、客户信息；
- 标量字段及其 `value`、`type`、`editable`、`required`、`options`；
- `items` 产品行数组及每行稳定 `line_id`；
- `attachments` 附件元数据及可编辑规则；
- ePortal 用于前端展示的字段分组和计算字段标识。

T 仅允许编辑 `editable=true` 的字段。金额、税额、毛利、合计等计算字段由 ePortal 计算，不接受 T 覆盖。

### 3. 版本化回写

`PATCH /internal/t-system/orders/{order_id}`

请求包括操作人、`expected_version`、标量字段变更、产品行和附件变更。ePortal 验证可编辑性、必填条件、数据类型、业务规则与订单访问权限，成功后重新计算派生字段并返回完整订单与新版本。

版本不匹配时返回 HTTP 409 和最新版本/摘要。T 保留本地待回写内容，提示用户返回 ePortal 刷新并重新进入修改页，绝不覆盖 ePortal 新版本。

## 智眸到 T 的数据流

1. 智眸以服务凭证调用 `POST /api/intake`，发送客户、字段、任务 ID 与元数据。
2. T 对字段执行标准化、匹配记忆规则、记录每次命中/未命中。
3. T 通过 ePortal 的创建订单接口发送原始/匹配后的结构化订单数据及自动修改归因。
4. ePortal 创建预订单并返回 `order_id`、`version`。T 保存关联关系和命中日志。

真实 ePortal API 与现有 mock adapter 隔离，开发/验收仍可使用 mock adapter。

## T2 页面

T2 不显示账号/密码登录。它根据服务器端编辑会话展示：

- 基本信息；
- 收货、税务和付款信息；
- 产品明细表（支持 ePortal 声明为可编辑的列和行操作）；
- 附件区（仅展示/操作 ePortal 允许的附件动作）；
- 记忆来源标记与修改后的长期、单次、不记忆选择。

页面使用 ePortal 提供的字段类型渲染文本、日期、布尔、下拉和只读计算字段，而不是将所有字段转换为文本输入。

## 错误说明与内部 Agent 学习

每个发生人工修改的可编辑字段下方提供“错误说明（供系统学习，不对外展示）”文本框。业务员填写的说明不需要获得 Agent 的即时回复，也不阻塞订单保存。

保存与 Agent 学习按以下顺序执行：

1. T 将订单修改回写 ePortal。
2. 仅当 ePortal 成功接受修改后，T 对每个有错误说明的字段保存纠正案例：客户、字段、智眸原始值、T 自动替换值（如有）、人工最终值、错误说明、ePortal 操作人、订单与版本。
3. T 在后台将案例发送到内部大模型；大模型只返回结构化 JSON：`error_type`、`normalized_original_value`、`correct_value`、`rule_type` 与 `summary`。
4. T 验证 JSON 格式，且要求 `correct_value` 与本次人工最终值完全一致；验证成功后，若该标准化“客户 + 字段 + 原始值”不存在启用的长期规则，则创建一条长期兑换记忆。
5. 下次智眸推送相同匹配键时，现有 T1 引擎命中该规则并将正确值传给 ePortal 建预订单。

Agent 自动创建新长期规则，但绝不自动覆盖已有长期规则。若同键已有长期规则且本次人工修改与它冲突，继续使用现有的“覆盖原规则 / 仅本次单次修改”人工选择；Agent 只记录案例和归纳结果。Agent 调用或解析失败不会影响 ePortal 已成功保存的订单，案例状态记为失败并可后台重试。

新增纠正案例记录应保存 Agent 状态（`pending`、`processed`、`failed`、`conflict`）、模型归纳、错误信息和创建/处理时间；history 同时记录案例创建、Agent 规则创建、Agent 失败或冲突。

## 审计与失败恢复

- T 的 history 记录 ePortal 的 `user_id` 与 `user_name`，以及字段修改、规则变化、回写成功/失败与版本冲突。
- T 在发起回写前持久化待回写数据；网络失败时可在有效编辑会话内重试相同版本的请求。
- ePortal 成功后 T 保存 ePortal 返回的新版本和订单摘要；重复请求由 ePortal 按订单 ID、版本和请求 ID 做幂等处理。

## 验收

1. 智眸推送的命中和未命中订单均可由 T 成功创建到 ePortal。
2. 有效 ePortal ticket 能打开 T2，且 history 使用 ePortal 操作人信息。
3. 过期或已使用 ticket 不能打开 T2。
4. T2 可按 ePortal schema 编辑标量字段、产品行和允许的附件操作；计算字段只读。
5. 回写成功后 ePortal 返回的新版本成为 T 的当前版本。
6. ePortal 返回 409 时，T 不覆盖新订单，并显示明确的刷新提示。
7. 原有记忆命中、长期/单次规则、负反馈、回写重试和操作留痕测试继续通过。
8. 有错误说明的成功回写会创建后台纠正案例；内部 Agent 的有效结果自动创建不存在同键规则。
9. 已有长期规则与 Agent 建议冲突时，Agent 不覆盖该规则；Agent 失败也不影响订单保存。
