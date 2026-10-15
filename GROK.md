# GROK.md

先读 `AGENTS.md`。Grok 是部署与企业微信负责人。

- 负责：全部开源组件的部署、升级、备份、监控（`deploy/`）、CI（`.github/`）、X2 企业微信对接（`frappe_apps/enterprise_base/wecom/`）、dify-on-wechat 配置（`config/dify-on-wechat/`）、运维手册。
- 只用官方镜像 + 配置 + 插件；每个组件锁定版本号（镜像写到具体版本或 digest，不用 `latest`）。
- 仓库里只放 `.env.example`；真实密钥放服务器密钥文件。
- 任务和验收标准见 `docs/task-cards/` 里以 `P*-R*` 开头的任务卡。
