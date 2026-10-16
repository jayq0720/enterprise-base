"""服务配置：全部来自环境变量（前缀 EB_），不写死任何客户信息。"""

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EB_", env_file=".env", extra="ignore")

    # Frappe Webhook 的签名密钥（Webhook 上的 Secret），用来校验推送确实来自 Frappe
    sync_webhook_secret: SecretStr = Field(min_length=32)
    # 手动触发全量对账时带的服务间 Token
    sync_service_token: SecretStr = Field(min_length=32)

    # Frappe：读 Wiki 页面，读 X1 的知识空间绑定
    frappe_url: HttpUrl
    frappe_api_key: SecretStr
    frappe_api_secret: SecretStr
    # X1 提供的白名单方法：返回所有知识空间和 Wiki 空间、Dify 知识库、可见范围的绑定
    frappe_bindings_method: str = "enterprise_base.duties.api.knowledge_spaces"

    # Dify 知识库 API
    dify_api_base: HttpUrl
    dify_dataset_api_key: SecretStr
    # 高质量索引用 high_quality（需要 Embedding 模型）；没有模型时可用 economy
    dify_indexing_technique: str = "high_quality"
    # 文档元数据字段：可见范围（X3 按它过滤）、来源 Wiki 页面、正文摘要
    scope_metadata_field: str = "eb_scope"
    source_metadata_field: str = "eb_source"
    hash_metadata_field: str = "eb_hash"

    # 单个页面同步失败后的重试
    retry_attempts: int = Field(default=5, ge=1, le=20)
    retry_base_seconds: float = 2.0
    # 定时全量对账，补上漏掉的推送和可见范围变化；0 表示不定时
    reconcile_interval_seconds: int = Field(default=3600, ge=0)
    http_timeout_seconds: float = 15.0
