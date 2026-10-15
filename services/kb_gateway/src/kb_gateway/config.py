"""服务配置：全部来自环境变量（前缀 EB_），不写死任何客户信息。"""

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EB_", env_file=".env", extra="ignore")

    # Dify Chatflow 调用本服务时带的服务间 Token
    kb_service_token: SecretStr = Field(min_length=32)

    # Frappe：查员工、部门、岗位和岗位可见的知识空间
    frappe_url: HttpUrl
    frappe_api_key: SecretStr
    frappe_api_secret: SecretStr
    # Employee 上存企业微信 userid 的自定义字段（由 X2 同步写入）
    frappe_wecom_field: str = "wecom_userid"
    # X1 提供的白名单方法：按岗位返回可见知识空间
    frappe_spaces_method: str = "enterprise_base.duties.api.knowledge_spaces_for_designation"

    # Dify 知识库 API
    dify_api_base: HttpUrl
    dify_dataset_api_key: SecretStr
    # 文档元数据里记录可见范围的字段（由 X5 写入）
    scope_metadata_field: str = "eb_scope"
    # 检索方式：知识库用高质量索引时 hybrid_search；经济索引只支持 keyword_search
    dify_search_method: str = "hybrid_search"

    top_k_max: int = Field(default=10, ge=1, le=50)
    http_timeout_seconds: float = 5.0
