# X1 知识空间与 Wiki 同步交接

代码按模型 → 接口 → Webhook/fixtures → 部署四份 PR 顺序审核合并；请等 Claude 回复“可合并”，不要绕过审核。镜像/fixtures 的 CI 记录见对应 PR；模型 Key、企业微信以及真实制度发布须在客户测试环境验收，不能用单元测试代替。

## 部署与凭据

在测试环境备份数据库、sites 卷和服务器密钥后，更新代码，在 `deploy/` 运行 `make init`、`make build`、`make up`。`make init` 只为旧环境补齐缺少的两项 X5 随机密钥，不覆盖已有值。新镜像安装 enterprise_base，迁移会导入配置并以服务器 `EB_SYNC_WEBHOOK_SECRET` 启用两条原生 Webhook。

新建的 `eb-x3@integration.invalid` 和 `eb-x5@integration.invalid` 是技术身份，默认禁用、没有 API Key，不发送欢迎邮件。管理员在 Frappe 后台启用两人并分别生成 Key/Secret，只授予 `EB Integration`，不要授予 HR Manager/System Manager，也不要使用 Administrator 凭据。X3 使用自己的凭据配置服务；X5 的凭据只录入服务器 `deploy/secrets/.env`：

- `EB_SYNC_FRAPPE_API_KEY`、`EB_SYNC_FRAPPE_API_SECRET`：X5 专属 Frappe API 凭据。
- `EB_SYNC_DIFY_DATASET_API_KEY`：管理员在 Dify 知识库后台生成的 Key。
- `EB_SYNC_DIFY_INDEXING_TECHNIQUE`：正式使用 high_quality 并配置 Embedding；无模型的测试可选 economy，不能把这项测试记为模型插件验收通过。
- `EB_SYNC_WEBHOOK_SECRET`、`EB_SYNC_SERVICE_TOKEN`：自动生成的两个独立 32 字节 base64url 值，不打印、不进 Git。
- `EB_POLICY_SYNC_IMAGE`：本仓库 CI 发布的 `ghcr.io/...@sha256:...`，不能改成 latest 或未固定 tag；私有 GHCR 包需在服务器交互登录，凭据不进仓库。

`make policy-sync` 检查上述配置后启动 X5。服务没有宿主机端口，使用 Frappe 网络访问 `frontend:8080`，通过 edge 网络访问 `dify/v1`；Frappe 队列可通过同一默认网络调用 `policy-sync:8080`。Frappe 只收到签名 Secret，不接收 Dify Key 或 X5 管理 Token。

## 两个知识空间

在 Frappe 管理后台创建两个 Wiki Space，再到“知识空间”（技术名 `EB Knowledge Space`）创建绑定：

1. 公司制度：选择 Wiki 空间，填写 Dify 知识库 ID，勾选全员可见，接口应返回 `["all"]`。
2. 人事手册：选择另一个 Wiki 空间和知识库，部门选择完整 Department name（含公司缩写）；在 Designation 的“可见的知识空间”中由 HRBP 岗位选中它。接口应包含 `dept:<完整部门名>` 和 `desig:<岗位名>`。

若 Wiki Space 设置了角色访问列表，需要由管理员将 `EB Integration` 加到该空间的 Read 列表，不能给 Write；原本为空的开放空间无需添加角色。Wiki 原生空间权限仍然生效，不能为了同步把受限空间改成全员开放或给技术用户 Wiki Manager。

名称不能含英文逗号。一个 Wiki Space 只能绑定一次。取消全员可见、清空部门且没有任何岗位选择它时，scope 为 `[]`，X5 会在后续对账中撤下该空间的文档；范围变化默认由每小时对账补齐，并非岗位保存瞬间推送。

更换知识库 ID 或删除绑定前，先将旧绑定的 scope 清空并完成一次对账，确认旧库文档已撤下，再修改绑定。当前 X5 只扫描仍在绑定列表中的库，不能靠删除绑定本身发现已脱离的旧库。

接口是无参数 GET `enterprise_base.duties.api.knowledge_spaces`，只允许集成角色。接口不读取 Employee；集成身份通过 REST 读取 Employee 时只准列表的 `name/department/designation`，详情、敏感字段、导出通用方法和写请求均拒绝。请用未授权测试账号再次确认拒绝，不用真实人员数据试探。

## 配置与验收证据

原生 Webhook 为 `EB Wiki Update`（on_update）、`EB Wiki Trash`（on_trash），只发 doctype/name；地址 `http://policy-sync:8080/v1/hooks/wiki`。启用 Enable Security，Secret 和 X5 一致，自定义请求头为空。不要把 Token 写入请求头，Frappe 会记录这些头。

配置变更通过 `bench --site <测试站点> execute enterprise_base.integration.export_configuration` 导出；先在测试环境操作。该方法调用官方 export-fixtures，再移除所有密钥、禁用技术账号和 Webhook 形成可移植配置；签名密钥仅在安装/迁移时注入。将 App 的 `enterprise_base/fixtures/*.json` 和 `enterprise_base/setup_fixtures/user.json` 安全复制回仓库，同时同步 `config/frappe/p1/`，核对一致再提交 PR。技术账号放在 setup_fixtures，只在账号不存在时创建；不能放回自动导入的 fixtures 目录，否则原生导入会重建 User 并破坏已配置的 API 凭据。不要复制生产 User、Employee 或 sites 配置。

在空站点安装固定版本组件及 App，确认 fixtures 可导入、权限仍只读、两条 Webhook 已安全启用，然后逐项记录：发布/修改/取消发布/删除 Wiki 页面时，Dify 文档及 eb_scope 是否变化；没有范围是否撤下；重复推送是否产生重复文档；错误签名是否 401；非集成用户是否被接口拒绝。仅反馈去密钥摘要，不能贴原始 Webhook Request Log、正文、人员信息或 API 凭据。
