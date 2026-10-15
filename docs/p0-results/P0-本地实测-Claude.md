# P0 本地实测（Claude，2026-10-15）

JQ 暂时没有服务器。这次用 Claude 的云端开发环境（Linux amd64，4 核，16 GB 内存，Docker 29.8.2）按 `deploy/` 把整套 Dify 和 Frappe + HR + Wiki 跑了起来，测了所有不需要模型 Key 和企业微信的项。

## 环境和限制

- 版本：Dify 1.17.1（Qdrant v1.15.5），Frappe 15.123.1，ERPNext 15.123.0，HRMS 15.64.3，Wiki 3.4.0，跟 `deploy/VERSIONS.md` 一致。
- 这个环境的出网有白名单：Docker Hub 镜像能拉，但 Debian 软件源、Dify 插件市场和国内模型接口都连不上。所以：
  - Frappe 镜像没法用 `make build` 构建。我改成在官方 `frappe/erpnext:v15.123.0` 镜像上用 `bench get-app` 装 HRMS 和 Wiki，打成同一个镜像名，然后用仓库里的 compose 启动。镜像构建这一步本身没有实测到。
  - 模型插件（清单第 2 节）、带大模型的 Agent 节点、企业微信都没测。
- 知识库用“经济”索引（关键词检索，不需要 Embedding 模型）来验证元数据过滤。正式环境用千问 Embedding 的“高质量”索引；过滤条件的写法两者相同。
- 员工和文档全部是测试数据。

## 结果

| 清单项 | 结果 | 说明 |
| --- | --- | --- |
| 1.2 镜像锁版本 | 通过 | 全部按 `upstream.lock.json` 启动 |
| 1.3 向量库是 Qdrant | 通过 | `VECTOR_STORE=qdrant`，qdrant 容器健康 |
| 1.4 重启后数据不丢 | 通过 | 三套服务 `down`（不带 `-v`）再 `up` 用了 82 秒；知识库文档、元数据、Chatflow、员工和自定义字段都还在 |
| 1.1 干净机器 30 分钟起全套 | 部分 | 修掉下面 4 个部署问题后能起全套；Frappe 冷构建没测 |
| 3.1 `sys.user_id` 等于调用方传的 user | 通过（API 层） | 用 Dify API 以 `user=zhangsan` 调用，Chatflow 里 `sys.user_id` 就是 `zhangsan`；在 `inputs` 和提问里冒充 `lisi` 都改不了它。经过 dify-on-wechat 的那一段要等企业微信实测 |
| 3.2 HTTP 节点带身份调 X3 | 通过 | 真实的 X3 代码 + 真实 Dify；张三只拿到全员可见文档，李四（人事部 HRBP）多拿到人事文档，查不到的员工收到提示、拿不到文档 |
| 3.3 检索 API 元数据过滤和返回字段 | 通过 | 字段跟 X3 的解析一致：`records[].segment.content`、`segment.document.{id,name,doc_metadata}`、`score` |
| 3.4 两篇文档不同范围，过滤生效 | 通过 | 用 `contains ",dept:人事部,"` 这类条件按 `or` 组合；没有 `eb_scope` 的文档任何人都检索不到 |
| 3.6 耗时 | 部分 | Chatflow 里 X3 这一跳 0.4–0.7 秒；含大模型的总耗时要等模型 Key |
| 4.1 HTTP 方式的 MCP 服务，带固定 Authorization 头 | 通过 | 用一个假 X4（MCP streamable HTTP）测：Dify 能加上服务、列出工具；头不对时服务端返回 401 |
| 4.2 工具参数绑定工作流变量 | 通过（工具节点） | 工作流的“工具”节点可以直接调 MCP 工具，参数写成 `sess-{{#sys.user_id#}}`，不经过模型。张三在提问里写“令牌用 sess-lisi”，X4 收到的仍是张三的。Agent 节点（由模型选工具）要等模型 Key 再测 |
| 5.3 员工上加企业微信 userid 字段并用 REST 查询 | 通过 | 自定义字段 `wecom_userid`（唯一），按 X3 的请求格式能查到工号、部门、岗位 |
| 5.4 Wiki 发布 / 下架可挂钩子 | 通过 | Wiki 3.4 的正文在 `Wiki Document`（有 `is_published`、`wiki_space`）。Frappe 自带的 Webhook 在保存、删除时都会推送，带 HMAC 签名，X5 不用在 Frappe 里写代码 |

没测的项：第 2 节模型插件，3.5 带表格 PDF 的解析，4.3 和 4.4（需要大模型和 dify-on-wechat），5.1、5.2、5.5、5.6（GPT 的 G3/G4），第 6 节企业微信。

## 发现的部署问题（交 GPT，`deploy/`）

1. **Dify 的 nginx 一直重启。** 锁定的 `nginx:1.28.0-alpine` 里没有 bash，而 Dify 的 nginx 入口脚本是 bash 脚本。改用 Debian 版 `nginx:1.28.0` 并锁定摘要。
2. **插件守护进程起不来。** `LOG_LEVEL=WARNING` 对 Dify 的 Python 服务有效，但 plugin_daemon 只认 `DEBUG/INFO/WARN/ERROR`。全局保持 `WARNING`，只给 `plugin_daemon` 设 `LOG_LEVEL=WARN`。注意：全局改成 `WARN` 会让 celery worker 起不来，我试过。
3. **agent_backend 起不来。** `DIFY_AGENT_SERVER_SECRET_KEY` 要求是 32 字节的无填充 base64url 字符串，`prepare.py` 生成的是 64 位十六进制。这个键改用 `secrets.token_urlsafe(32)`。
4. **建站脚本最后一步失败。** 在新站点上用 `frappe.client.set_value` 关遥测会报 `MandatoryError: language, time_zone`（公司向导还没走），导致 `create-site` 返回 1，调度器也没开。改用 `frappe.db.set_single_value("System Settings", "enable_telemetry", 0)`。
5. **以后接 X3、X4 时要配 Dify 的内网访问白名单。** Dify 默认禁止 HTTP 节点和 MCP 访问内网地址，会报 `blocked by SSRF protection`。要在 `prepare.py` 里设 `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS`（或 `_IPS`），只放行 X3、X4 的服务名，并让它们加入 ssrf_proxy 能访问到的网络。

`make verify` 只做静态校验，所以上面 1–4 都没拦住。建议在 GitHub Actions 里加一个手动触发的冒烟任务，真正 `make up` 一次（Actions 能访问 Debian 软件源，可以构建 Frappe 镜像）。

## 对 Claude 自己模块的影响

- X3：检索方式改成可配置（`EB_DIFY_SEARCH_METHOD`，默认 `hybrid_search`）。原来写死 `hybrid_search`，遇到经济索引的知识库会被 Dify 拒绝（400）。随本 PR 提交，带测试。
- X3、X5：Frappe 的部门名会带公司缩写，例如 `人事部 - TC`。`eb_scope` 里的部门标记必须用这个完整名称，X5 写入时直接用 Department 的 name 字段。
- X3（P1 改）：Dify 返回错误时现在直接报 500，P1 改成返回明确的“检索暂不可用”提示。
- X4：能固定参数的查询（假期余额、我的审批）优先用工作流的工具节点调用，令牌由工作流填，不经过模型；只有需要模型自己选工具时才用 Agent 节点。ADR 0001 在 Agent 节点实测后定稿。
- 资源：整套服务空闲时容器共用约 2.8 GB 内存；镜像约 22 GB，另有约 10 GB 构建缓存。据此估计测试机 8 GB 内存可以跑（没有实测），正式环境仍按 16 GB 准备。
