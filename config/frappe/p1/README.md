# P1 X1 配置导出

来源：[真实 Frappe CI 38859062030](https://github.com/qianjie0720-sys/enterprise-base/actions/runs/38859062030)，提交 `c0c3fc7f8a67f2e2e679e10aa364c963c68aec33`，2026-10-16 06:11 UTC。导出 ZIP SHA256：`f35a3d091c2b67a36fed42a457350019351218abcb79546e8d4794ededb55f1c`。

通过官方 export-fixtures 导出并净化：Role 1、Custom Field 3、只读 Custom DocPerm 5、技术 User 2、Webhook 2。这里与 App 对应文件逐字一致；User 位于 App 的 setup_fixtures，仅首次创建，避免原生 fixture 迁移重建用户并清空凭据。Webhook 导出为禁用，安装/迁移时由服务器 Secret 安全启用。

该次运行两个新站点各 7 项测试通过，并通过真实 HTTP API 认证/权限拒绝、原生签名、空库导入和保留技术账号凭据检查。重跑会产生新的导出时间戳，不应无故覆盖审核快照。客户环境 Wiki 发布/修改/撤回/删除至 Dify 的全链路验收仍待执行，步骤见 [交接手册](../../../docs/manual/P1-X1知识空间与Wiki同步.md)。
