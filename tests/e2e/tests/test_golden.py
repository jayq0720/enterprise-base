"""离线检查标准问题集和评测脚本（CI 里不用测试环境也会跑）。"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from e2e import evaluate

DIFY = Path(__file__).resolve().parents[3] / "config" / "dify"
SUITES = sorted(evaluate.GOLDEN.glob("*.yaml"))
# 会写业务数据的分支和说法：问题集里只能用不存在的单号，或者不出现
WRITES = ("确认请假",)


@pytest.mark.parametrize("path", SUITES, ids=lambda p: p.stem)
def test_suite_matches_dsl(path: Path) -> None:
    suite = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc = yaml.safe_load((DIFY / f"{suite['app']}.yml").read_text(encoding="utf-8"))
    nodes = {n["id"] for n in doc["workflow"]["graph"]["nodes"]}
    routes = suite.get("routes") or {}
    assert set(routes.values()) <= nodes, "routes 里的节点在 DSL 里不存在"
    assert suite.get("user") in ("employee", "sales")
    ids = [c["id"] for c in suite["cases"]]
    assert len(ids) == len(set(ids))
    for c in suite["cases"]:
        assert set(c) <= {"id", "q", "route", "any", "all", "none"}, c["id"]
        assert c.get("route") in (None, *routes), c["id"]
        if c["q"].strip() in WRITES:
            assert "已提交请假申请" in (c.get("none") or []), c["id"]
    # 每个分支至少一道题
    assert set(routes) <= {c.get("route") for c in suite["cases"]}


def _sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events).encode()


def test_run_suite_scores_routes_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        q = json.loads(request.content)["query"]
        assert request.headers["authorization"] == "Bearer app-key"
        ran = ["start", "intent__kw", "t_balance"] if "余额" in q else ["start", "a_other"]
        events: list[dict[str, Any]] = [
            *({"event": "node_started", "data": {"node_id": n}} for n in ran),
            {
                "event": "message",
                "answer": "年假：剩余 3 天" if "余额" in q else "电话 13800138000",
            },
            {
                "event": "message_end",
                "metadata": {
                    "usage": {"total_tokens": 42, "total_price": "0.01", "currency": "RMB"}
                },
            },
        ]
        return httpx.Response(200, content=_sse(events))

    real = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    s = evaluate.EvalSettings(
        dify_api_url="http://dify.test/v1", hr_app_key="app-key", employee="u1"
    )
    suite = {
        "app": "hr-assistant",
        "user": "employee",
        "routes": {"balance": "t_balance"},
        "cases": [
            {"id": "a", "q": "查余额", "route": "balance", "any": ["剩余"]},
            {"id": "b", "q": "你好", "route": "balance"},
        ],
    }
    a, b = evaluate.run_suite(s, suite)
    assert a.passed and a.tokens == 42 and a.price == 0.01 and a.fallback == ["intent__kw"]
    assert not b.passed
    assert any("分支" in p for p in b.problems) and any("敏感" in p for p in b.problems)
    text = evaluate.report([a, b])
    assert "| hr-assistant | 1/2 |" in text and "intent__kw" in text


def test_skips_app_without_key() -> None:
    s = evaluate.EvalSettings(dify_api_url="http://dify.test/v1", employee="u1")
    assert evaluate.run_suite(s, {"app": "policy-qa", "cases": [{"id": "x", "q": "?"}]}) == []
