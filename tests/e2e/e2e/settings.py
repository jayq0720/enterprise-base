"""测试环境的地址和凭据，全部来自环境变量（前缀 EB_E2E_），不写死任何客户信息。

只能指向测试环境：测试会用管理员 Key 新建、删除合成的部门、岗位、员工、Wiki 和知识库。
"""

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class E2ESettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EB_E2E_", env_file=".env", extra="ignore")

    # Frappe：测试环境的管理员（System Manager + HR Manager）API Key
    frappe_url: HttpUrl
    frappe_host: str | None = None
    frappe_admin_key: SecretStr
    frappe_admin_secret: SecretStr
    # 不填就用 Frappe 里的第一家公司
    company: str | None = None

    # Dify 服务 API（…/v1）和知识库 API Key（建临时知识库、看同步结果）
    dify_api_url: HttpUrl
    dify_host: str | None = None
    dify_dataset_key: SecretStr
    # 临时知识库的索引方式；装了向量模型的环境可改成 high_quality
    dify_indexing_technique: str = "economy"
    # HR 助手应用的 API Key；不填就跳过对话测试（对话测试要模型 Key 到位）
    hr_app_key: SecretStr | None = None

    x3_url: HttpUrl
    x3_token: SecretStr
    x4_url: HttpUrl
    x4_token: SecretStr

    # 发起请假测试用的假期类型（测试会给合成员工分配这类假期）
    leave_type: str = "年假"
    # 等 Webhook → X5 → Dify 同步完成的最长秒数
    sync_timeout_seconds: float = Field(default=180, gt=0)
    http_timeout_seconds: float = 60
