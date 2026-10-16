# X1 Frappe App（GPL-3.0-only）

依赖固定版本 Frappe/ERPNext、HRMS、Wiki，见 deploy/VERSIONS.md。标准 DocType `EB Knowledge Space` 的中文界面名是“知识空间”；Designation 的“岗位职责”和“可见的知识空间”是 Custom Field，Employee 的 `wecom_userid` 是唯一 Data 字段。

提供数据模型、知识空间接口、API 集成只读边界和原生 Wiki Webhook 配置。X3/X4/X5 保持独立服务，App 不实现它们的业务。

本地检查：`uv sync --frozen && uv run ruff check . && uv run ruff format --check . && uv run mypy enterprise_base`。

真实测试：在安装此 App、已注入测试签名 Secret 的测试站点执行 `bench --site <测试站点> run-tests --app enterprise_base`。`frappe-contract` CI 构建固定 Python 3.12 组合，验证安装、空库导入及官方配置导出。静态检查不能替代真实站点测试，不在生产站点运行测试。

初始化 hook 均为可重放代码。fixtures 必须来自真实站点导出；技术账号模板放在 setup_fixtures，只创建缺少的账号，迁移不重置已有凭据。密码、API Key 和员工数据不进仓库。部署和配置步骤见 [交接手册](../../docs/manual/P1-X1知识空间与Wiki同步.md)。
