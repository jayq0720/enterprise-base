"""服务配置：全部来自环境变量（前缀 EB_），不写死任何客户信息。"""

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EB_", env_file=".env", extra="ignore")

    # Dify 调本服务时带的服务间 Token（MCP 服务配置里的固定请求头，以及 /v1/session）
    mcp_service_token: SecretStr = Field(min_length=32)
    # 会话令牌的签名密钥，和服务 Token 分开
    mcp_session_secret: SecretStr = Field(min_length=32)
    session_ttl_seconds: int = Field(default=900, ge=60, le=3600)

    frappe_url: HttpUrl
    frappe_api_key: SecretStr
    frappe_api_secret: SecretStr
    # Employee 上存企业微信 userid 的自定义字段（由 X2 同步写入）
    frappe_wecom_field: str = "wecom_userid"
    # MCP 防 DNS 重绑定：允许的 Host 头（Dify 访问本服务用的主机名和端口）
    mcp_allowed_hosts: list[str] = ["frappe-mcp:8080", "localhost:*", "127.0.0.1:*"]
    # 列表类工具最多返回几条
    list_limit: int = Field(default=10, ge=1, le=50)
    http_timeout_seconds: float = 10.0
