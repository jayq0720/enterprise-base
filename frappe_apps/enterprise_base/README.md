# X1 Frappe App（GPL-3.0-only）

依赖固定版本 Frappe/ERPNext、HRMS、Wiki，见 deploy/VERSIONS.md。标准 DocType `EB Knowledge Space` 的中文界面名是“知识空间”；Designation 的“岗位职责”和“可见的知识空间”是 Custom Field，Employee 的 `wecom_userid` 是唯一 Data 字段。

本部分提供数据模型和名称/唯一性校验；知识空间接口、API 集成权限、Webhook 和部署分后续小 PR 提供。没有实现 X3/X4/X5 的业务。

本地检查：`uv sync --frozen && uv run ruff check . && uv run ruff format --check . && uv run mypy enterprise_base`。

真实测试：在安装此 App 的测试站点执行 `bench --site <测试站点> run-tests --app enterprise_base`。本机没有 Frappe/MariaDB，静态检查不能替代该测试；后续集成 CI 将验证安装、空库导入并用官方 export-fixtures 导出配置。

初始化 hook 均为可重放代码。fixtures 必须来自真实站点导出，密码、API Key 和用户数据不导入仓库；后续提交会包含实际导出记录。
