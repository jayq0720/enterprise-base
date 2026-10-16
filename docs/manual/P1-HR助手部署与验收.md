# HR 助手部署与验收（P1-G7）

本步骤用于独立测试环境，先按 [LOCAL.md](../../deploy/LOCAL.md) 完成 Frappe/Dify 初始化。X3/X4 放在 `frappe-services.yaml`：两者依赖 Frappe，与 X5 共用其默认网络，Dify 只有 SSRF 代理进 edge 网络，HTTP 节点和 MCP 调用都经代理访问 X3/X4，Dify api/worker 不直连。采用 `hr-assistant` profile，首次建站时不要求尚未生成的接口凭据；无宿主机端口，不添加 Caddy 路由。

## 账号与启动

在服务器的仓库根目录执行 `cd deploy && make init`。旧配置会补齐缺少的 X3/X4 配置和三项独立随机密钥，已有值保留。镜像固定为 VERSIONS.md 中 main 构建的摘要；私有 GHCR 包先交互执行 `docker login ghcr.io`，不要把访问令牌放进命令或仓库。

1. Frappe 后台 → 用户，启用 `eb-x3@integration.invalid` 和 `eb-x4@integration.invalid`，分别在“API 访问”生成 API Key/Secret。X3 保持 `EB Integration`；X4 保持 fixtures 中的 `EB X4`。不要增加 HR Manager/System Manager，不能共用账号或管理员 Key。
2. 只在服务器编辑 `deploy/secrets/.env`：X3 的 Key/Secret 填 `EB_KB_FRAPPE_API_KEY`、`EB_KB_FRAPPE_API_SECRET`；X4 的填 `EB_MCP_FRAPPE_API_KEY`、`EB_MCP_FRAPPE_API_SECRET`。
3. Dify 知识库 → API 访问，生成知识库 API Key，填 `EB_KB_DIFY_DATASET_API_KEY`。必须与 X5 在同一个工作空间。X5 为 high_quality 时 X3 保持 `EB_KB_DIFY_SEARCH_METHOD=hybrid_search` 并先配置 Embedding/重排模型；无模型测试的 economy 索引则改为 `keyword_search`。
4. 按 [X1/Wiki 手册](P1-X1知识空间与Wiki同步.md) 配置 X5、知识空间和审核发布。X3 检索必须有已同步的文档及权限元数据。

```sh
# 当前在 deploy/，基础服务已 make up；先录入上述服务器凭据。
make policy-sync
make hr-assistant
make status
```

`make hr-assistant` 先检查必填凭据、独立密钥和镜像摘要，再重建 Dify 的 ssrf_proxy，最后等待 X3/X4 健康。只给工作流代理配置 `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=kb-gateway,frappe-mcp`，不放行整段私网；Agent 代理不增加放行，Sandbox 仍在隔离网络。重建代理会短暂中断在途的外部请求，在测试环境先验证。

**升级 X3/X4/X5**：拉取新代码后在 `deploy/` 执行 `make init`，它会把 `secrets/.env` 里的三个镜像摘要改成新代码中的版本（密钥不动），再执行 `make policy-sync`、`make hr-assistant`。镜像摘要不要手工改成 PR 构建或浮动标签。

`make prepare` 自动生成权限 600 的 `secrets/kb-gateway.env`、`secrets/frappe-mcp.env`；不要手工修改生成物。X3/X4 只收到各自的 API 凭据及 Token，X4 的 `EB_MCP_SESSION_SECRET` 只供服务签名，不填写到 Dify；`EB_MCP_ALLOWED_HOSTS` 固定为 `["frappe-mcp:8080"]`，不允许 localhost 通配符。GET /healthz 仅检查进程存活，凭据、权限和上游可用性还须联测。

## Dify 工具与应用

先按 [模型插件验收](P0-模型插件验收.md) 安装国产模型官方插件、录入 Key。后台地址为 `dify.<服务器配置域名>`，仍受管理员来源白名单保护。

1. 工具 → MCP → 添加：地址 `http://frappe-mcp:8080/mcp/`，**服务标识必须填 `eb-hr`**，认证请求头 `Authorization: Bearer <EB_MCP_SERVICE_TOKEN>`，值从服务器密钥文件读取，仅在后台录入。保存并确认能发现工具。X4 升级新增工具后，在同一 MCP 服务点击“更新”。以后修改这个 MCP 服务的地址或 Token，保存后工具会显示未授权，要先点“授权”，再点“更新”，否则工作流调用工具会失败。
2. 工作室 → 创建应用 → 导入 DSL，选择仓库 `config/dify/hr-assistant.yml`。
3. 应用 → 环境变量：`x3_url=http://kb-gateway:8080`、`x4_url=http://frappe-mcp:8080`；`x3_token` 填服务器 `EB_KB_SERVICE_TOKEN`，`x4_token` 填 `EB_MCP_SERVICE_TOKEN`，两项保持 secret 类型。
4. 检查模型节点选用已配置的国产模型、MCP 节点绑定 eb-hr，发布应用。在“API 访问”生成应用 Key；接企业微信时将其填入服务器 `DIFY_API_KEY`，再按 LOCAL.md 运行 `make wechat`。

用有 Active Employee、岗位和 wecom_userid 的测试员工调用应用，`user` 必须是该员工企业微信 userid，流程从 `sys.user_id` 取身份；控制台预览的管理员身份不能替代。分别问“我的岗位职责是什么”“我还有几天年假”，核对本人岗位职责及余额，再用无岗位、无员工、错误 Token 的身份确认拒绝。不要在聊天/PR 粘贴真实 userid、员工内容、Key 或完整运行详情。

## 服务器验收与回传

E2E 会创建/删除合成人员、制度及知识库，只能指向测试环境。按 [tests/e2e/README.md](../../tests/e2e/README.md) 把凭据写到服务器 `deploy/secrets/e2e.env`（权限 600）。内网地址如下，其余必填 Key/Secret 从对应后台生成，不使用 X3/X4 的受限 Key 充当测试管理员：

| 项 | 测试值/来源 |
| --- | --- |
| EB_E2E_FRAPPE_URL / EB_E2E_FRAPPE_HOST | `http://frappe:8080` / `hr.<EB_DOMAIN>` |
| EB_E2E_FRAPPE_ADMIN_KEY / EB_E2E_FRAPPE_ADMIN_SECRET | 测试管理员，角色含 System Manager + HR Manager |
| EB_E2E_DIFY_API_URL / EB_E2E_DIFY_DATASET_KEY | `http://dify/v1` / 与 X5 同工作空间的知识库 Key |
| EB_E2E_DIFY_INDEXING_TECHNIQUE | 与 EB_SYNC_DIFY_INDEXING_TECHNIQUE 一致 |
| EB_E2E_X3_URL / EB_E2E_X3_TOKEN | `http://kb-gateway:8080` / EB_KB_SERVICE_TOKEN |
| EB_E2E_X4_URL / EB_E2E_X4_TOKEN | `http://frappe-mcp:8080` / EB_MCP_SERVICE_TOKEN |
| EB_E2E_HR_APP_KEY | 发布的 HR 助手应用 Key；无模型 Key 时删除此项，记录对话测试跳过 |
| EB_E2E_LEAVE_TYPE | 已有假期类型的完整名称，如年假 |

在 `deploy/` 运行下列命令；项目名不是 eb 时，把网络名 eb_edge 改为实际 `<EB_PROJECT>_edge`。使用临时容器访问内网，不额外开放服务端口。测试挂载源码只读，依赖及缓存写临时目录，测试结束删除容器。

```sh
make verify
make status
docker run --rm --network eb_edge \
  --env-file "$(pwd)/secrets/e2e.env" \
  -e UV_PROJECT_ENVIRONMENT=/tmp/e2e-venv -e UV_CACHE_DIR=/tmp/uv-cache \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$(pwd)/../tests/e2e:/tests:ro" -w /tests \
  python:3.12.12-slim-bookworm sh -ec \
  'pip -q install uv==0.11.32; uv sync --frozen; uv run pytest -v -p no:cacheprovider'
```

回传北京时间、提交/镜像摘要、make status 中 X3/X4/X5 的 healthy 状态、两句本人问答“符合/不符合”、pytest 的 passed/failed/skipped 数量与合成数据清理结果。日志先去掉凭据和个人内容；不要回传完整 Compose config、inspect、原始 Dify/Frappe 请求日志。所有测试被跳过不算通过；无模型 Key 仅允许跳过 HR 应用对话。

当前无已部署服务器：仅完成配置生成、隔离和缺项拒绝检查。空服务器启动、三个服务健康、真实模型问答及 E2E 实测/部署成功日志均待 JQ 执行后补录，不能用配置检查代替。
