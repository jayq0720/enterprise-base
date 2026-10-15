# AGENTS.md：共用开发规范

本仓库由 JQ、Claude、Gemini、Grok 共同开发。三个 AI 开工前必须先读完本文件，再读各自的 `CLAUDE.md`、`GEMINI.md`、`GROK.md`。
方案依据：《企业基础平台开发方案（第 4 版：用开源项目组装）》。一句话：**Dify 管 AI，Frappe + Frappe HR + Frappe Wiki 管人和流程，我们只写把两边连起来的 5 个模块（X1–X5）。**

## 1. 目录归属

| 目录 | 负责人 | 内容 |
| --- | --- | --- |
| `services/kb_gateway/` | Claude | X3 按岗位检索服务（独立服务） |
| `services/frappe_mcp/` | Claude | X4 Frappe MCP 服务（独立服务） |
| `config/dify/` | Claude | Dify 应用 DSL 导出（问答应用、Agent 应用） |
| `docs/`（`docs/manual/` 除外） | Claude | 架构、ADR、验证清单、任务卡、周报 |
| `frappe_apps/enterprise_base/duties/` | Gemini | X1 岗位职责扩展 |
| `services/policy_sync/` | Gemini | X5 制度文档同步（独立服务） |
| `config/frappe/` | Gemini | Frappe 导出的 fixtures（Workflow、Role、Custom Field） |
| `tests/e2e/`、`docs/manual/` | Gemini | 端到端测试、用户和管理员手册 |
| `frappe_apps/enterprise_base/wecom/` | Grok | X2 企业微信对接 |
| `deploy/`、`.github/`、`config/dify-on-wechat/` | Grok | 部署、CI、企业微信入口配置 |
| 根目录文件（本文件、`pyproject.toml` 等） | Claude | 改动在 PR 里提出 |

不改别人负责的目录。确实需要时，在自己的 PR 描述里写清楚原因并 @ 负责人。

## 2. 组装原则（硬规则）

1. **不改开源项目源码**（Dify、Frappe、Frappe HR、Frappe Wiki、dify-on-wechat、Qdrant）。只用官方镜像、插件、Frappe App、配置和接口。确需改源码，先在 `docs/adr/` 写 ADR，经 JQ 同意。
2. **X3、X4、X5 是独立服务**，只通过 HTTP 接口访问 Dify 和 Frappe，不 import 它们的代码，不装进 Frappe。装进 Frappe 的 X1、X2 按 GPL-3.0 处理。
3. **按“每家客户单独私有化部署”来做**：公司名、部门、岗位、流程、域名、密钥全部是配置或导入数据，不写进代码。
4. **所有外部接口和 Key 由人在后台录入**（Dify 控制台、Frappe 后台）或放服务器密钥文件；代码里只有通用机制，仓库里只有 `.env.example`。
5. 各开源组件在 `deploy/` 里锁定版本号；升级先在测试环境验证。

## 3. 身份与数据安全

- 员工身份的唯一来源是企业微信 userid：dify-on-wechat 把它作为 Dify 的 `user` 传入，应用里用 `sys.user_id` 取。**不信任模型生成的身份参数**，见 `docs/adr/0001-身份传递.md`。
- X3、X4 每次请求都按调用人在 Frappe 里的员工、部门、岗位重新校验权限；查不到员工或岗位时一律拒绝，不回落到“全部可见”。
- 手机号、身份证号、银行卡号、薪资：不同步、不进日志、不进模型上下文、接口默认不返回。
- 敏感数据只走国产模型（千问、豆包、DeepSeek）；境外模型插件不装。
- 密钥、Token、Secret 不进代码、日志、PR 描述、聊天记录。

## 4. 技术约定

- 自写代码一律 Python 3.12+，依赖用 uv 管理；只用开源依赖，新增依赖在 PR 里写明许可证。
- 每个服务是 `services/<name>/` 下独立的 uv 项目，有自己的 `pyproject.toml`、`tests/` 和 `Dockerfile`。
- 服务配置全部走环境变量（前缀 `EB_`），用 pydantic-settings 读取；缺必填项时启动即失败。
- 合 PR 前在改动的服务目录里通过：`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy .`、`uv run pytest`。CI 还会跑许可证检查和 gitleaks 密钥扫描。
- 时间一律带时区（UTC 存储，展示时转北京时间）。
- 注释、文档、提交信息用中文，标识符用英文。

## 5. 任务、分支、PR

1. 任务编号：Claude `P<阶段>-C<n>`、Gemini `P<阶段>-M<n>`、Grok `P<阶段>-R<n>`。每个任务开工前 Claude 在 `docs/task-cards/` 给出任务卡。
2. 分支名：`claude/P1-C1-kb-gateway`、`gemini/P1-M1-duties`、`grok/P0-R1-compose` 这种格式。
3. 一个 PR 只做一件事，不超过 400 行（不含锁文件、fixtures 导出、测试数据）。
4. PR 描述：做了什么 / 怎么测的 / 任务编号 / 新增依赖及许可证。
5. 流程：CI 全绿 → Claude 审（Claude 自己的 PR 由 JQ 看） → 作者修改 → JQ 合并。不直接推 `main`。
6. 每个 PR 必须带的测试：权限拒绝（无岗位、岗位不符）；敏感字段不出现在返回和日志里；Frappe fixtures 能在空库导入。

## 6. 节奏

周一 Claude 发本周计划，周五发周报；每阶段末按方案验收。阶段：P0 部署与验证（10-15 – 10-23）、P1 组织与知识（10-26 – 11-13）、P2 流程与 Agent（11-16 – 12-04）、P3 试运行与上线（12-07 – 12-25），12-28 上线。
