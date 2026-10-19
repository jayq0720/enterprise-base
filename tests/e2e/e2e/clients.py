"""对各个服务的最小 HTTP 客户端。只走公开接口，和员工、Dify 工作流的调用方式一致。"""

import json
import time
from collections.abc import Callable
from typing import Any

import httpx

from e2e.settings import E2ESettings


def _client(base: str, host: str | None, headers: dict[str, str], timeout: float) -> httpx.Client:
    if host:
        headers = {**headers, "Host": host}
    return httpx.Client(base_url=base.rstrip("/"), headers=headers, timeout=timeout)


class Frappe:
    """测试环境管理员身份，只用来准备和清理合成数据。"""

    def __init__(self, s: E2ESettings, key: str = "", secret: str = "") -> None:
        self._s = s
        key = key or s.frappe_admin_key.get_secret_value()
        secret = secret or s.frappe_admin_secret.get_secret_value()
        self.http = _client(
            str(s.frappe_url),
            s.frappe_host,
            {"Authorization": f"token {key}:{secret}"},
            s.http_timeout_seconds,
        )

    def as_user(self, user: str) -> "Frappe":
        """给合成用户生成 API Key，以本人身份办工作流动作（例如清理时撤销已批的请假）。"""
        r = self.http.post(
            "/api/method/frappe.core.doctype.user.user.generate_keys", json={"user": user}
        )
        _check(r)
        secret = str(r.json()["message"]["api_secret"])
        return Frappe(self._s, str(self.get("User", user)["api_key"]), secret)

    def insert(self, doctype: str, doc: dict[str, Any]) -> dict[str, Any]:
        r = self.http.post(f"/api/resource/{doctype}", json=doc)
        _check(r)
        data: dict[str, Any] = r.json()["data"]
        return data

    def get(self, doctype: str, name: str) -> dict[str, Any]:
        r = self.http.get(f"/api/resource/{doctype}/{name}")
        _check(r)
        data: dict[str, Any] = r.json()["data"]
        return data

    def update(self, doctype: str, name: str, values: dict[str, Any]) -> dict[str, Any]:
        r = self.http.put(f"/api/resource/{doctype}/{name}", json=values)
        _check(r)
        data: dict[str, Any] = r.json()["data"]
        return data

    def delete(self, doctype: str, name: str) -> None:
        r = self.http.delete(f"/api/resource/{doctype}/{name}")
        if r.status_code != 404:
            _check(r)

    def names(self, doctype: str, filters: list[list[Any]], limit: int = 20) -> list[str]:
        r = self.http.get(
            f"/api/resource/{doctype}",
            params={
                "fields": '["name"]',
                "filters": json.dumps(filters, ensure_ascii=False),
                "limit_page_length": str(limit),
            },
        )
        _check(r)
        return [str(row["name"]) for row in r.json()["data"]]

    def apply_workflow(self, doctype: str, name: str, action: str) -> dict[str, Any]:
        doc = self.get(doctype, name)
        r = self.http.post(
            "/api/method/frappe.model.workflow.apply_workflow",
            json={"doc": json.dumps(doc, ensure_ascii=False), "action": action},
        )
        _check(r)
        data: dict[str, Any] = r.json()["message"]
        return data


class DifyDatasets:
    def __init__(self, s: E2ESettings) -> None:
        key = s.dify_dataset_key.get_secret_value()
        self.http = _client(
            str(s.dify_api_url), s.dify_host, {"Authorization": f"Bearer {key}"}, 30
        )

    def create(self, name: str, indexing_technique: str) -> str:
        r = self.http.post(
            "/datasets",
            json={"name": name, "indexing_technique": indexing_technique, "permission": "only_me"},
        )
        _check(r)
        return str(r.json()["id"])

    def delete(self, dataset_id: str) -> None:
        r = self.http.delete(f"/datasets/{dataset_id}")
        if r.status_code != 404:
            _check(r)

    def documents(self, dataset_id: str) -> list[dict[str, Any]]:
        r = self.http.get(f"/datasets/{dataset_id}/documents", params={"limit": 100})
        _check(r)
        docs: list[dict[str, Any]] = r.json().get("data", [])
        return docs


class X3:
    def __init__(self, s: E2ESettings) -> None:
        token = s.x3_token.get_secret_value()
        self.http = _client(str(s.x3_url), None, {"Authorization": f"Bearer {token}"}, 30)

    def search(self, user_id: str, query: str) -> dict[str, Any]:
        r = self.http.post("/v1/search", json={"user_id": user_id, "query": query, "top_k": 5})
        _check(r)
        data: dict[str, Any] = r.json()
        return data


class X4:
    """按 Dify 工作流的方式调 X4：先拿会话令牌，再走 MCP 调工具。"""

    def __init__(self, s: E2ESettings) -> None:
        token = s.x4_token.get_secret_value()
        self.http = _client(
            str(s.x4_url),
            None,
            {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
            30,
        )

    def session(self, user_id: str) -> str:
        r = self.http.post("/v1/session", json={"user_id": user_id})
        _check(r)
        return str(r.json()["session_token"])

    def call(self, user_id: str, tool: str, **arguments: str) -> dict[str, Any]:
        init = {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "eb-e2e", "version": "0"},
        }
        self._rpc("initialize", init)
        args = {"session_token": self.session(user_id), **arguments}
        result = self._rpc("tools/call", {"name": tool, "arguments": args})
        out: dict[str, Any] = json.loads(result["content"][0]["text"])
        return out

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        r = self.http.post(
            "/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        )
        _check(r)
        result: dict[str, Any] = r.json()["result"]
        return result


class HRAssistant:
    """模拟 dify-on-wechat：user 填企业微信 userid，同一个员工连续的消息沿用同一个会话。"""

    def __init__(self, s: E2ESettings, app_key: str) -> None:
        self.http = _client(
            str(s.dify_api_url), s.dify_host, {"Authorization": f"Bearer {app_key}"}, 120
        )

    def say(self, user_id: str, query: str, conversation_id: str = "") -> tuple[str, str]:
        r = self.http.post(
            "/chat-messages",
            json={
                "inputs": {},
                "query": query,
                "user": user_id,
                "response_mode": "blocking",
                "conversation_id": conversation_id,
            },
        )
        _check(r)
        data = r.json()
        return str(data.get("answer") or ""), str(data.get("conversation_id") or "")

    def messages(self, user_id: str) -> list[dict[str, Any]]:
        """这个员工在 HR 助手里的全部消息（各会话按时间先后），用来核对企业微信入口。"""
        r = self.http.get("/conversations", params={"user": user_id, "limit": 20})
        _check(r)
        out: list[dict[str, Any]] = []
        for conv in r.json().get("data", []):
            m = self.http.get(
                "/messages", params={"user": user_id, "conversation_id": conv["id"], "limit": 50}
            )
            _check(m)
            out += m.json().get("data", [])
        return sorted(out, key=lambda m: m.get("created_at") or 0)


def wait_for[T](check: Callable[[], T | None], timeout: float, interval: float = 3) -> T:
    """反复执行 check，直到返回非 None；超时报错。用来等 Webhook → X5 → Dify 的异步同步。"""
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value is not None:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"等待 {timeout:.0f} 秒仍未满足条件")
        time.sleep(interval)


def _check(r: httpx.Response) -> None:
    if r.is_error:
        # 只带状态码和前 300 字，避免把整页报错（可能含数据）打进测试日志
        raise AssertionError(
            f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:300]}"
        )
