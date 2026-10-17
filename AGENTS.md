# AGENTS.md：共用开发规范

本仓库由 JQ、Claude、GPT 共同开发，Grok bot 负责测试 PR。Claude 和 GPT 开工前必须先读完本文件，再读各自的 `CLAUDE.md`、`GPT.md`。
方案依据：《企业基础平台开发方案（第 5 版）》。一句话：**Dify 管 AI，Frappe + Frappe HR + Frappe Wiki 管人和流程，我们只写把两边连起来的 5 个模块（X1–X5）。**

## 1. 目录归属

| 目录 | 负责人 | 内容 |
| --- | --- | --- |
| `services/kb_gateway/` | Claude | X3 按岗位检索服务（独立服务） |
| `services/frappe_mcp/` | Claude | X4 Frappe MCP 服务（独立服务） |
| `services/policy_sync/` | Claude | X5 制度文档同步（独立服务） |
| `config/dify/` | Claude | Dify 应用 DSL 导出（问答应用、Agent 应用） |
| `.github/` | Claude | CI |
| `tests/e2e/` | Claude | 端到端测试 |
| `docs/`（`docs/manual/` 除外） | Claude | 架构、ADR、验证清单、任务卡、周报 |
| `deploy/` | GPT | 全部开源组件的部署、升级、备份、监控 |
| `frappe_apps/enterprise_base/` | GPT | `duties/` 是 X1 岗位职责扩展，`wecom/` 是 X2 企业微信对接 |
| `config/frappe/` | GPT | Frappe 导出的 fixtures（Workflow、Role、Custom Field） |
| `config/dify-on-wechat/` | GPT | 企业微信入口配置 |
| `docs/manual/` | GPT | 用户、管理员、运维手册 |
| 根目录文件（本文件、`.gitignore` 等） | Claude | 改动在 PR 里提出 |

不改别人负责的目录。确实需要时，在自己的 PR 描述里写清楚原因。

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

1. 任务编号：Claude `P<阶段>-C<n>`、GPT `P<阶段>-G<n>`。GPT 的任务由 JQ 分发，每个任务开工前 Claude 在 `docs/task-cards/` 给出任务卡。
2. 分支名：`claude/P1-C1-kb-gateway`、`gpt/P0-G1-compose` 这种格式。
3. 一个 PR 只做一件事，不超过 400 行（不含锁文件、fixtures 导出、测试数据）。
   任务太大要拆成几个 PR 时，每个 PR 的目标分支都是 `main`，按顺序合并；不要把 PR 的目标分支设成上一个 PR 的分支，否则合并后改动进不了 `main`。
4. PR 描述按 `.github/pull_request_template.md`：做了什么 / 怎么测的 / 任务编号 / 新增依赖及许可证 / **给 Grok 的测试步骤**。
5. 流程：CI 全绿 → **Grok bot 按 PR 里的测试步骤实测**（见 `docs/Grok测试说明.md`）→ 互审（Claude 审 GPT 的 PR，GPT 复看 Claude 的 PR）→ 作者按测试和审阅意见修改 → JQ 合并。Grok 报的问题由 PR 作者修，修完 Grok 复测。不直接推 `main`。
6. 每个 PR 必须带的测试：权限拒绝（无岗位、岗位不符、未授权用户不能登录）；敏感字段不出现在返回、同步结果和日志里；Frappe fixtures 能在空库导入；部署改动附一次成功运行的日志摘要（去掉密钥）。

### GPT 的代码怎么进仓库

两种方式都按上面的规则走，由 JQ 定用哪种：

- **GPT 自己提 PR**（默认）：GPT 用自己的 GitHub 账号，从 `gpt/…` 分支提 PR。
- **Claude 代提交**：GPT 把改动交给 JQ（补丁、压缩包或文件），JQ 转给 Claude。Claude 原样放进 `gpt/…` 分支，提交信息写明“代 GPT 提交”，PR 描述照 GPT 给的写，只修 lint、格式这类机械问题，并在 PR 里逐条列出改了什么。实质性问题不替 GPT 改，写成审阅意见退回 GPT。

两种方式下 Claude 都要审，JQ 都要合并；代提交时 Claude 不能既改又判“可合并”，审阅意见必须写在 PR 里，留给 JQ 看。

GPT 不能直接登录服务器时：GPT 写好脚本和步骤，由 JQ 执行，把输出（去掉密钥）贴回给 GPT。

## 6. 节奏

周一 Claude 发本周计划，周五发周报；每阶段末按方案验收。阶段：P0 部署与验证（10-15 – 10-23）、P1 组织与知识（10-26 – 11-13）、P2 流程与 Agent（11-16 – 12-04）、P3 试运行与上线（12-07 – 12-25），12-28 上线。
