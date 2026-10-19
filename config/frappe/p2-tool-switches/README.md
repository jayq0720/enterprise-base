# P2-G3 岗位助手开关原生配置导出

来源：[原生 CI 39185773010](https://github.com/qianjie0720-sys/enterprise-base/actions/runs/39185773010)，提交 `825efc56fe7d27364181527aaaca4b0060b3ce8e`。产物 `12158111802`，ZIP SHA256 `86c30d690e92a26afaea3d3b8271f87fff199df2e7f7fd1a1581d32707e35a08`，已校验摘要并逐字节回填 App fixtures/customization 和本目录。两个站点各 47 项原生测试成功，旧 NULL 补1、关闭0保留、HR Manager 写入及其他角色拒绝、X4 精确只读、导出和第二空库导入通过。

新增 4 个默认1的 Check 和企业微信助手分节；现有岗位权限、知识空间权限级别保持原生导出值，仅时间戳更新。不含客户岗位勾选业务数据和凭据；回填后须再次通过 CI。先部署本卡 Frappe，再升级 P2-C2 的 X4，真实后台/企业微信对话待部署后联测。
