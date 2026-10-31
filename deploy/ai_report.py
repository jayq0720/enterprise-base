"""AI 助手周报（make ai-report）：读 Dify 自己存的运行记录，汇总最近几天三个应用的用量、
耗时、Token 和费用、兜底和出错、意图分布、点赞点踩。

只读，只出汇总数字和消息 / 运行 ID（到 Dify 后台“日志”里按 ID 查原文），不输出员工
userid 和提问原文。结果打印出来，同时存到 data/reports/ai-<日期>.md。
用法：python ai_report.py [天数，默认 7] [--container 容器名（不用 compose 时）]
"""

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    str(ROOT / "secrets/dify.env"),
    "-f",
    str(ROOT / "data/dify/docker/eb-compose.yaml"),
    "exec",
    "-T",
    "db_postgres",
]
PSQL = 'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -f -'
MODEL_TYPES = "('llm','question-classifier','parameter-extractor')"

SQL = """
begin transaction read only;
with r as (
  select wr.id, a.name app, wr.status, wr.elapsed_time, wr.total_tokens
  from workflow_runs wr join apps a on a.id = wr.app_id
  where wr.triggered_from = 'app-run' and wr.created_at >= now() - interval '{days} days'
), n as (
  select r.app, e.workflow_run_id run, e.node_id, e.node_type, e.status,
         nullif(e.outputs, '')::json outputs, nullif(e.execution_metadata, '')::json meta
  from workflow_node_executions e join r on r.id = e.workflow_run_id
), m as (
  select a.name app, m.from_end_user_id, m.conversation_id
  from messages m join apps a on a.id = m.app_id
  where m.invoke_from <> 'debugger' and m.created_at >= now() - interval '{days} days'
), f as (
  select a.name app, f.rating, f.message_id, f.created_at
  from message_feedbacks f join apps a on a.id = f.app_id
  where f.created_at >= now() - interval '{days} days'
)
select json_build_object(
  'runs', (select json_agg(x) from (
    select app, count(*) runs, count(*) filter (where status = 'failed') failed,
           round(avg(elapsed_time)::numeric, 1) avg_s,
           round((percentile_cont(0.95) within group (order by elapsed_time))::numeric, 1) p95_s,
           coalesce(sum(total_tokens), 0) tokens
    from r group by app order by app) x),
  'people', (select json_agg(x) from (
    select app, count(*) messages, count(distinct from_end_user_id) users,
           count(distinct conversation_id) conversations
    from m group by app) x),
  'price', (select json_agg(x) from (
    select app, meta->>'currency' currency, sum((meta->>'total_price')::numeric) price
    from n where meta->>'total_price' is not null group by 1, 2) x),
  'fallback', (select json_agg(x) from (
    select app,
           count(distinct run) filter (where node_id ~ '__m[0-9]+$') backup,
           count(distinct run) filter (where node_id ~ '__(kw|down)$') rules,
           count(distinct run) filter (where node_id = 'a_limited') limited,
           count(*) filter (where status = 'exception' and node_type in {model_types}) model_errors
    from n group by app) x),
  'intents', (select json_agg(x) from (
    select app, coalesce(outputs->>'class_id', outputs->>'intent') intent, count(*) n
    from n where node_id ~ '^intent(__m[0-9]+|__kw)?$' and status = 'succeeded'
    group by 1, 2 order by 1, 3 desc) x),
  'feedback', (select json_agg(x) from (
    select app, rating, count(*) n from f group by 1, 2) x),
  'disliked', (select json_agg(x) from (
    select app, message_id::text id from f where rating = 'dislike'
    order by created_at desc limit 20) x),
  'failed', (select json_agg(x) from (
    select app, id::text id from r where status = 'failed' limit 20) x)
)::jsonb;  -- jsonb 输出在一行里
rollback;
"""


def query(days: int, container: str | None) -> dict:
    sql = SQL.format(days=days, model_types=MODEL_TYPES)
    prefix = ["docker", "exec", "-i", container] if container else COMPOSE
    out = subprocess.run(
        [*prefix, "sh", "-c", PSQL], input=sql, capture_output=True, text=True, check=False
    )
    if out.returncode:
        raise SystemExit("查询 Dify 数据库失败：" + out.stderr.strip()[-300:])
    line = next(x for x in out.stdout.splitlines() if x.startswith("{"))
    return {k: v or [] for k, v in json.loads(line).items()}


def _by_app(rows: list[dict]) -> dict[str, dict]:
    return {r["app"]: r for r in rows}


def _pct(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "-"


def render(data: dict, days: int) -> str:
    today = datetime.date.today()
    lines = [f"# AI 助手周报（{today - datetime.timedelta(days=days)} 至 {today}）", ""]
    people, fb, fall = _by_app(data["people"]), data["feedback"], _by_app(data["fallback"])
    lines += [
        "| 应用 | 回答次数 | 提问人数 | 会话 | 平均秒数 | P95 秒数 | Token | 费用 "
        "| 出错 | 用了备用模型 | 模型全挂 | 被限流 | 点赞/点踩 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in data["runs"]:
        app, runs = r["app"], r["runs"]
        p, fl = people.get(app, {}), fall.get(app, {})
        cost = "，".join(
            f"{float(x['price']):.2f} {x['currency']}" for x in data["price"] if x["app"] == app
        )
        likes = sum(x["n"] for x in fb if x["app"] == app and x["rating"] == "like")
        dislikes = sum(x["n"] for x in fb if x["app"] == app and x["rating"] == "dislike")
        lines.append(
            f"| {app} | {runs} | {p.get('users', 0)} | {p.get('conversations', 0)} "
            f"| {r['avg_s']} | {r['p95_s']} | {r['tokens']} | {cost or '0'} "
            f"| {r['failed']}（{_pct(r['failed'], runs)}） "
            f"| {fl.get('backup', 0)}（{_pct(fl.get('backup', 0), runs)}） "
            f"| {fl.get('rules', 0)} | {fl.get('limited', 0)} | {likes}/{dislikes} |"
        )
    if data["intents"]:
        lines += ["", "## 意图分布", ""]
        for app in dict.fromkeys(x["app"] for x in data["intents"]):
            items = [f"{x['intent']} {x['n']}" for x in data["intents"] if x["app"] == app]
            lines.append(f"- {app}：" + "，".join(items))
    for key, title in (("disliked", "被点踩的回答"), ("failed", "出错的运行")):
        if data[key]:
            lines += ["", f"## {title}（到 Dify 后台对应应用的“日志”里按 ID 查看）", ""]
            lines += [f"- {x['app']}：`{x['id']}`" for x in data[key]]
    lines += [
        "",
        "说明：费用按 Dify 里配置的模型单价计算，没配单价的模型显示 0；"
        "“用了备用模型”“模型全挂”指主模型失败后走了 models.json 的兜底；"
        "make eval 的测试提问也计在内。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("days", nargs="?", type=int, default=7)
    ap.add_argument("--container")
    args = ap.parse_args()
    if not 1 <= args.days <= 366:
        raise SystemExit("天数要在 1–366 之间")
    text = render(query(args.days, args.container), args.days)
    reports = ROOT / "data/reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"ai-{datetime.date.today()}.md"
    path.write_text(text, encoding="utf-8")
    sys.stdout.write(text + f"\n已保存：{path}\n")


if __name__ == "__main__":
    main()
