# GPT.md

先读 `AGENTS.md`。GPT 是部署运维和 Frappe 侧负责人。

- 负责：全部开源组件的部署、升级、备份、监控（`deploy/`）；X1 岗位职责扩展和 X2 企业微信对接（`frappe_apps/enterprise_base/`）；Frappe 里的组织、岗位、权限和审批流程配置（`config/frappe/`）；dify-on-wechat 接入（`config/dify-on-wechat/`）；用户、管理员和运维手册（`docs/manual/`）。
- 只用官方镜像、配置、插件、Frappe App、DocType、Custom Field、Hooks、Fixtures 和 REST 接口；每个组件锁定版本号（镜像写到具体版本或 digest，不用 `latest`），升级先在测试环境验证。
- Frappe 里的配置一律导出成 fixtures 进仓库，不在线上手工改了就算。
- X3、X4、X5 是 Claude 的独立服务，不要把它们的功能做进 Frappe App。它们依赖你提供的接口约定见 `services/kb_gateway/README.md`（Employee 字段 `wecom_userid`、X1 的白名单方法）。
- 仓库里只放 `.env.example`；真实密钥放服务器密钥文件。
- 你也复看 Claude 的 PR，有问题在 PR 里提（或交 JQ 转给 Claude）。
- 任务和验收标准见 `docs/task-cards/` 里以 `P*-G*` 开头的任务卡。
