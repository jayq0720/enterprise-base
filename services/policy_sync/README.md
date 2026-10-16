# X5 制度同步服务（policy_sync）

制度在 Frappe Wiki 里审核发布后，自动推入 Dify 知识库，并写上可见范围，X3 检索时按它过滤。独立服务，只通过 HTTP 调用 Frappe 和 Dify。

## 流程

1. Frappe 自带的 Webhook 在 `Wiki Document` 保存（`on_update`）和删除（`on_trash`）时推送到 `POST /v1/hooks/wiki`。本服务校验签名后立刻返回 202，后台同步。
2. 推送内容只用来知道是哪一页。页面正文、是否发布、属于哪个 Wiki 空间，一律回 Frappe 重新读。
3. 按 X1 的绑定找到这个 Wiki 空间对应的 Dify 知识库和可见范围：
   - 已发布：Dify 里没有这一页，或正文变了，就新建一份文档，写元数据 `eb_scope`（可见范围）、`eb_source`（Wiki 页面编号）、`eb_hash`（正文摘要），再删掉旧的。正文没变只是范围变了，只改元数据。
   - 取消发布、删除、移到没绑定的空间、可见范围无效：删掉 Dify 里这一页的所有副本。
4. 失败按指数退避重试（默认 5 次）。最终失败记日志，由定时全量对账（默认每小时）补上；也可以带 Token 调 `POST /v1/reconcile` 立即对账。

不在本地存状态：每次都按 `eb_source` 在所有绑定的知识库里找已有副本，所以页面换空间、重复推送、服务重启都不会留下多余副本。日志只记页面编号和错误类型，不记正文。

## 依赖其他模块的约定

| 约定 | 提供方 | 内容 |
| --- | --- | --- |
| 白名单方法 `enterprise_base.duties.api.knowledge_spaces` | X1（GPT） | 无参数；返回 `[{"space": "人事手册", "wiki_space": "<Wiki Space 编号>", "dify_dataset_id": "...", "scope": ["dept:人事部 - TC", "desig:HRBP"]}]`；全员可见时 `scope` 是 `["all"]`。见任务卡 `docs/task-cards/P1-G1-X1知识空间接口与Wiki推送.md` |
| Frappe Webhook | 部署配置（GPT） | `Wiki Document` 的 `on_update`、`on_trash` 各一条，JSON 请求体 `{"doctype": "Wiki Document", "name": "{{ doc.name }}"}`，开启签名，Secret 等于 `EB_SYNC_WEBHOOK_SECRET` |
| 文档元数据 `eb_scope` | 本服务写，X3 读 | `,all,` 或 `,dept:<部门>,desig:<岗位>,`，部门用 Department 的完整名称（例如 `人事部 - TC`） |

Frappe API 用户只需要读 `Wiki Document`、`Wiki Space` 和调用上面的 X1 方法。Dify 用知识库 API Key。

## 本地开发

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

## 状态

P1-C2 第一版。2026-10-16 在本地的真实 Frappe Wiki 3.4 和 Dify 1.17.1 上跑通：对账补齐漏推的页面，发布、修改、取消发布、移动空间、删除五种操作都由 Webhook 触发同步，结果正确。X1 的绑定方法当时用 Frappe 服务器脚本临时代替。
