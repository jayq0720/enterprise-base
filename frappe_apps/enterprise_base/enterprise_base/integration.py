"""原生 Webhook 配置与经 Frappe 导出的安全 fixtures。"""

import json
import os
from pathlib import Path

import frappe


def configure_webhooks() -> None:
    secret = os.environ.get("EB_SYNC_WEBHOOK_SECRET")
    for name, event in (("EB Wiki Update", "on_update"), ("EB Wiki Trash", "on_trash")):
        doc = (
            frappe.get_doc("Webhook", name)
            if frappe.db.exists("Webhook", name)
            else frappe.new_doc("Webhook")
        )
        doc.update(
            {
                "name": name,
                "webhook_doctype": "Wiki Document",
                "webhook_docevent": event,
                "request_url": "http://policy-sync:8080/v1/hooks/wiki",
                "request_method": "POST",
                "request_structure": "JSON",
                "webhook_headers": [],
                "webhook_json": '{"doctype": "Wiki Document", "name": {{ doc.name | tojson }}}',
            }
        )
        doc.update({"enabled": int(bool(secret)), "enable_security": int(bool(secret))})
        if secret:
            if len(secret) < 32:
                frappe.throw("EB_SYNC_WEBHOOK_SECRET 至少 32 位；未启用 Wiki 推送。")
            doc.webhook_secret = secret
        doc.save()


def export_configuration() -> None:
    from frappe.utils.fixtures import export_fixtures

    export_fixtures("enterprise_base")
    folder = Path(frappe.get_app_path("enterprise_base", "fixtures"))
    for path in folder.glob("*.json"):
        rows = json.loads(path.read_text())
        for row in rows:
            for key in (
                "api_key",
                "api_secret",
                "password",
                "webhook_secret",
                "new_password",
                "reset_password_key",
            ):
                row.pop(key, None)
            if row["doctype"] == "Webhook":
                row.update(
                    enabled=0, enable_security=0
                )  # 空库安全导入，after_sync 再注入实际密钥。
            if row["doctype"] == "User":
                row.update(enabled=0, send_welcome_email=0)
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
