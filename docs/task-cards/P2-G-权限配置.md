# P2-G 权限配置（按权限复核表落到 Frappe）

依据：`docs/权限复核表.md`（JQ 2026-10-16 确认全部按默认）。只改 `config/frappe` 和 `frappe_apps/enterprise_base` 的 fixtures，不改 Frappe / HRMS / Wiki 源码。

## 要做的

1. **敏感字段只有 HR 管理员和本人能看**（复核表第 3 条）
   - Employee 上的手机号、身份证号、银行卡、薪资相关字段设权限级别（permlevel ≥ 1）。
   - permlevel 1 只给 HR Manager 读写；员工本人读自己档案时能看到（用 Employee 角色 + “If Owner”/User Permission，按 Frappe 实测可行的做法）。
   - HR User 能看全员档案，但看不到这些字段；REST `GET /api/resource/Employee/<id>` 和报表导出也要拿不到。
2. **制度发布要审核**（第 4 条）
   - Wiki 文档：HR User 能起草和修改，不能发布；只有 HR Manager 能发布（发布后才触发 X5 同步）。
   - 用 Workflow 或 Wiki 自带的发布权限实现，二选一，写明理由。
   - “知识空间”的可见范围只有 HR Manager 能改。
3. **离职当天停用**（第 7 条）
   - 员工状态改为 Left 时，自动禁用对应的 Frappe 用户（Server Script 或 X1 里的 doc_events 都行）。
   - X3、X4 已经只认 Active 员工，这一条是为了后台也同步停用。
4. **X4 接口账号**
   - 新建 Frappe 用户给 X4 用，角色只读 Employee（name、employee_name、user_id、wecom_userid）、Leave Type、Leave Application，加 Leave Application 新建权限；不给提交、审批、删除。

## 验收

- 用 HR User 账号通过 REST 读某员工档案，返回里没有敏感字段；HR Manager 能读到；员工本人能读到自己的。
- HR User 发布 Wiki 文档被拒；HR Manager 发布后 X5 收到 Webhook。
- 把测试员工改成 Left，对应用户被禁用；该员工在企业微信里问助手，收到“未找到你的在职员工信息”。
- X4 账号新建请假单成功，提交（submit）和删除被拒。
- 全部写进 fixtures，空库导入后复现。

做完开 PR（分支 `gpt/P2-G-permissions`），PR 描述里贴上面 5 条的实测结果，Claude 审核。
