"""数据结构。"""

from pydantic import BaseModel


class SpaceBinding(BaseModel):
    """X1 的一条知识空间绑定：哪个 Wiki 空间同步到哪个 Dify 知识库、谁能看。"""

    wiki_space: str
    dify_dataset_id: str
    # 可见范围标记，例如 ["all"] 或 ["dept:人事部 - TC", "desig:HRBP"]
    scope: list[str]


class WikiPage(BaseModel):
    name: str
    title: str
    content: str
    published: bool
    # 所属 Wiki 空间；页面不在任何空间下时为 None
    wiki_space: str | None


class DifyDoc(BaseModel):
    id: str
    metadata: dict[str, str]


class WebhookEvent(BaseModel):
    """Frappe Webhook 推送的内容只用来知道是哪一页，页面内容一律回 Frappe 重新读。"""

    name: str
    doctype: str = "Wiki Document"
