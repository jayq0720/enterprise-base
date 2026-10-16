"""仅测试站点：验证官方导入不会重置已配置的技术账号。"""

import frappe
from frappe.utils.fixtures import sync_fixtures

from enterprise_base.install import configure


def verify_user_migration() -> None:
    if not frappe.conf.allow_tests:
        frappe.throw("仅测试站点可运行此验证。")
    user = frappe.get_doc("User", "eb-x3@integration.invalid")
    user.enabled, user.api_key, user.api_secret = 1, "synthetic-api-key", "synthetic-api-secret"
    user.save()
    sync_fixtures("enterprise_base")
    configure()
    user.reload()
    if not (
        user.enabled
        and user.api_key == "synthetic-api-key"
        and user.get_password("api_secret") == "synthetic-api-secret"
    ):
        frappe.throw("fixture 导入改变了既有技术账号的启用状态或 API 凭据。")
    user.enabled, user.api_key, user.api_secret = 0, None, None
    user.save()


def verify_http_contract() -> None:
    from frappe.app import application
    from werkzeug.test import Client
    from werkzeug.wrappers import Response

    if not frappe.conf.allow_tests:
        frappe.throw("仅测试站点可运行此验证。")
    site = frappe.local.site
    user = frappe.get_doc("User", "eb-x3@integration.invalid")
    user.enabled, user.api_key, user.api_secret = 1, "synthetic-http-key", "synthetic-http-secret"
    user.save()
    wiki = frappe.get_doc(
        {
            "doctype": "Wiki Space",
            "space_name": "HTTP Test",
            "route": "http-" + frappe.generate_hash(length=10),
        }
    ).insert()
    space = frappe.get_doc(
        {
            "doctype": "EB Knowledge Space",
            "space_name": wiki.name,
            "wiki_space": wiki.name,
            "visible_to_all": 1,
        }
    ).insert()
    frappe.db.commit()
    headers = {"Host": site, "Authorization": "token synthetic-http-key:synthetic-http-secret"}
    client = Client(application, Response)
    try:
        response = client.get(
            "/api/method/enterprise_base.duties.api.knowledge_spaces", headers=headers
        )
        if response.status_code != 200 or not any(
            row["space"] == space.name and row["scope"] == ["all"]
            for row in (response.get_json() or {}).get("message", [])
        ):
            raise RuntimeError(f"真实 HTTP 知识空间契约失败，状态码 {response.status_code}。")
        for path, params, expected in (
            ("/api/resource/Employee", {"fields": '["name","department","designation"]'}, 200),
            ("/api/resource/Employee", {"fields": '["cell_number","bank_ac_no"]'}, 403),
            ("/api/resource/Employee/unknown", {}, 403),
            ("/api/method/frappe.client.get_list", {"doctype": "Employee"}, 403),
        ):
            response = client.get(path, query_string=params, headers=headers)
            if response.status_code != expected:
                raise RuntimeError(
                    f"真实 HTTP {path} 状态码 {response.status_code}，应为 {expected}。"
                )
        response = client.get(
            "/api/method/enterprise_base.duties.api.knowledge_spaces", headers={"Host": site}
        )
        if response.status_code != 403:
            raise RuntimeError("真实 HTTP Guest 未被拒绝。")
    finally:
        frappe.init(site=site)
        frappe.connect()
        frappe.set_user("Administrator")
        user = frappe.get_doc("User", "eb-x3@integration.invalid")
        user.enabled, user.api_key, user.api_secret = 0, None, None
        user.save()
        frappe.db.commit()
