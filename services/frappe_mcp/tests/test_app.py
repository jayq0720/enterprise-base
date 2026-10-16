import json
from collections.abc import Mapping

from starlette.testclient import TestClient

from frappe_mcp import session
from frappe_mcp.app import create_app
from frappe_mcp.config import Settings

from .conftest import SECRET, TOKEN, FakeHR

AUTH = {"Authorization": f"Bearer {TOKEN}"}
MCP_HEADERS = {**AUTH, "Accept": "application/json, text/event-stream"}


def rpc(c: TestClient, method: str, params: Mapping[str, object]) -> dict[str, object]:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    resp = c.post("/mcp/", json=body, headers=MCP_HEADERS)
    assert resp.status_code == 200, resp.text
    data: dict[str, object] = resp.json()
    return data


def test_requires_service_token(settings: Settings) -> None:
    with TestClient(create_app(settings, FakeHR())) as c:
        assert c.get("/healthz").status_code == 200
        assert c.post("/v1/session", json={"user_id": "zhangsan"}).status_code == 401
        assert c.post("/mcp/", json={}).status_code == 401


def test_session_endpoint_issues_token(settings: Settings) -> None:
    with TestClient(create_app(settings, FakeHR())) as c:
        resp = c.post("/v1/session", json={"user_id": "zhangsan"}, headers=AUTH)
        token = resp.json()["session_token"]
        assert session.verify(SECRET.encode(), token) == "zhangsan"
        assert c.post("/v1/session", json={}, headers=AUTH).status_code == 422


def test_mcp_lists_and_calls_tools(settings: Settings) -> None:
    with TestClient(create_app(settings, FakeHR())) as c:
        init = {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        }
        rpc(c, "initialize", init)
        tools = rpc(c, "tools/list", {})["result"]
        names = sorted(t["name"] for t in tools["tools"])  # type: ignore[index]
        assert names == ["leave_balance", "my_leave_applications", "pending_my_approval"]
        token = session.issue(SECRET.encode(), "zhangsan", 900)
        call = {"name": "leave_balance", "arguments": {"session_token": token}}
        result = rpc(c, "tools/call", call)["result"]
        text = result["content"][0]["text"]  # type: ignore[index]
        assert json.loads(text)["employee_name"] == "张三"
