"""到 Frappe 查提问人的员工、部门、岗位和可见知识空间。"""

import json
from typing import Protocol

import httpx

from kb_gateway.config import Settings
from kb_gateway.models import EmployeeProfile, KnowledgeSpace
from kb_gateway.scope import viewer_tokens


class Directory(Protocol):
    async def get_profile(self, user_id: str) -> EmployeeProfile | None:
        """查不到在职员工时返回 None。"""
        ...


class FrappeDirectory:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._s = settings
        self._client = client
        key = settings.frappe_api_key.get_secret_value()
        secret = settings.frappe_api_secret.get_secret_value()
        self._headers = {"Authorization": f"token {key}:{secret}"}
        self._base = str(settings.frappe_url).rstrip("/")

    async def get_profile(self, user_id: str) -> EmployeeProfile | None:
        resp = await self._client.get(
            f"{self._base}/api/resource/Employee",
            headers=self._headers,
            params={
                "filters": _json(
                    [[self._s.frappe_wecom_field, "=", user_id], ["status", "=", "Active"]]
                ),
                # 只取需要的字段，不取手机号、身份证等
                "fields": _json(["name", "department", "designation"]),
                "limit_page_length": "2",
            },
        )
        resp.raise_for_status()
        rows = resp.json().get("data", [])
        if len(rows) != 1:
            # 查不到或一个 userid 对应多个员工：都按无权处理
            return None
        row = rows[0]
        department = row.get("department") or None
        designation = row.get("designation") or None
        tokens = set(viewer_tokens(department, designation))
        spaces = [sp for sp in await self._spaces() if tokens.intersection(sp.scope)]
        return EmployeeProfile(
            employee=row["name"], department=department, designation=designation, spaces=spaces
        )

    async def _spaces(self) -> list[KnowledgeSpace]:
        resp = await self._client.get(
            f"{self._base}/api/method/{self._s.frappe_spaces_method}", headers=self._headers
        )
        resp.raise_for_status()
        items = resp.json().get("message", [])
        return [
            KnowledgeSpace(name=i["space"], dataset_id=i["dify_dataset_id"], scope=i["scope"])
            for i in items
            if i.get("dify_dataset_id") and isinstance(i.get("scope"), list)
        ]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)
