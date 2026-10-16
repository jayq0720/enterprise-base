"""制度发布 → Webhook → X5 同步到 Dify → X3 按岗位检索；撤回后检索不到。"""

from typing import Any

from e2e.clients import X3, DifyDatasets, Frappe, wait_for
from e2e.settings import E2ESettings
from e2e.world import World

from .conftest import assert_clean


def publish(frappe: Frappe, world: World, wiki_space: str, title: str, content: str) -> str:
    """按制度审核流程发布：起草 → 送审 → 发布。"""
    name = str(
        frappe.insert(
            "Wiki Document",
            {
                "title": title,
                "content": content,
                "parent_wiki_document": world.root(frappe, wiki_space),
                "is_published": 0,
            },
        )["name"]
    )
    world.cleanup.append(lambda: frappe.delete("Wiki Document", name))
    frappe.apply_workflow("Wiki Document", name, "EB Send for Review")
    page = frappe.apply_workflow("Wiki Document", name, "EB Publish")
    assert page["is_published"] == 1
    return name


def titles(x3: X3, user: str, query: str) -> list[str]:
    return [str(r["title"]) for r in x3.search(user, query)["records"]]


def test_publish_scope_withdraw(
    settings: E2ESettings, frappe: Frappe, x3: X3, world: World
) -> None:
    timeout = settings.sync_timeout_seconds
    t_all, t_b = f"{world.tag} 差旅报销制度", f"{world.tag} 岗位B操作规范"
    p_all = publish(frappe, world, world.wiki_all, t_all, f"{t_all}：出差需提前申请。")
    p_b = publish(frappe, world, world.wiki_b, t_b, f"{t_b}：只给岗位B看。")

    # 同步是异步的：等 B 能检索到两篇
    wait_for(lambda: True if t_b in titles(x3, world.b.wecom, t_b) else None, timeout)
    wait_for(lambda: True if t_all in titles(x3, world.b.wecom, t_all) else None, timeout)
    # A 只能看全员空间
    assert t_all in titles(x3, world.a.wecom, t_all)
    assert t_b not in titles(x3, world.a.wecom, t_b)

    # Dify 里每页一份，写了范围和来源
    docs = DifyDatasets(settings).documents(world.dataset)
    meta = {
        d["name"]: {m["name"]: m.get("value") for m in d.get("doc_metadata") or []} for d in docs
    }
    assert meta[t_all]["eb_scope"] == ",all,"
    assert meta[t_all]["eb_source"] == p_all
    assert meta[t_b]["eb_scope"] == f",desig:{world.b.designation},"
    assert sorted(meta) == sorted([t_all, t_b])

    # 撤回后 B 也检索不到
    frappe.apply_workflow("Wiki Document", p_b, "EB Withdraw")
    wait_for(lambda: True if t_b not in titles(x3, world.b.wecom, t_b) else None, timeout)
    assert_clean(world, docs)


def test_outsiders_get_nothing(x3: X3, world: World) -> None:
    out: dict[str, Any] = x3.search(f"{world.tag.lower()}-nobody", "差旅报销")
    assert out["records"] == [] and out["notice"]
