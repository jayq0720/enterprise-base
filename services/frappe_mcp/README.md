# X4 员工 HR 查询服务（frappe_mcp）

员工在企业微信里问“我还有几天年假”“我的请假批了没有”“有什么要我审批”时，Dify 通过 MCP 调本服务查 Frappe HR。独立服务，只通过 HTTP 调用 Frappe，不改 Frappe 源码。

## 身份怎么传（ADR 0001）

模型只能调工具，不能决定“查谁”：

1. 工作流第一步用 HTTP 请求节点调 `POST /v1/session`，请求体 `{"user_id": "{{#sys.user_id#}}"}`（企业微信 userid，由 dify-on-wechat 设置），拿到 `session_token`（HMAC 签名，默认 15 分钟有效）。
2. 工具节点或 Agent 调 MCP 工具时把 `session_token` 原样传进去。本服务验签后取出 userid，再按企业微信字段找在职员工；找不到或对应多人一律不查。
3. 模型伪造不出别人的令牌：签名密钥 `EB_MCP_SESSION_SECRET` 只在本服务里。工具参数里没有员工编号、姓名这类可以被模型改写的字段。

所有接口（`/v1/session`、`/mcp/`）都要带 `Authorization: Bearer <EB_MCP_SERVICE_TOKEN>`。

## 工具

| 工具 | 返回 | 范围 |
| --- | --- | --- |
| `leave_balance` | 各假期类型的总额、已休、审批中、剩余 | 只查自己 |
| `my_leave_applications` | 最近的请假申请（编号、类型、起止日期、天数、状态） | 只查自己，不含已取消 |
| `pending_my_approval` | 等我审批的请假申请，带申请人姓名 | 审批人是我、状态 Open |

只返回上面列出的字段，不返回手机号、身份证号、银行卡、薪资；审批人邮箱也不返回。Frappe 出错时返回“HR 系统暂时不可用”，不报 500。

后续：发起请假（写操作，要员工在企业微信里确认后才提交）、查岗位职责（等 X1 接口）另开 PR。

## 在 Dify 里接入

1. 工具 → MCP → 添加 MCP 服务：地址 `http://frappe-mcp:8080/mcp/`，请求头 `Authorization: Bearer <EB_MCP_SERVICE_TOKEN>`。
2. Chatflow：开始 → HTTP 请求 `POST http://frappe-mcp:8080/v1/session`（同样的请求头）→ 代码节点取出 `session_token` → 工具节点（参数 `session_token` 绑定上一步输出）。工具结果在工具节点的 `json` 输出里。
3. Dify 的 SSRF 代理默认拦内网地址，部署时要放行本服务（`SSRF_PROXY_ALLOW_PRIVATE_DOMAINS` 或 `_IPS`），改完重建 `ssrf_proxy` 容器。

## 本地开发

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest
```

## 状态

P2-C1 只读部分：3 个查询工具已用本地 Frappe HR 和 Dify 1.17.1 端到端实测（张三查余额和申请、李四查待审批、没有员工记录的人只收到提示）。
