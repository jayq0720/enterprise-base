"""服务配置：全部来自环境变量（前缀 EB_PORTAL_），不写死任何客户信息。"""

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EB_PORTAL_", env_file=".env", extra="ignore")

    # 员工在浏览器里打开的地址（https），扫码回调和 Origin 校验都用它
    public_url: HttpUrl
    # 登录会话 Cookie 的签名密钥
    session_secret: SecretStr = Field(min_length=32)
    session_ttl_seconds: int = Field(default=8 * 3600, ge=300, le=24 * 3600)
    # 每隔多久回 X4 重新确认一次“仍是在职员工”，离职、停用后最迟这么久失效
    recheck_seconds: int = Field(default=60, ge=0, le=600)

    # 企业微信自建应用（和 X2 扫码登录可以是同一个应用），可信域名要加上前台域名
    wecom_corp_id: str = Field(min_length=1)
    wecom_agent_id: str = Field(min_length=1)
    wecom_app_secret: SecretStr = Field(min_length=1)
    wecom_api_base: HttpUrl = HttpUrl("https://qyapi.weixin.qq.com/cgi-bin/")
    wecom_sso_url: HttpUrl = HttpUrl("https://login.work.weixin.qq.com/wwlogin/sso/login")
    wecom_oauth_url: HttpUrl = HttpUrl("https://open.weixin.qq.com/connect/oauth2/authorize")

    # X4：员工自己的假期、请假、审批、岗位职责（前台专用 Token，和 Dify 的不同）
    x4_url: HttpUrl
    x4_portal_token: SecretStr = Field(min_length=32)
    # X3：按岗位检索知识
    x3_url: HttpUrl
    x3_token: SecretStr = Field(min_length=32)
    # Dify 应用 API（服务端调用，浏览器拿不到 Key）；不填就不显示对应的对话
    dify_api_base: HttpUrl
    dify_hr_app_key: SecretStr | None = None
    dify_policy_app_key: SecretStr | None = None

    http_timeout_seconds: float = 10.0
    chat_timeout_seconds: float = 120.0

    @field_validator("public_url")
    @classmethod
    def _https(cls, v: HttpUrl) -> HttpUrl:
        # 本机调试允许 http://localhost、http://*.localhost，其余必须 https（Cookie 带 Secure）
        host = v.host or ""
        local = host in ("localhost", "127.0.0.1") or host.endswith(".localhost")
        if v.scheme != "https" and not local:
            raise ValueError("EB_PORTAL_PUBLIC_URL 必须是 https")
        if v.path not in (None, "", "/") or v.query or v.fragment:
            raise ValueError("EB_PORTAL_PUBLIC_URL 只写协议和域名")
        return v

    @property
    def origin(self) -> str:
        return str(self.public_url).rstrip("/")
