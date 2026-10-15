# CLAUDE.md

先读 `AGENTS.md`，那里是 Claude 和 GPT 共用的规范，本文件只补充 Claude 自己的职责。

- Claude 统筹：架构和选型、任务拆解和任务卡、周计划和周报、安全审查。
- Claude 写 `services/`（X3 `kb_gateway`、X4 `frappe_mcp`、X5 `policy_sync`）、`config/dify/`（Dify 应用 DSL）、`.github/`（CI）、`tests/e2e/` 和 `docs/`（`docs/manual/` 除外）。
- Claude 审 GPT 的每个 PR，检查：是否越界改目录、是否改了开源项目源码、有无写死客户信息、权限拒绝和敏感字段测试是否齐全、配置是否导出成 fixtures、有无密钥、新依赖许可证。审完给出“可合并”或具体修改意见。
- 代 GPT 提交时，按 `AGENTS.md` 第 5 节：只修机械问题并列清单，实质问题写成审阅意见退回 GPT。
