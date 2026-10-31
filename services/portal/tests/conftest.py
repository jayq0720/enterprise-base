from typing import Any

import pytest

from portal.backends import TooMany, Unavailable
from portal.config import Settings

ORIGIN = "https://portal.test"
SECRET = "s" * 40
HR_KEY = "app-hr-key-synthetic"
FEATURES = ("leave_query", "leave_apply", "approval", "duties")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        public_url=ORIGIN,
        session_secret=SECRET,
        wecom_corp_id="synthetic-corp",
        wecom_agent_id="1000002",
        wecom_app_secret="synthetic-app-secret",
        x4_url="http://frappe-mcp:8080",
        x4_portal_token="p" * 40,
        x3_url="http://kb-gateway:8080",
        x3_token="k" * 40,
        dify_api_base="http://dify-api:5001/v1",
        dify_hr_app_key=HR_KEY,
    )


class FakeBackends:
    """合成数据：code-<userid> 换出 userid；zhangsan、lisi 是在职员工。"""

    def __init__(self) -> None:
        self.employees: dict[str, dict[str, Any]] = {
            "zhangsan": {
                "ok": True,
                "employee_name": "张三",
                "designation": "出纳",
                "features": {
                    "leave_query": True,
                    "leave_apply": True,
                    "approval": False,
                    "duties": True,
                },
            },
            "lisi": {
                "ok": True,
                "employee_name": "李四",
                "designation": "主管",
                "features": dict.fromkeys(
                    ("leave_query", "leave_apply", "approval", "duties"), True
                ),
            },
        }
        self.x4_calls: list[tuple[str, str, dict[str, str]]] = []
        self.chats: list[tuple[str, str, str, str]] = []
        self.searches: list[tuple[str, str]] = []
        self.intakes: list[tuple[str, bytes]] = []
        self.down = False
        self.limited = False
        self.missing_conversation = False

    async def wecom_userid(self, code: str) -> str | None:
        return code.removeprefix("code-") if code.startswith("code-") else None

    async def x4(self, name: str, user_id: str, **args: str) -> dict[str, Any]:
        if self.down:
            raise Unavailable
        if self.limited and name != "profile":
            raise TooMany
        self.x4_calls.append((name, user_id, args))
        if name == "profile":
            return self.employees.get(user_id, {"ok": False, "error": "未找到"})
        return {"tool": name, "employee_name": self.employees[user_id]["employee_name"]}

    async def intake(self, authorization: str, body: bytes) -> tuple[int, dict[str, Any]]:
        if self.down:
            raise Unavailable
        self.intakes.append((authorization, body))
        if authorization != "Bearer " + "i" * 40:
            return 401, {"error": "unauthorized"}
        return 200, {"ok": True, "id": "CRM-LEAD-2", "duplicate": False, "grade": "B"}

    async def search(self, user_id: str, query: str) -> dict[str, Any]:
        self.searches.append((user_id, query))
        return {
            "records": [
                {
                    "title": "年假规定",
                    "content": "满一年 5 天",
                    "score": 0.9,
                    "document_id": "doc-1",
                    "space": "全员制度",
                }
            ],
            "notice": None,
        }

    async def chat(
        self, key: str, user_id: str, query: str, conversation_id: str
    ) -> dict[str, Any]:
        self.chats.append((key, user_id, query, conversation_id))
        if self.missing_conversation and conversation_id:
            return {"_conversation_missing": True}
        return {
            "answer": f"{user_id} 你好",
            "conversation_id": "0b8d6c1e-1111-4222-8333-444455556666",
            "metadata": {"usage": {}},
        }
