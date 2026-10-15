# GEMINI.md

先读 `AGENTS.md`。Gemini 是 Frappe 侧负责人。

- 负责：X1 岗位职责扩展（`frappe_apps/enterprise_base/duties/`）、X5 制度同步（`services/policy_sync/`）、Frappe 配置 fixtures（`config/frappe/`）、端到端测试（`tests/e2e/`）、用户和管理员手册（`docs/manual/`）。
- Frappe 里的配置（工作流、角色、自定义字段）一律导出成 fixtures 进仓库，不在线上手工改了就算。
- X5 是独立服务，只调 Frappe 和 Dify 的 HTTP 接口，不装进 Frappe。
- 任务和验收标准见 `docs/task-cards/` 里以 `P*-M*` 开头的任务卡。
