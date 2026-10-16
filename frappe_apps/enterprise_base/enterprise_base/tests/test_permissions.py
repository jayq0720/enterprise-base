"""临时真实站点的 REST、导出和审批回归；仅使用合成人员数据。"""

import json
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.app import application
from frappe.integrations.doctype.webhook.webhook import enqueue_webhook
from frappe.model.workflow import apply_workflow
from frappe.tests.utils import FrappeTestCase
from werkzeug.test import Client
from werkzeug.wrappers import Response


class TestPermissions(FrappeTestCase):
    def test_permissions_http(self):
        site = frappe.local.site
        data = json.loads(Path(__file__).with_name("p2_data.json").read_text())
        for row in data["records"]:
            if not frappe.db.exists(row["doctype"], row.get("name")):
                frappe.get_doc(row).insert()
        for label, roles in data["users"].items():
            email = f"p2-{label}@test.invalid"
            user = frappe.get_doc(
                {
                    "doctype": "User",
                    "email": email,
                    "first_name": label,
                    "send_welcome_email": 0,
                    "roles": [{"role": r} for r in roles],
                }
            ).insert()
            user.api_key, user.api_secret = f"synthetic-p2-{label}", "synthetic-p2-secret"
            user.save()
        for label in ("staff", "hr"):
            frappe.get_doc(
                {
                    **data["employee"],
                    "first_name": label,
                    "employee_number": "P2-" + label,
                    "user_id": f"p2-{label}@test.invalid",
                    "wecom_userid": f"p2-{label}",
                }
            ).insert()
        employee = frappe.db.get_value("Employee", {"user_id": "p2-staff@test.invalid"}, "name")
        x4 = frappe.get_doc("User", "eb-x4@integration.invalid")
        x4.enabled, x4.api_key, x4.api_secret = 1, "synthetic-p2-x4", "synthetic-p2-secret"
        x4.save()
        wiki = frappe.get_doc(data["wiki_space"]).insert()
        page = frappe.get_doc(
            {
                "doctype": "Wiki Document",
                "title": "P2 Synthetic Policy",
                "parent_wiki_document": wiki.root_group,
                "wiki_space": wiki.name,
                "content": "Synthetic policy",
                "is_published": 0,
            }
        ).insert()
        frappe.db.commit()
        client = Client(application, Response, use_cookies=False)

        def request(label, method, path, expected=200, **kwargs):
            response = client.open(
                path,
                method=method,
                headers={
                    "Host": site,
                    "Authorization": f"token synthetic-p2-{label}:synthetic-p2-secret",
                },
                **kwargs,
            )
            self.assertEqual(
                response.status_code, expected, f"{label} {method} {path}：{response.status_code}"
            )
            return response.get_json() or {}

        def restore():
            frappe.init(site=site)
            frappe.connect()
            frappe.set_user("Administrator")

        try:
            path = "/api/resource/Employee/" + employee
            for label, allowed in (("hr", False), ("manager", True), ("staff", True)):
                row = request(label, "GET", path)["data"]
                for field in data["sensitive"]:
                    self.assertEqual(field in row, allowed, f"{label} 敏感字段读取边界失败")
            request(
                "staff",
                "GET",
                "/api/resource/Employee/"
                + request(
                    "hr",
                    "GET",
                    "/api/resource/Employee",
                    query_string={"filters": json.dumps([["user_id", "=", "p2-hr@test.invalid"]])},
                )["data"][0]["name"],
                403,
            )
            exported = request(
                "hr",
                "GET",
                "/api/resource/Employee",
                query_string={"fields": json.dumps(["name", *data["sensitive"]])},
            )["data"]
            self.assertTrue(all(not (set(row) & set(data["sensitive"])) for row in exported))
            response = client.get(
                "/api/method/frappe.desk.reportview.export_query",
                headers={
                    "Host": site,
                    "Authorization": "token synthetic-p2-hr:synthetic-p2-secret",
                },
                query_string={
                    "doctype": "Employee",
                    "fields": json.dumps(["name", "cell_number", "ctc"]),
                    "file_format_type": "CSV",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b"19900000000", response.data)
            self.assertNotIn(b"424242", response.data)
            request(
                "hr",
                "PUT",
                "/api/resource/Wiki Document/" + page.name,
                403,
                json={"is_published": 1},
            )
            restore()
            page.reload()
            frappe.set_user("p2-hr@test.invalid")
            page = apply_workflow(page.as_json(), "EB Send for Review")
            frappe.set_user("p2-manager@test.invalid")
            page = apply_workflow(page.as_json(), "EB Publish")
            self.assertEqual(page.is_published, 1)
            with patch("requests.request") as send:
                send.return_value.text = "accepted"
                enqueue_webhook(page, {"name": "EB Wiki Update"})
                self.assertEqual(
                    send.call_args.kwargs["url"], "http://policy-sync:8080/v1/hooks/wiki"
                )
                self.assertEqual(
                    set(json.loads(send.call_args.kwargs["data"])), {"doctype", "name"}
                )
            frappe.db.commit()
            leave = request(
                "x4",
                "POST",
                "/api/resource/Leave Application",
                json={**data["leave"], "employee": employee},
                expected=200,
            )["data"]
            request(
                "x4",
                "PUT",
                "/api/resource/Leave Application/" + leave["name"],
                403,
                json={"docstatus": 1},
            )
            request("x4", "DELETE", "/api/resource/Leave Application/" + leave["name"], 403)
            request("x4", "GET", path, 403)
            request(
                "x4",
                "GET",
                "/api/resource/Employee",
                403,
                query_string={"fields": '["cell_number"]'},
            )
            request("manager", "PUT", path, json={"status": "Left", "relieving_date": "2026-10-16"})
            request("staff", "GET", path, 401)
            request(
                "x4",
                "GET",
                "/api/resource/Employee",
                query_string={
                    "fields": '["name","employee_name","user_id","wecom_userid"]',
                    "filters": json.dumps(
                        [["wecom_userid", "=", "p2-staff"], ["status", "=", "Active"]]
                    ),
                },
            )
            restore()
            self.assertFalse(frappe.db.get_value("User", "p2-staff@test.invalid", "enabled"))
        finally:
            restore()
            x4 = frappe.get_doc("User", "eb-x4@integration.invalid")
            x4.enabled, x4.api_key, x4.api_secret = 0, None, None
            x4.save()
            frappe.db.commit()
