# 任务卡 P0-G3 / P0-G4：Frappe HR 试配与试用报告

- 负责：GPT　审核：Claude　验收：JQ
- 时间：10-16 – 10-23（等你自己的 P0-G1 环境起来后开始；环境未好前可先在 Frappe 官方 Docker 本地试）
- 分支：`gpt/P0-G3-frappe-trial`

## 要做的事

1. 在 Frappe HR 里配 2 个部门、5 个岗位（Designation）、10 个测试员工。全部用测试数据：姓名用“测试员工01”之类，手机号、身份证号留空。
2. 配一条请假审批：员工 → 直属上级 → HR，走通一次（发起、上级通过、HR 通过；再走一次被退回）。
3. 在 Employee 上加 Custom Field `wecom_userid`（企业微信 userid，唯一），验证能用 REST `GET /api/resource/Employee?filters=[["wecom_userid","=","..."]]&fields=["name","department","designation","status"]` 查到。用 API Key/Secret 认证，Key 不进仓库。
4. 查 Frappe Wiki：页面发布、取消发布时有没有可以挂的 doc_events 或 webhook，记录事件名和字段（X5 要用）。
5. 试按岗位授权：某个角色只能读某些记录（User Permission / Role Permission），记录做法和限制。
6. 把第 1、2、3 步的配置导出为 fixtures，放 `config/frappe/p0/`。
7. 实测 dify-on-wechat 的 `wechatcom_app`（企业微信应用）渠道能否收发消息，Dify 运行日志里 `sys.user_id` 是否等于企业微信 userid（对应验证清单 3.1、6.1）。

## 交付

- PR：`config/frappe/p0/` 下的 fixtures + `docs/p0-results/frappe-试用报告.md`（一页纸：满足什么、缺什么、要扩展什么，配截图）。
- 在 `docs/P0-验证清单.md` 第 5、6 节逐项填结果。

## 验收

- fixtures 能在空库导入后复现部门、岗位、请假审批。
- 报告回答清楚：岗位职责清单、按岗位审批、按岗位授权各自是“自带 / 需扩展 / 做不到”；dify-on-wechat 能不能接上企业微信应用。
