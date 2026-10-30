# X6 员工前台（portal）

员工在电脑浏览器里用的网站：企业微信扫码登录（在企业微信里打开时免扫码），然后可以

- 和“HR 助手”“制度问答”对话（就是企业微信里那两个 Dify 应用）
- 查假期余额和自己的请假记录，填表发起请假（先出草稿，核对后点“确认提交”）
- 看待我审批的请假单，直接点“通过”或“退回”
- 看自己的岗位职责
- 在自己岗位能看的知识空间里检索

菜单按岗位上的助手开关显示（和企业微信里一样），关掉的功能不出现，接口也会拒绝。

独立服务，只通过 HTTP 调 X4、X3 和 Dify 的应用 API，不 import、不装进 Frappe 和 Dify，不改开源源码。页面是本服务自带的静态文件（原生 JS，无构建步骤、无第三方前端库）。

## 身份怎么传

浏览器只拿到一个签名的会话 Cookie，里面只有企业微信 userid。

1. `/auth/login` 生成一次性 nonce（HttpOnly Cookie，5 分钟）和签名的 state，跳到企业微信扫码页（在企业微信里打开时用网页授权，`snsapi_base`）。
2. `/auth/callback` 校验 state 签名、和 nonce Cookie 匹配，用 code 到企业微信换 userid，再调 X4 `profile` 确认是在职员工，才签发会话 Cookie（`__Host-eb_portal`，HttpOnly、Secure、SameSite=Strict，默认 8 小时）。查不到在职员工的人登录不进来。
3. 之后页面调 `/api/*`，本服务从 Cookie 取 userid，带上各自的服务 Token 调 X4 `/v1/portal/*`、X3 `/v1/search`、Dify `/chat-messages`（`user` 填 userid，和 dify-on-wechat 一样）。**浏览器传来的任何身份字段都不用**；X4、X3 每次请求都按 userid 重新查员工和岗位，Dify 工作流里照旧只用 `sys.user_id`。
4. 每隔 `EB_PORTAL_RECHECK_SECONDS`（默认 60 秒）回 X4 确认一次仍在职；离职、停用后会话立即作废。

所以上线关掉 Dify Web App（防 `sys.user_id` 冒充）后，员工网页端用的是这里：Dify 应用 API Key 只在本服务里，浏览器拿不到。

和 Frappe 后台的扫码登录不同：前台不给任何后台权限，只能看和办本人的事，所以没有拦“系统管理员”这类后台角色；接口账号（EB Integration、EB X4）和后台一样由 X2 的审批人校验拒绝。系统管理员仍然不能扫码进 Frappe 后台。

## 安全措施

- 写操作要同时带自定义请求头 `X-EB-Portal: 1` 和本站 `Origin`，读操作要带请求头；会话 Cookie 是 SameSite=Strict，防跨站请求。
- 所有响应带 CSP（只允许本站脚本、禁止被嵌入）、`nosniff`、`no-referrer`；接口和登录不缓存。
- 页面只用 `textContent` 显示服务端内容，不拼 HTML。
- 日志只记异常类型，不记 userid、扫码 code、问题内容和返回内容；httpx 的请求日志压到 WARNING。
- 只返回页面要用的字段：检索结果只有标题、内容、空间；对话只有回答和会话 ID。

## 配置

见 `.env.example`。企业微信应用可以和 X2 扫码登录用同一个，要在企业微信后台把前台域名加进应用的“可信域名”和“企业微信授权登录”回调域。X4 要配 `EB_MCP_PORTAL_TOKEN`（和 Dify 用的 Token 不同），否则前台接口一律 401。

部署：域名建议 `portal.<客户域名>`，经 Caddy 对员工开放（不加管理员 IP 白名单），反向代理到本服务 8080 端口；本服务要能访问 `frappe-mcp`、`kb-gateway` 和 Dify 的 `dify` 内网别名。部署脚本的接入见任务卡 `docs/task-cards/P3-G4-员工前台部署.md`。

## 本地开发

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest
```
