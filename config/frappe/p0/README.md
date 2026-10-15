# P0 fixtures 导出与复现约定

当前没有运行中的 Frappe，**此目录尚无已导出的 fixtures**。这里仅定义导出范围和空库验收要求；不能把手写模板、计划或截图称作导出结果。JQ 已允许运行验证待服务器就绪后进行。

试配时只使用 `docs/manual/P0-Frappe试配与验收.md` 的虚构组织。通过官方界面/REST 配置完成并验证后，在我们自己的 `enterprise_base` App 中定义有过滤条件的 `fixtures` hook，只导出 P0 对象；不要更改 frappe、erpnext、hrms 或 wiki 的 hooks/源码。该 App 当前只有占位目录，导出前需要先完成正式 App 初始化并安装到测试站点，不能直接对占位目录执行 export-fixtures。

导出范围至少包括：

- `Custom Field`：Employee 的 `wecom_userid`，包含准确字段名、类型、唯一约束及权限属性。
- `Company/Department/Designation/Employee`：试配测试记录，以及实际必需的其他引用；真实员工数据不导出。
- `Workflow/Workflow State/Workflow Action Master` 与使用的自定义角色、权限配置；审批自定义字段若有增加也一起导出。
- 测试请假类型、必要的假期/额度等实际依赖；User Permission 等配置引用的测试用户需有可重复的安全前置创建步骤。
- `System Settings` 的遥测关闭配置，确认实际数据库单例值为 0，不能用 site_config 中的同名项替代。

不要导出真实用户、密码/哈希、API Secret、企业微信真实 userid、Token 或模型 Key。导出内容需人工复核，不能直接整库复制；若原生导出包含禁止入库的内容，暂停提交，先确定官方支持的最小导出范围，不直接手改 JSON 冒充完整导出结果。空库导入所需的测试用户/角色绑定应在运行流程中明确，不依赖作者机器已有账户。

官方导出命令的形态为 `bench --site <测试站点> export-fixtures --app enterprise_base`，原生输出位置是 App 的 `enterprise_base/fixtures/`。将原样导出副本及导出清单留在本目录，并保留 App 原生 fixtures 作为安装/迁移入口；不要假定 Frappe 自动扫描 `config/frappe/p0/`。复制仅用于归档，两处内容必须一致，任何改动都回到试配库重新导出。

验收时创建独立空测试站点、安装同版本依赖和 enterprise_base，执行正式安装/迁移流程，再按手册检查：2 部门、5 岗位、10 员工；wecom_userid 可查询且重复拒绝；审批成功/退回；无岗位/非指定主管/未授权用户拒绝；敏感字段不出现在接口、同步结果或日志。记录实际导出命令、文件列表、版本、导入输出摘要与截图，随后补到试用报告。

本说明不是已实现的 fixtures hook 或导入器；这些交付需在有测试环境后生成和验证。官方依据：[Fixtures hooks](https://docs.frappe.io/framework/user/en/python-api/hooks#fixtures)。
