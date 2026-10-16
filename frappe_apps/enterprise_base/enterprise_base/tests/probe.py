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
    user.enabled, user.api_key = 0, None
    user.save()
