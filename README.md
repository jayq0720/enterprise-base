# enterprise-base：企业基础平台

企业内部知识库、工作流和岗位职责的基础平台。用开源项目组装：

- **Dify**：模型插件（千问、豆包、DeepSeek）、问答和 Agent 应用、知识库。
- **Frappe + Frappe HR + Frappe Wiki**：部门、岗位、员工、审批流程、制度文档、管理后台。
- **dify-on-wechat**：员工在企业微信里对话的入口。

我们自己只写 5 个连接模块：

| 编号 | 位置 | 模块 | 负责 |
| --- | --- | --- | --- |
| X1 | `frappe_apps/enterprise_base/duties/` | 岗位职责扩展（Frappe App） | Gemini |
| X2 | `frappe_apps/enterprise_base/wecom/` | 企业微信对接（Frappe App） | Grok |
| X3 | `services/kb_gateway/` | 按岗位检索服务 | Claude |
| X4 | `services/frappe_mcp/` | Frappe MCP 服务 | Claude |
| X5 | `services/policy_sync/` | 制度文档同步 | Gemini |

开发规范见 [AGENTS.md](AGENTS.md)；架构决定见 [docs/adr/](docs/adr/)；本地部署见 `deploy/LOCAL.md`（P0-R1 交付）。
