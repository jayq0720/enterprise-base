# 任务卡 P1-G1（补充）：X1 知识空间接口和 Wiki 推送配置

- 负责：GPT　审核：Claude　验收：JQ
- 时间：随 P1-G1 X1 岗位职责扩展一起做（10-26 – 11-13）
- 分支：`gpt/P1-G1-duties`（和 P1-G1 同一个 PR 也可以）
- 背景：X5 制度同步（`services/policy_sync/`）和 X3 按岗位检索（`services/kb_gateway/`）都要从 X1 读“哪个知识空间对应哪个 Wiki 空间、哪个 Dify 知识库、谁能看”。本卡定这份约定。

## 要做的事

1. **DocType「知识空间」**（`frappe_apps/enterprise_base/duties/`）：
   - `space_name`（名称，唯一）
   - `wiki_space`（Link → Wiki Space）
   - `dify_dataset_id`（Data，管理员在后台填 Dify 知识库 ID，不写死在代码里）
   - `visible_to_all`（Check，全员可见）
   - `departments`（子表，Link → Department，可选）
   - 岗位职责里“可见的知识空间”链接到这个 DocType。
2. **白名单方法** `enterprise_base.duties.api.knowledge_spaces()`，无参数，返回：

   ```json
   [
     {"space": "公司制度", "wiki_space": "77ot2jorm1", "dify_dataset_id": "b293…", "scope": ["all"]},
     {"space": "人事手册", "wiki_space": "a1lm5v7ot2", "dify_dataset_id": "9a6b…", "scope": ["dept:人事部 - TC", "desig:HRBP"]}
   ]
   ```

   - `scope` 的算法：勾了全员可见就是 `["all"]`；否则是 `departments` 里每个部门的 `dept:<Department.name>`，加上所有在“可见的知识空间”里选了它的岗位的 `desig:<Designation.name>`。
   - 部门用 Department 的 `name`（带公司缩写，例如 `人事部 - TC`），不要用 `department_name`。
   - 一个空间既不全员可见、也没有部门和岗位时，`scope` 返回空列表（X5 会把它的文档全部撤下）。
   - 只允许下面第 4 条的集成角色调用；不返回任何员工个人信息。
3. **保存时校验**：部门名、岗位名不能含英文逗号（`eb_scope` 用逗号分隔）；同一个 Wiki 空间只能绑定一个知识空间。
4. **集成用户和角色**（写进 fixtures，Key 不进仓库）：角色 `EB Integration`，只读 `Employee`、`Department`、`Designation`、`Wiki Document`、`Wiki Space`、`知识空间`，可调用上面的方法。X3、X5 各用一个绑定这个角色的 API 用户。
5. **Wiki 推送**：两条 Frappe Webhook（fixtures 放 `config/frappe/`）：
   - DocType `Wiki Document`，事件 `on_update` 一条、`on_trash` 一条
   - 地址 `http://policy-sync:8080/v1/hooks/wiki`，POST，请求结构 JSON
   - 请求体 `{"doctype": "Wiki Document", "name": "{{ doc.name }}"}`（只发编号，X5 会回 Frappe 读正文）
   - 开启 Enable Security，Secret 用部署时生成的 `EB_SYNC_WEBHOOK_SECRET`；不要把 Token 放在请求头里，Frappe 会把请求头写进 Webhook Request Log。
6. **部署**（`deploy/`）：加 `policy-sync` 服务，镜像来自本仓库 CI 发布，环境变量见 `services/policy_sync/.env.example`；`prepare.py` 生成 `EB_SYNC_WEBHOOK_SECRET`、`EB_SYNC_SERVICE_TOKEN`（`secrets.token_urlsafe(32)`）；它要能访问 Frappe 和 Dify API，Frappe 的队列容器要能访问它。

## 交付

- PR：上面的 DocType、方法、fixtures、compose 改动，加测试：
  - `scope` 算法：全员可见、只有部门、只有岗位、都没有四种情况
  - 名称带英文逗号时保存被拒
  - 没有 `EB Integration` 角色的用户调用方法被拒

## 验收

- 在测试站点上配两个知识空间（一个全员可见，一个只给人事部和 HRBP），调用方法的返回和上面的格式一致。
- 起 X5 后，在 Wiki 里发布、修改、取消发布、删除页面，Dify 对应知识库里的文档跟着变（Claude 已在本地用临时脚本代替 X1 跑通过，见 `services/policy_sync/README.md`）。
