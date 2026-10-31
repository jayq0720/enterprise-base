"""标准问题集评测（make eval）：把 golden/*.yaml 里的问题发给已部署的 Dify 应用，逐题检查
走没走对分支、回答里该有的词有没有、不该出现的有没有，再统计耗时、Token、费用和兜底次数。

只读：问题集里不放“确认请假”，记跟进用不存在的询盘号，不会写业务数据。
用法：cd tests/e2e && uv run python -m e2e.evaluate [--only 应用] [额外的问题集.yaml ...]
"""

import argparse
import datetime
import json
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

GOLDEN = Path(__file__).resolve().parents[1] / "golden"
RESULTS = Path(__file__).resolve().parents[1] / "eval-results"
# 任何回答里都不该出现：手机号、身份证号、银行卡号、令牌、程序报错
ALWAYS_FORBIDDEN = [
    r"(?<!\d)1[3-9]\d{9}(?!\d)",
    r"(?<!\d)\d{17}[\dXx](?!\d)",
    r"(?<!\d)\d{16,19}(?!\d)",
    r"session_token|draft_token|Traceback|Bearer ",
]
# 生成的兜底节点（model_fallback.py）：备用模型、关键词规则、固定回复
FALLBACK = re.compile(r"__(m\d+|kw|down)$")


class EvalSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EB_EVAL_", env_file=".env", extra="ignore")

    dify_api_url: HttpUrl
    dify_host: str | None = None
    hr_app_key: SecretStr | None = None
    policy_app_key: SecretStr | None = None
    inquiry_app_key: SecretStr | None = None
    # 用来提问的员工（企业微信 userid）：employee 是普通员工，sales 是开了询盘功能的业务员
    employee: str
    sales: str | None = None
    min_pass_rate: float = 0.9
    timeout_seconds: float = 120


@dataclass
class Result:
    app: str
    id: str
    question: str
    passed: bool
    problems: list[str]
    answer: str
    seconds: float
    tokens: int = 0
    price: float = 0.0
    currency: str = ""
    fallback: list[str] = field(default_factory=list)


def chat(http: httpx.Client, user: str, query: str) -> tuple[str, list[str], dict[str, Any], float]:
    """流式调一次对话：返回回答、执行过的节点 id、用量、秒数。"""
    started = time.monotonic()
    answer: list[str] = []
    nodes: list[str] = []
    usage: dict[str, Any] = {}
    body = {"inputs": {}, "query": query, "user": user, "response_mode": "streaming"}
    with http.stream("POST", "/chat-messages", json=body) as r:
        if r.status_code != 200:
            r.read()
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:])
            kind = event.get("event")
            if kind == "node_started":
                nodes.append(str(event["data"]["node_id"]))
            elif kind in ("message", "agent_message"):
                answer.append(str(event.get("answer") or ""))
            elif (
                kind == "workflow_finished" and (event.get("data") or {}).get("status") == "failed"
            ):
                raise RuntimeError("工作流出错：" + str(event["data"].get("error"))[:200])
            elif kind == "workflow_finished" and not answer:
                # 没收到流式片段时（例如回复节点前有失败的节点）用工作流最终输出
                answer.append(
                    str(((event.get("data") or {}).get("outputs") or {}).get("answer") or "")
                )
            elif kind == "message_end":
                usage = (event.get("metadata") or {}).get("usage") or {}
            elif kind == "error":
                raise RuntimeError(str(event.get("message"))[:200])
    return "".join(answer), nodes, usage, time.monotonic() - started


def check(case: dict[str, Any], routes: dict[str, str], answer: str, nodes: list[str]) -> list[str]:
    problems = []
    if route := case.get("route"):
        if routes[route] not in nodes:
            problems.append(f"没走到“{route}”分支")
    if (words := case.get("any")) and not any(w in answer for w in words):
        problems.append(f"回答里没有 {words} 中的任何一个")
    for w in case.get("all") or []:
        if w not in answer:
            problems.append(f"回答里缺“{w}”")
    for w in case.get("none") or []:
        if w in answer:
            problems.append(f"回答里不该有“{w}”")
    for pat in ALWAYS_FORBIDDEN:
        if re.search(pat, answer):
            problems.append(f"回答里出现了敏感内容（{pat}）")
    if not answer.strip():
        problems.append("没有回答")
    return problems


def run_suite(s: EvalSettings, suite: dict[str, Any]) -> list[Result]:
    app = suite["app"]
    key = {"hr-assistant": s.hr_app_key, "policy-qa": s.policy_app_key}.get(app, s.inquiry_app_key)
    user = s.sales if suite.get("user") == "sales" else s.employee
    if key is None or not user:
        print(f"跳过 {app}：没配 App Key 或提问员工", file=sys.stderr)
        return []
    headers = {"Authorization": f"Bearer {key.get_secret_value()}"}
    if s.dify_host:
        headers["Host"] = s.dify_host
    out = []
    with httpx.Client(
        base_url=str(s.dify_api_url).rstrip("/"), headers=headers, timeout=s.timeout_seconds
    ) as http:
        for case in suite["cases"]:
            try:
                answer, nodes, usage, secs = chat(http, user, case["q"])
            except (httpx.HTTPError, RuntimeError, ValueError) as e:
                out.append(Result(app, case["id"], case["q"], False, [f"调用失败：{e}"], "", 0))
                continue
            problems = check(case, suite.get("routes") or {}, answer, nodes)
            out.append(
                Result(
                    app,
                    case["id"],
                    case["q"],
                    not problems,
                    problems,
                    answer,
                    secs,
                    int(usage.get("total_tokens") or 0),
                    float(usage.get("total_price") or 0),
                    str(usage.get("currency") or ""),
                    [n for n in nodes if FALLBACK.search(n)],
                )
            )
    return out


def report(results: list[Result]) -> str:
    lines = [f"# 标准问题集评测 {datetime.datetime.now():%Y-%m-%d %H:%M}", ""]
    lines += [
        "| 应用 | 通过 | 平均秒数 | 最慢秒数 | Token | 费用 | 用了兜底 |",
        "|---|---|---|---|---|---|---|",
    ]
    for app in dict.fromkeys(r.app for r in results):
        rs = [r for r in results if r.app == app]
        secs = [r.seconds for r in rs if r.seconds]
        cur = next((r.currency for r in rs if r.currency), "")
        lines.append(
            f"| {app} | {sum(r.passed for r in rs)}/{len(rs)} "
            f"| {statistics.mean(secs) if secs else 0:.1f} | {max(secs, default=0):.1f} "
            f"| {sum(r.tokens for r in rs)} | {sum(r.price for r in rs):.4f} {cur} "
            f"| {sum(bool(r.fallback) for r in rs)} |"
        )
    failed = [r for r in results if not r.passed]
    if failed:
        lines += ["", "## 没通过", ""]
        for r in failed:
            lines.append(f"- **{r.app} / {r.id}**：{r.question} → {'；'.join(r.problems)}")
            lines.append(f"  - 回答：{r.answer[:300]!r}")
    fell = [r for r in results if r.fallback]
    if fell:
        lines += ["", "## 用了兜底（主模型失败）", ""]
        lines += [f"- {r.app} / {r.id}：{', '.join(r.fallback)}" for r in fell]
    return "\n".join(lines) + "\n"


def load(paths: list[Path]) -> list[dict[str, Any]]:
    return [yaml.safe_load(p.read_text(encoding="utf-8")) for p in paths]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", type=Path, help="额外的问题集（例如本公司的制度问题）")
    ap.add_argument("--only", help="只测这个应用，例如 hr-assistant")
    args = ap.parse_args()
    s = EvalSettings()
    suites = load(sorted(GOLDEN.glob("*.yaml")) + list(args.files))
    results: list[Result] = []
    for suite in suites:
        if not args.only or suite["app"] == args.only:
            results += run_suite(s, suite)
    if not results:
        print("没有可测的问题（检查 EB_EVAL_* 配置）", file=sys.stderr)
        return 2
    text = report(results)
    RESULTS.mkdir(exist_ok=True)
    stamp = f"{datetime.datetime.now():%Y%m%d-%H%M%S}"
    (RESULTS / f"{stamp}.md").write_text(text, encoding="utf-8")
    rows = [r.__dict__ for r in results]
    (RESULTS / f"{stamp}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), "utf-8")
    print(text)
    rate = sum(r.passed for r in results) / len(results)
    print(f"通过率 {rate:.0%}（要求 ≥ {s.min_pass_rate:.0%}），结果在 {RESULTS}/{stamp}.md")
    return 0 if rate >= s.min_pass_rate else 1


if __name__ == "__main__":
    sys.exit(main())
