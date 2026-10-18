# X4 员工 HR 查询服务（frappe_mcp）

员工在企业微信里问“我还有几天年假”“我的请假批了没有”“有什么要我审批”时，Dify 通过 MCP 调本服务查 Frappe HR。独立服务，只通过 HTTP 调用 Frappe，不改 Frappe 源码。

## 身份怎么传（ADR 0001）

模型只能调工具，不能决定“查谁”：

1. 工作流第一步用 HTTP 请求节点调 `POST /v1/session`，请求体 `{"user_id": "{{#sys.user_id#}}"}`（企业微信 userid，由 dify-on-wechat 设置），拿到 `session_token`（HMAC 签名，默认 15 分钟有效）。
2. 工具节点或 Agent 调 MCP 工具时把 `session_token` 原样传进去。本服务验签后取出 userid，再按企业微信字段找在职员工；找不到或对应多人一律不查。
3. 模型伪造不出别人的令牌：签名密钥 `EB_MCP_SESSION_SECRET` 只在本服务里。工具参数里没有员工编号、姓名这类可以被模型改写的字段。

所有接口（`/v1/session`、`/v1/leave/confirm`、`/mcp/`）都要带 `Authorization: Bearer <EB_MCP_SERVICE_TOKEN>`。

## 工具

| 工具 | 返回 | 范围 |
| --- | --- | --- |
| `leave_balance` | 各假期类型的总额、已休、审批中、剩余 | 只查自己 |
| `my_leave_applications` | 最近的请假申请（编号、类型、起止日期、天数、状态） | 只查自己，不含已取消 |
| `pending_my_approval` | 等我审批的请假申请，带申请人姓名 | 两级审批：停在主管环节且 `leave_approver` 是我，或已转到 HR 环节且 `eb_hr_approver` 是我；未提交 |
| `my_responsibilities` | 自己的岗位名称和岗位职责（X1 在“岗位”上加的“岗位职责”，富文本转成纯文本，最多 3000 字） | 只查自己档案上的岗位 |
| `draft_leave` | 请假草稿（类型、起止日期、事由、剩余天数）和签名的 `draft_token`；**不写 HR 系统** | 只给自己起草 |

只返回上面列出的字段，不返回手机号、身份证号、银行卡、薪资；审批人邮箱也不返回。Frappe 出错时返回“HR 系统暂时不可用”，不报 500。

## 发起请假：员工确认后才提交

写操作分两步，第二步模型碰不到：

1. 模型调 `draft_leave` 起草。草稿内容和员工身份签进 `draft_token`（默认 10 分钟有效），工作流把它存进会话变量 `draft`，并把草稿念给员工核对。
2. 员工回复“确认请假”。工作流用条件分支判断 `sys.query` 正好是“确认请假”且会话里有草稿，再用 HTTP 请求节点调 `POST /v1/leave/confirm`，请求体 `{"user_id": "{{#sys.user_id#}}", "draft_token": "{{#conversation.draft#}}"}`，然后清空 `draft`。

`/v1/leave/confirm` 不是 MCP 工具，模型调不到；草稿里的 userid 必须和 `sys.user_id` 一致，别人拿到草稿也提交不了；草稿内容改一个字签名就不对。提交的请假单是 Open、未提交状态，审批人按 HRMS 规则（员工上的请假审批人，没有就用部门的）自动填，之后走 HRMS 原来的审批。重叠、余额不足、假期类型不对时返回员工看得懂的提示。

## 在 Dify 里接入

1. 工具 → MCP → 添加 MCP 服务：地址 `http://frappe-mcp:8080/mcp/`，请求头 `Authorization: Bearer <EB_MCP_SERVICE_TOKEN>`。
2. Chatflow：开始 → HTTP 请求 `POST http://frappe-mcp:8080/v1/session`（同样的请求头）→ 代码节点取出 `session_token` → 工具节点（参数 `session_token` 绑定上一步输出）。工具结果在工具节点的 `json` 输出里。
3. X4 升级后新增了工具（例如 `my_responsibilities`），要在 Dify 的 MCP 服务里点“更新”刷新工具列表，再导入或发布用到它的应用。
4. Dify 的 SSRF 代理默认拦内网地址，部署时要放行本服务（`SSRF_PROXY_ALLOW_PRIVATE_DOMAINS` 或 `_IPS`），改完重建 `ssrf_proxy` 容器。

## 本地开发

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest
```

## 状态

P2-C1：3 个查询工具和发起请假已用本地 Frappe HR 和 Dify 1.17.1 端到端实测（张三查余额和申请、李四查待审批、没有员工记录的人只收到提示；张三起草后回复“确认请假”提交成功，审批人自动带出；李四说“确认请假”不会提交张三的草稿；重复确认不会重复提交）。查岗位职责（P2-C1c）用 eb-x4 专用账号在本地 Frappe 实测：张三返回岗位和职责，李四岗位没填职责、王五档案没有岗位、查无此人各自收到提示。

Frappe 的 API 用户用 X1 建好的专用账号 `eb-x4@integration.invalid`（角色 EB X4，默认停用，管理员启用并生成 Key 后填进 `EB_FRAPPE_API_KEY`/`EB_FRAPPE_API_SECRET`）。这个账号只能：按企业微信 userid 查员工的编号、姓名、用户、岗位；按岗位名查岗位职责；读假期类型和请假单；新建未提交的请假单；调 HRMS 的余额和审批人两个查询方法。其余一律 403。
