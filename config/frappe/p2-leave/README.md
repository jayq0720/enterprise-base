# P2-G1 请假配置导出

来源：原生 Frappe CI [39088079424](https://github.com/qianjie0720-sys/enterprise-base/actions/runs/39088079424)，提交 `3427b009`，两个空站点各 26 项测试通过，HTTP 权限、导出、第二空库导入和技术账号凭据保持成功。产物 `12122180860`，ZIP SHA256 `fee309e773227b98d77d50cb20a1a053940ed50b72e4d1f1d12efa325c0053f0`；App fixtures/customization 与本目录逐字节回填，无用户凭据或业务记录。

包含 HR User/HR Manager 两级审批 Workflow/状态/动作、Custom Field，以及 Employee/Leave Application customization；旧 p1/p2 快照保留其历史来源，不混写。本次覆盖部门审批人回退、HR 自审批拒绝、提交后状态伪造拒绝及取消余额冲销；回填后的仓库文件仍需再跑 CI。真实客户/企业微信联测待服务器。
