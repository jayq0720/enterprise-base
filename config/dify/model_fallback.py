"""给三个 Dify 应用的模型节点加备用模型和兜底，按 models.json 生成。

用法（仓库根目录）：uv run --with ruamel.yaml python config/dify/model_fallback.py

每个模型节点（意图分类、参数提取、LLM）：
- 失败先重试一次，再依次换 chain 里的下一家模型（节点 id 加 __m2、__m3）；
- 全部失败时：意图分类改用关键词规则（__kw 代码节点 + __kwif 条件分支），
  其他节点回复一句固定的话（__down），对话不报错；
- 参数提取和 LLM 的输出经 __agg（变量聚合）汇合，下游统一读 __agg。

可以重复运行：先去掉上次生成的节点、恢复下游引用，再按 models.json 重新生成。
"""

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

HERE = Path(__file__).resolve().parent
APPS = ("hr-assistant", "policy-qa", "inquiry-assistant")
MODEL_TYPES = ("llm", "question-classifier", "parameter-extractor")
GENERATED = "__"  # 生成的节点 id 都带这个分隔符，手写的节点 id 不要用


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=2, offset=0)
    # 和 Dify 导出一样把空值写成 null
    y.representer.add_representer(
        type(None), lambda r, _: r.represent_scalar("tag:yaml.org,2002:null", "null")
    )
    return y


def _edge(src: dict[str, Any], handle: str, tgt: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"{src['id']}-{handle}-{tgt['id']}",
        "type": "custom",
        "source": src["id"],
        "sourceHandle": handle,
        "target": tgt["id"],
        "targetHandle": "target",
        "data": {"sourceType": src["data"]["type"], "targetType": tgt["data"]["type"]},
    }


def _node(node_id: str, x: float, y: float, data: dict[str, Any]) -> dict[str, Any]:
    return {"id": node_id, "type": "custom", "position": {"x": x, "y": y}, "data": data}


def _ref_pattern(node_id: str) -> re.Pattern[str]:
    return re.compile(r"\{\{#" + re.escape(node_id) + r"\.([A-Za-z0-9_]+)#\}\}")


def _rewrite(obj: Any, fn: Any) -> Any:
    """对 DSL 里所有字符串和变量选择器（[节点, 字段]）做替换。"""
    if isinstance(obj, str):
        return fn(obj, None)
    if isinstance(obj, list):
        if len(obj) in (2, 3) and all(isinstance(s, str) for s in obj):
            new = fn(None, list(obj))
            if new is not None:
                obj[:] = new
                return obj
        for i, v in enumerate(obj):
            obj[i] = _rewrite(v, fn)
        return obj
    if isinstance(obj, dict):
        for k in list(obj):
            obj[k] = _rewrite(obj[k], fn)
    return obj


def strip(graph: dict[str, Any]) -> None:
    """去掉上次生成的节点和边，下游引用改回原节点。"""
    nodes = {n["id"]: n for n in graph["nodes"]}
    restored = [
        _edge(nodes[e["source"].removesuffix("__agg")], "source", nodes[e["target"]])
        for e in graph["edges"]
        if e["source"].endswith("__agg") and GENERATED not in e["target"]
    ]
    graph["nodes"] = [n for n in graph["nodes"] if GENERATED not in n["id"]]
    graph["edges"] = [
        e for e in graph["edges"] if GENERATED not in e["source"] and GENERATED not in e["target"]
    ] + restored
    agg = re.compile(r"\{\{#([A-Za-z0-9_]+)__agg\.([A-Za-z0-9_]+)\.output#\}\}")

    def back(text: str | None, sel: list[str] | None) -> Any:
        if text is not None:
            return agg.sub(r"{{#\1.\2#}}", text)
        if sel and sel[0].endswith("__agg") and len(sel) == 3 and sel[2] == "output":
            return [sel[0].removesuffix("__agg"), sel[1]]
        return None

    _rewrite(graph, back)
    for n in graph["nodes"]:
        if n["data"]["type"] not in MODEL_TYPES:
            continue
        for key in ("error_strategy", "retry_config", "default_value"):
            n["data"].pop(key, None)


def _outputs(node: dict[str, Any]) -> list[tuple[str, str]]:
    d = node["data"]
    if d["type"] == "llm":
        return [("text", "string")]
    if d["type"] == "parameter-extractor":
        return [(p["name"], p["type"]) for p in d["parameters"]]
    return []


def _keyword_nodes(
    graph: dict[str, Any], primary: dict[str, Any], spec: dict[str, Any], x: float, y: float
) -> dict[str, Any]:
    """意图分类全失败时的关键词规则（正则）：代码节点算出类别，条件分支接到原来的各个去向。"""
    pid = primary["id"]
    rules = json.dumps(spec["rules"], ensure_ascii=False)
    code = (
        "import json\nimport re\n\n"
        f"RULES = json.loads({rules!r})\n\n\n"
        "def main(query: str) -> dict:\n"
        '    """模型都不可用时按关键词判断意图（按顺序，先命中先用）。"""\n'
        '    q = query or ""\n'
        "    for intent, words in RULES:\n"
        "        if any(re.search(w, q) for w in words):\n"
        '            return {"intent": intent}\n'
        f'    return {{"intent": {spec["default"]!r}}}\n'
    )
    kw = _node(
        f"{pid}__kw",
        x,
        y,
        {
            "type": "code",
            "title": f"{primary['data']['title']}（关键词兜底）",
            "desc": "模型都不可用时按关键词判断，由 model_fallback.py 生成",
            "code_language": "python3",
            "code": code,
            "variables": [{"variable": "query", "value_selector": ["sys", "query"]}],
            "outputs": {"intent": {"type": "string", "children": None}},
        },
    )
    classes = [c["id"] for c in primary["data"]["classes"]]
    cases = [
        {
            "case_id": c,
            "id": c,
            "logical_operator": "and",
            "conditions": [
                {
                    "id": f"kw_{c}",
                    "variable_selector": [kw["id"], "intent"],
                    "comparison_operator": "is",
                    "value": c,
                    "varType": "string",
                }
            ],
        }
        for c in classes
        if c != spec["default"]
    ]
    kwif = _node(
        f"{pid}__kwif",
        x + 300,
        y,
        {"type": "if-else", "title": "按关键词分流", "cases": cases},
    )
    graph["nodes"] += [kw, kwif]
    graph["edges"].append(_edge(kw, "source", kwif))
    nodes = {n["id"]: n for n in graph["nodes"]}
    for e in [e for e in graph["edges"] if e["source"] == pid]:
        handle = "false" if e["sourceHandle"] == spec["default"] else e["sourceHandle"]
        graph["edges"].append(_edge(kwif, handle, nodes[e["target"]]))
    return kw


def apply(graph: dict[str, Any], cfg: dict[str, Any], specs: dict[str, Any]) -> list[str]:
    chain, retry = cfg["chain"], cfg["retry"]
    done = []
    primaries = [n for n in graph["nodes"] if n["data"]["type"] in MODEL_TYPES]
    for primary in primaries:
        pid, data = primary["id"], primary["data"]
        if pid not in specs:
            raise SystemExit(f"models.json 的 fallbacks 里没有节点 {pid} 的兜底配置")
        spec = specs[pid]
        x, y = primary["position"]["x"], primary["position"]["y"]
        out_edges = [e for e in graph["edges"] if e["source"] == pid]
        nodes = {n["id"]: n for n in graph["nodes"]}
        is_classifier = data["type"] == "question-classifier"

        tiers = [primary]
        for i, model in enumerate(chain[1:], start=2):
            node = _node(f"{pid}__m{i}", x, y + 220 * (i - 1), copy.deepcopy(dict(data)))
            node["data"]["title"] = f"{data['title']}（备用 {i - 1}）"
            node["data"]["desc"] = f"上一个模型失败时用 {model['name']}，由 model_fallback.py 生成"
            node["data"]["model"] = {
                **copy.deepcopy(dict(data["model"])),
                "provider": model["provider"],
                "name": model["name"],
            }
            tiers.append(node)
        data["model"]["provider"], data["model"]["name"] = chain[0]["provider"], chain[0]["name"]
        for t in tiers:
            t["data"]["error_strategy"] = "fail-branch"
            t["data"]["retry_config"] = {"retry_enabled": True, **retry}
        graph["nodes"] += tiers[1:]

        bottom = y + 220 * len(chain)
        if is_classifier:
            last = _keyword_nodes(graph, primary, spec, x, bottom)
            for t in tiers[1:]:
                for e in out_edges:
                    graph["edges"].append(_edge(t, e["sourceHandle"], nodes[e["target"]]))
        else:
            last = _node(
                f"{pid}__down",
                x,
                bottom,
                {"type": "answer", "title": "AI 不可用时回复", "answer": spec["answer"], "variables": []},
            )
            graph["nodes"].append(last)
            outs = _outputs(primary)
            agg = _node(
                f"{pid}__agg",
                x + 300,
                y,
                {
                    "type": "variable-aggregator",
                    "title": f"{data['title']}（汇合）",
                    "output_type": outs[0][1],
                    "variables": [[t["id"], outs[0][0]] for t in tiers],
                    "advanced_settings": {
                        "group_enabled": True,
                        "groups": [
                            {
                                "groupId": f"{pid}_{name}",
                                "group_name": name,
                                "output_type": typ,
                                "variables": [[t["id"], name] for t in tiers],
                            }
                            for name, typ in outs
                        ],
                    },
                },
            )
            graph["nodes"].append(agg)
            graph["edges"] = [e for e in graph["edges"] if e["source"] != pid]
            for t in tiers:
                graph["edges"].append(_edge(t, "source", agg))
            for e in out_edges:
                graph["edges"].append(_edge(agg, "source", nodes[e["target"]]))
            pat = _ref_pattern(pid)
            names = {name for name, _ in outs}

            def fwd(text: str | None, sel: list[str] | None, pid: str = pid) -> Any:
                if text is not None:
                    return pat.sub(lambda m: f"{{{{#{pid}__agg.{m.group(1)}.output#}}}}", text)
                if sel and len(sel) == 2 and sel[0] == pid and sel[1] in names:
                    return [f"{pid}__agg", sel[1], "output"]
                return None

            # 只改原节点下游，不动备用节点自己（它们是原节点的副本，不会引用原节点）
            skip = {t["id"] for t in tiers} | {agg["id"]}
            for n in graph["nodes"]:
                if n["id"] not in skip:
                    _rewrite(n["data"], fwd)
        for upper, lower in zip(tiers, [*tiers[1:], last], strict=True):
            graph["edges"].append(_edge(upper, "fail-branch", lower))
        done.append(pid)
    return done


def main() -> None:
    cfg = json.loads((HERE / "models.json").read_text(encoding="utf-8"))
    y = _yaml()
    for app in APPS:
        path = HERE / f"{app}.yml"
        doc = y.load(path.read_text(encoding="utf-8"))
        graph = doc["workflow"]["graph"]
        strip(graph)
        done = apply(graph, cfg, cfg["fallbacks"].get(app, {}))
        with path.open("w", encoding="utf-8") as f:
            y.dump(doc, f)
        print(f"{app}: {', '.join(done)}", file=sys.stderr)


if __name__ == "__main__":
    main()
