"""接口与内部数据模型。"""

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """Dify Chatflow 的 HTTP 请求节点发来的检索请求。

    user_id 必须绑定 `sys.user_id`（企业微信 userid），不能让模型填写，见 ADR 0001。
    """

    user_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1)


class Citation(BaseModel):
    title: str
    content: str
    score: float
    document_id: str
    space: str


class SearchResponse(BaseModel):
    records: list[Citation]
    # 检索不到时给 LLM 的说明，例如“未找到你的岗位信息”
    notice: str | None = None


class KnowledgeSpace(BaseModel):
    """一个知识空间对应一个 Dify 知识库。"""

    name: str
    dataset_id: str
    # 可见范围标记，例如 ["all"] 或 ["dept:人事部 - TC", "desig:HRBP"]
    scope: list[str]


class EmployeeProfile(BaseModel):
    employee: str
    department: str | None
    designation: str | None
    spaces: list[KnowledgeSpace]


class RawRecord(BaseModel):
    """Dify 知识库检索返回的一条分段。"""

    title: str
    content: str
    score: float
    document_id: str
    metadata: dict[str, object]
