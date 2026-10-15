# P0-G3/G4 Frappe 试配与验收步骤

状态：离线准备，未在 Frappe 执行。JQ 于 2026-10-15 确认服务器尚未部署，实测延期。以下配置仅用于测试环境；不能把这份步骤当作已导出的 fixtures。部署版本以 `deploy/VERSIONS.md` 为准，结果填 `docs/p0-results/frappe-试用报告.md`。

## 组织测试数据

先完成 `deploy/LOCAL.md` 的登录、应用安装及测试公司向导，确认 Frappe、ERPNext、HRMS、Wiki 的实际版本。公司用“P0测试公司”，不填真实组织、联系方式或财务信息。建立两个测试部门、五个 Designation，再建立十个在职测试员工；ID 使用实际生成值，测试编号另行记录映射，不假定 Employee.name 等于姓名。

| 员工测试编号 | 姓名 | 测试部门 | 测试岗位 | 预留企业微信映射（仅假数据） |
| --- | --- | --- | --- | --- |
| 01 | 测试员工01 | P0研发部 | P0研发岗 | p0_test_01 |
| 02 | 测试员工02 | P0研发部 | P0研发岗 | p0_test_02 |
| 03 | 测试员工03 | P0研发部 | P0研发主管 | p0_test_03 |
| 04 | 测试员工04 | P0研发部 | P0研发主管 | p0_test_04 |
| 05 | 测试员工05 | P0行政部 | P0行政岗 | p0_test_05 |
| 06 | 测试员工06 | P0行政部 | P0行政岗 | p0_test_06 |
| 07 | 测试员工07 | P0行政部 | P0人事岗 | p0_test_07 |
| 08 | 测试员工08 | P0行政部 | P0人事岗 | p0_test_08 |
| 09 | 测试员工09 | P0行政部 | P0HR主管 | p0_test_09 |
| 10 | 测试员工10 | P0行政部 | P0HR主管 | p0_test_10 |

手机号、证件、银行卡、薪资字段全部留空；只填写当前版本要求的测试入职日期、生日等必填假数据。创建独立的测试员工、主管、HR 用户，邮件用 `example.invalid` 测试域；密码只在测试服务器设置，不导出到仓库。测试员工01的直属主管设为测试员工03，HR 审批人用测试员工09；主管/HR 的岗位和系统角色分别记录。

## Employee 企业微信字段与 REST

通过官方 Custom Field/Customize Form，在 Employee 增加 Data 字段，fieldname 必须准确为 `wecom_userid`，开启唯一约束。部分界面默认添加 `custom_` 前缀：保存后检查实际 fieldname，不能误把 `custom_wecom_userid` 当成协议字段。若该界面不允许目标名称，采用官方 Custom Field DocType/REST 创建或经测试 App 安装导入，不修改框架源码。

给十个员工写入上表假 userid。尝试给另一个员工保存重复 userid，必须被拒绝；保存失败不算已经验证数据库唯一索引，空库导入后也要再测。企业微信真联调时另用授权测试应用内的真实测试账号，映射只留在服务器，不把这些真实 userid 替换进已提交的测试资料。

创建最小权限 API 服务用户，在服务器保存 API Key/Secret。用官方 Employee REST 接口按 `wecom_userid` 查询，只请求 `name,department,designation,status`；客户端再检查员工在职、部门/岗位存在、恰好一条匹配。不要用管理员 Token 得出的结果证明服务用户权限正确。请求对应任务卡形式：

```text
GET /api/resource/Employee
filters=[["wecom_userid","=","p0_test_01"]]
fields=["name","department","designation","status"]
```

在服务器使用客户端的安全凭据配置，不把认证值写入命令或报告。分别验证有效映射、未知 userid、离职员工、无岗位、无部门、无认证和无权限用户。Frappe 资源接口可能返回空列表或仍返回允许读取的非在职记录；X3/X4 必须自行拒绝未知/离职/无岗位身份，不能假定资源接口等同身份校验。检查响应字段白名单，无手机号、证件、银行或薪资字段；日志检查只输出通过/未通过摘要。

## 请假审批与权限拒绝

先创建测试假期类型、节假日/工作日及必要的请假额度，选额度足够的日期。记录当前 HRMS `Leave Application` 的业务 status：`Open/Approved/Rejected/Cancelled`，将 Workflow 自定义状态与业务 status、docstatus 分别映射，避免只改界面标签。

配置草稿 → 待直属主管 → 待HR → 已批准，另设退回/拒绝路径。员工只能提交自己的申请；第一步仅单据 `leave_approver` 指定的主管能操作；第二步仅指定测试 HR 角色/用户能操作；同角色的其他主管不能代批。使用测试角色并配合内置角色和文档权限，不给普通用户 System Manager 来绕过权限。将允许编辑角色、跳转角色、条件和状态字段全部导出。

用三个真实登录会话完成一次员工提交、主管通过、HR 通过；再创建第二单，主管或 HR 退回。记录最终 Workflow 状态、HRMS status、docstatus 及额度影响。再测其他主管越权、员工替别人发起、员工直接跳到已批准、无岗位用户尝试审批、禁用用户登录，均记录实际拒绝行为。UI 隐藏按钮不能代替 REST/文档权限拒绝验证。

按岗位授权试验单独建立测试角色，使用 Role Permission Manager 控制 DocType 操作，使用 User Permission 限定实际 Link 字段的记录，并设置 Applicable For。Designation 是岗位数据，不会自动成为系统 Role；没有目标岗位/部门 Link 的 DocType，不能假定权限会递归生效。对列表、按 name 读取、导出分别使用授权/非授权账号验证，记录角色叠加导致的放宽情况。X1 的岗位职责清单和白名单方法属于后续 App 扩展，本试配不实现 X3/X4/X5。

## Wiki 发布、下架与 X5 接口调查

固定 Wiki v3.4.0 源码中同时存在：旧 `Wiki Page` 的 `published/content/route/allow_guest`；新版 `Wiki Document` 的 `is_published/content/route/wiki_space`；`Wiki Space` 也有 `is_published/roles/root_group`。官方 hooks 已对 `Wiki Document` 的 `on_update/on_trash` 和 `Wiki Space` 的 `on_update/on_trash` 注册处理。依据：[旧页面](https://github.com/frappe/wiki/blob/v3.4.0/wiki/wiki/doctype/wiki_page/wiki_page.json)、[新版文档](https://github.com/frappe/wiki/blob/v3.4.0/wiki/frappe_wiki/doctype/wiki_document/wiki_document.json)、[空间](https://github.com/frappe/wiki/blob/v3.4.0/wiki/wiki/doctype/wiki_space/wiki_space.json)、[hooks](https://github.com/frappe/wiki/blob/v3.4.0/wiki/hooks.py)。这只是源码核对，尚未确认实际 UI 操作触发链。

在测试 UI 新建文档并发布、改正文、下架、重新发布、删除，再改变整个空间的发布状态及访问角色。使用官方 Webhook 或测试 App 的 doc_events 观察实际 DocType、事件、字段和投递次数，只使用纯测试内容。向 Claude 提供实际对象名、字段、前后状态、投递失败重试/重放行为和脱敏样例；不默认 X5 只监听旧 Wiki Page。下架、删文档、空间撤权都必须能让 X5 删除或禁用已同步内容；这部分消费逻辑仍由 Claude 独立服务实现。

Wiki 定时遥测在该版本使用 `is_pulse_enabled()` 判断；部署中的关闭配置须在运行时核验，不能只看开关名称就宣称无外联。

## fixtures 与企业微信验收

导出流程见 `config/frappe/p0/README.md`。保留测试数据与所有引用依赖，另建全新空测试站点安装相同版本 App，再导入并复测部门/岗位/员工、审批两路径、重复 userid 拒绝及权限拒绝。未完成导出和空库复现前不判通过。

最后按 `deploy/LOCAL.md` 配置授权企业微信测试应用，验证回调校验、一次入站消息和回复；在 Dify Chatflow 的系统变量中检查 `sys.user_id` 与该测试账号 userid 相同。记录相等/不相等及测试环境编号，不粘贴真实 userid、原始消息或密钥；dify-on-wechat 原始日志继续丢弃。可信域名、可信 IP、备案要求由 JQ 在部署时确认；本阶段未实测，不能据此宣称渠道可用。
