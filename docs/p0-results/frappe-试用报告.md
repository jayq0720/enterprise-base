# P0-G3/G4 Frappe 试用报告（待实测）

**尚未完成试用或验收。** 2026-10-15，JQ 确认尚无服务器，运行测试延期。目前完成步骤、测试数据规划及固定版本 Wiki 源码调查；未生成 fixtures，不含实际截图。版本计划为 Frappe v15.123.1、ERPNext v15.123.0、HRMS v15.64.3、Wiki v3.4.0，实际安装版本待填。

| 清单项 | 已准备/源码发现 | 运行结果 |
| --- | --- | --- |
| 5.1 | 两部门、五岗位、十员工测试映射已定义 | 未创建，待测 |
| 5.2 | 员工 → 指定主管 → HR；业务状态与 docstatus 单独检查 | 成功/退回/越权拒绝均待测 |
| 5.3 | Custom Field 需准确为 wecom_userid、Data、唯一；REST 四字段白名单 | 建字段、查询、重复拒绝待测 |
| 5.4 | Wiki v3.4.0 有 Wiki Document.is_published 与空间发布/角色；旧 Wiki Page.published 仍在 | 实际事件、Webhook 与撤权链路待测 |
| 5.5 | Role 控制操作，User Permission 限制 Link 记录；Designation 不等于 Role | 按岗位/文档读取与导出隔离待测 |
| 5.5 模块可见性 | 普通用户隐藏 ERPNext 不需用的财务/销售模块，并验证 REST/报表权限 | 待试配及空库重测，不以隐藏菜单代替权限拒绝 |
| 5.6 | 本报告为准备记录 | 结论待试用证据 |
| 3.1、6.1 | 企业微信回调与 sys.user_id 相等的判定步骤已定义 | 回调、收发、身份均待测 |
| 6.2 | 客户公网域名、可信 IP 与备案要求 | 待部署时由 JQ 确认 |

能力初判：岗位职责清单是 X1 计划扩展项，尚未实现；按岗位审批需要把岗位与角色、指定审批人及 Workflow 条件关联，尚不能宣称现成配置已经满足；按岗位授权有原生角色和 User Permission 机制，但跨 DocType、角色叠加与读取/导出隔离必须实测。当前不把任何一项判定为“已满足”或“做不到”。

给 Claude 的 X5 调查：固定版本的官方 hooks 对 Wiki Document、Wiki Space 注册 on_update/on_trash；发布状态字段、空间角色变化均可能影响同步范围。请勿在未实测前仅按旧 Wiki Page.published 设计同步。源码链接、测试步骤和需要采集的事件信息见手册。

另修正部署初始化的遥测关闭位置：从 site_config 同名值改为通过官方接口写 System Settings.enable_telemetry=0。已核对固定版本读取源码并校验 Compose；数据库实际值与外联行为仍待运行验证。

待补证据：实际安装版本；审批两路径截图及业务状态；API 白名单和拒绝测试摘要；Wiki 事件与重复/重试记录；企业微信身份相等结果；fixtures 导出文件及空库复现摘要。所有真实凭据和真实账号映射留在服务器。

操作步骤：[`P0-Frappe试配与验收.md`](../manual/P0-Frappe试配与验收.md)。导出约定：[`config/frappe/p0/README.md`](../../config/frappe/p0/README.md)。执行人/日期/复核人及 JQ 的 Frappe 底座确认均待实际试用后填写；清单 5、6 节保持待验收。
