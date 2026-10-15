# CLAUDE.md

先读 `AGENTS.md`，那里是三个 AI 共用的规范，本文件只补充 Claude 自己的职责。

- Claude 统筹：架构和选型、任务拆解和任务卡、周计划和周报、安全审查。
- Claude 写 `services/kb_gateway/`（X3）、`services/frappe_mcp/`（X4）、`config/dify/`（Dify 应用 DSL）和 `docs/`。
- Claude 审 Gemini 和 Grok 的每个 PR，检查：是否符合 `AGENTS.md`（尤其“不改开源源码”“不写死客户信息”）、有无越界修改、测试是否覆盖权限拒绝和敏感字段、有无密钥泄露、新依赖许可证。审完给出“可合并”或具体修改意见。
