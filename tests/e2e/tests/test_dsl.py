"""离线检查三个 Dify 应用 DSL（CI 里不用测试环境也会跑）：模型只用 models.json 里批准的国产厂商，
每个模型节点都有备用模型和兜底，图里的边都连得上。改了 DSL 或 models.json 后先运行
`uv run --with ruamel.yaml python config/dify/model_fallback.py` 重新生成。"""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

DIFY = Path(__file__).resolve().parents[3] / "config" / "dify"
APPS = ("hr-assistant", "policy-qa", "inquiry-assistant")
MODEL_TYPES = ("llm", "question-classifier", "parameter-extractor")
CFG: dict[str, Any] = json.loads((DIFY / "models.json").read_text(encoding="utf-8"))


def graph(app: str) -> dict[str, Any]:
    doc = yaml.safe_load((DIFY / f"{app}.yml").read_text(encoding="utf-8"))
    g: dict[str, Any] = doc["workflow"]["graph"]
    return g


@pytest.mark.parametrize("app", APPS)
def test_models_are_approved_domestic_providers(app: str) -> None:
    for n in graph(app)["nodes"]:
        model = n["data"].get("model")
        if model:
            assert model["provider"] in CFG["providers"], (app, n["id"], model["provider"])


def test_chain_providers_approved() -> None:
    assert len(CFG["chain"]) >= 2
    for m in CFG["chain"]:
        assert m["provider"] in CFG["providers"]


@pytest.mark.parametrize("app", APPS)
def test_every_model_node_falls_back(app: str) -> None:
    g = graph(app)
    nodes = {n["id"]: n for n in g["nodes"]}
    fail = {e["source"]: e["target"] for e in g["edges"] if e["sourceHandle"] == "fail-branch"}
    primaries = [n for n in g["nodes"] if n["data"]["type"] in MODEL_TYPES and "__" not in n["id"]]
    assert primaries
    for p in primaries:
        chain, cur = [p["id"]], p["id"]
        while cur in fail:
            cur = fail[cur]
            chain.append(cur)
        models = [nodes[c]["data"].get("model") for c in chain[:-1]]
        assert [(m["provider"], m["name"]) for m in models] == [
            (m["provider"], m["name"]) for m in CFG["chain"]
        ], (app, p["id"], "重新运行 model_fallback.py")
        for c in chain[:-1]:
            assert nodes[c]["data"]["error_strategy"] == "fail-branch"
        # 最后一级不是模型：意图分类走关键词规则，其他回复固定的话
        last = nodes[chain[-1]]["data"]["type"]
        want = "code" if p["data"]["type"] == "question-classifier" else "answer"
        assert last == want, (app, p["id"], last)


@pytest.mark.parametrize("app", APPS)
def test_keyword_rules_use_real_classes(app: str) -> None:
    g = graph(app)
    for n in g["nodes"]:
        if n["data"]["type"] == "question-classifier" and "__" not in n["id"]:
            spec = CFG["fallbacks"][app][n["id"]]
            classes = {c["id"] for c in n["data"]["classes"]}
            assert {r[0] for r in spec["rules"]} | {spec["default"]} <= classes


@pytest.mark.parametrize("app", APPS)
def test_edges_connect_existing_nodes(app: str) -> None:
    g = graph(app)
    ids = {n["id"] for n in g["nodes"]}
    for e in g["edges"]:
        assert e["source"] in ids and e["target"] in ids, e["id"]
    # 下游不再直接读会失败的模型节点，统一读汇合节点
    text = (DIFY / f"{app}.yml").read_text(encoding="utf-8")
    for n in g["nodes"]:
        if n["data"]["type"] in ("llm", "parameter-extractor") and "__" not in n["id"]:
            assert f"{{{{#{n['id']}." not in text, (app, n["id"])


@pytest.mark.parametrize("app", ("hr-assistant", "inquiry-assistant"))
def test_session_failure_stops_before_model(app: str) -> None:
    g = graph(app)
    out = {(e["source"], e["sourceHandle"]): e["target"] for e in g["edges"]}
    assert out[("token", "source")] == "limit"
    assert out[("limit", "false")] == "intent"
    assert out[("limit", "true")] == "a_limited"
