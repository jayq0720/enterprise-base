# X3 按岗位检索服务（kb_gateway）

员工在企业微信里提问时，只在他的岗位有权看的文档里检索。独立服务，只通过 HTTP 调用 Frappe 和 Dify。

为什么不直接用 Dify 的“外部知识库接口”：那个接口不带提问人身份，见 `docs/adr/0001-身份传递.md`。

## 流程

1. Dify 问答应用（Chatflow）的 HTTP 请求节点调 `POST /v1/search`，请求头 `Authorization: Bearer <EB_KB_SERVICE_TOKEN>`，请求体 `{"user_id": "{{#sys.user_id#}}", "query": "{{#sys.query#}}", "top_k": 5}`。`user_id` 必须绑定系统变量，不能交给模型填。
2. 按企业微信 userid 到 Frappe 查在职员工的部门、岗位（只取这三个字段）；查不到、或一个 userid 对应多人，一律不检索。
3. 调 X1 提供的方法，取这个岗位可见的知识空间（每个空间对应一个 Dify 知识库）。
4. 在每个知识库里检索，用文档元数据 `eb_scope` 过滤；返回后再在本地复核一遍，缺范围标记的文档一律丢弃。
5. 合并、按分数排序，返回出处列表；检索不到时 `notice` 给出原因，由 LLM 转告员工。

## 依赖其他模块的约定

| 约定 | 提供方 | 内容 |
| --- | --- | --- |
| Employee 自定义字段 `wecom_userid` | X2（GPT） | 通讯录同步时写入企业微信 userid，唯一 |
| 白名单方法 `enterprise_base.duties.api.knowledge_spaces_for_designation` | X1（GPT） | 参数 `designation`；返回 `[{"space": "公司制度", "dify_dataset_id": "..."}]`；只给本服务的 API 用户开权限 |
| 文档元数据 `eb_scope` | X5（Claude） | `,all,` 或 `,dept:<部门>,desig:<岗位>,`；部门、岗位名不能含英文逗号 |

字段名和方法路径都可以用环境变量改，见 `.env.example`。

## 本地开发

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest
```

## 状态

P0 骨架：接口、权限过滤和测试已完成，用假的 Frappe、Dify 测试。Dify 检索 API 的实际字段名待 P0 验证清单 3.3 实测后核对（解析集中在 `dify.py`）。
