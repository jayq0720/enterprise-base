# P1-G4 通讯录字段导出

来源：[真实 Frappe CI 39011175051](https://github.com/qianjie0720-sys/enterprise-base/actions/runs/39011175051)，提交 `da3cff82ff459b81f226c962c3af48b26efecc3c`，2026-10-17 06:55 UTC。ZIP SHA256：`11f8142e0c6c66fe4280ce84c59135fbeab0b41ba6f4ddd0558e8bc917e7eae5`。

`custom_field.json` 与该次原生导出、App fixtures 对应文件逐字一致；共四个字段，新增 Department 的唯一、只读企业微信部门 ID。两个空站点各 20 项测试通过，含权限拒绝、幂等、离职停用、回滚、敏感日志检查、导出与第二空库导入。企业微信联测仍待环境，配置步骤见 [同步手册](../../../docs/manual/P1-企业微信通讯录同步.md)。原 P1/P2 目录保留各自历史审核快照。
