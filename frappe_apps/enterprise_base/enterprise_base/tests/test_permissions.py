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

EMPLOYEE_API = "/api/resource/Employee"


class TestPermissions(FrappeTestCase):
    def test_permissions_http(self):
        site = frappe.local.site
        frappe.db.set_single_value("System Settings", "allow_error_traceback", 0)
        data = json.loads(Path(__file__).with_name("p2_data.json").read_text())
        for row in data["records"]:
            if not frappe.db.exists(row["doctype"], row.get("name")):
                created = frappe.get_doc(row).insert()
                if row["doctype"] == "Version":
                    self.assertNotIn("19900000000", created.data)
        for label, roles in data["users"].items():
            email = f"p2-{label}@test.invalid"
            user = frappe.get_doc(
                {**data["user"], "email": email, "roles": [{"role": r} for r in roles]}
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
        hr_employee = frappe.db.get_value("Employee", {"user_id": "p2-hr@test.invalid"}, "name")
        x4 = frappe.get_doc("User", "eb-x4@integration.invalid")
        x4.enabled, x4.api_key, x4.api_secret = 1, "synthetic-p2-x4", "synthetic-p2-secret"
        x4.save()
        wiki = frappe.get_doc(data["wiki_space"]).insert()
        knowledge = frappe.get_doc(
            {
                "doctype": "EB Knowledge Space",
                "space_name": "P2 Synthetic Space",
                "wiki_space": wiki.name,
            }
        ).insert()
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

        def headers(label):
            return {
                "Host": site,
                "Authorization": f"token synthetic-p2-{label}:synthetic-p2-secret",
            }

        def request(label, method, path, expected=200, **kwargs):
            response = client.open(path, method=method, headers=headers(label), **kwargs)
            self.assertEqual(response.status_code, expected, f"{label} {method} {path}")
            return response.get_json() or {}

        def restore():
            frappe.init(site=site)
            frappe.connect()
            frappe.set_user("Administrator")

        try:
            path = EMPLOYEE_API + "/" + employee
            space_path = "/api/resource/EB Knowledge Space/" + knowledge.name
            wiki_path = "/api/resource/Wiki Document/" + page.name
            for label, target, allowed in data["employee_reads"]:
                target = employee if target == "staff" else hr_employee
                row = request(label, "GET", EMPLOYEE_API + "/" + target)["data"]
                for field in data["sensitive"]:
                    if allowed and field in data["employee"]:
                        self.assertEqual(row.get(field), data["employee"][field])
                    elif not allowed:
                        self.assertNotIn(field, row)
            request("staff", "GET", EMPLOYEE_API + "/" + hr_employee, 403)
            request("hr", "PUT", path, 403, json={"user_id": "p2-hr@test.invalid"})
            request("hr", "PUT", path, 403, json={"leave_approver": "p2-hr@test.invalid"})
            request("hr", "PUT", space_path, 403, json={"visible_to_all": 1})
            request("manager", "PUT", space_path, json={"visible_to_all": 1})
            request("manager", "GET", data["position_path"])
            exported = request("hr", "GET", EMPLOYEE_API, query_string=data["restricted_query"])[
                "data"
            ]
            self.assertTrue(all(not (set(row) & set(data["sensitive"])) for row in exported))
            response = client.get(
                "/api/method/frappe.desk.reportview.export_query",
                headers=headers("hr"),
                query_string=data["export_query"],
            )
            self.assertEqual(response.status_code, 200)
            for value in data["private_samples"]:
                self.assertNotIn(value.encode(), response.data)
            request("hr", "PUT", wiki_path, json={"content": "Edited synthetic draft"})
            request("hr", "PUT", wiki_path, 403, json={"is_published": 1})
            request("hr", "PUT", wiki_path, 403, json={"is_group": 1, "is_published": 1})
            restore()
            page.reload()
            frappe.set_user("p2-hr@test.invalid")
            page = apply_workflow(page.as_json(), "EB Send for Review")
            frappe.set_user("p2-manager@test.invalid")
            page = apply_workflow(page.as_json(), "EB Publish")
            self.assertEqual(page.is_published, 1)
            self.assertTrue(
                frappe.safe_eval(
                    frappe.get_doc("Webhook", "EB Wiki Update").condition, eval_locals={"doc": page}
                )
            )
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
            for method in ("get_leave_details", "get_leave_approver"):
                request(
                    "x4",
                    "GET",
                    "/api/method/hrms.hr.doctype.leave_application.leave_application." + method,
                    query_string={"employee": employee, "date": "2026-10-16"},
                )
            leave = request(
                "x4",
                "POST",
                "/api/resource/Leave Application",
                json={**data["leave"], "employee": employee},
            )["data"]
            self.assertEqual((leave["docstatus"], leave["status"]), (0, "Open"))
            leave_path = "/api/resource/Leave Application/" + leave["name"]
            for dt, query in data["x4_reads"].items():
                request("x4", "GET", "/api/resource/" + dt, query_string=query)
            request("x4", "PUT", leave_path, 403, json={"docstatus": 1})
            request("x4", "DELETE", leave_path, 403)
            for query in data["x4_joins"]:
                request("x4", "GET", "/api/resource/Leave Application", 403, query_string=query)
            request("x4", "GET", path, 403)
            request("x4", "GET", EMPLOYEE_API, 403, query_string=data["x4_sensitive_query"])
            request("manager", "PUT", path, json={"status": "Left", "relieving_date": "2026-10-16"})
            request("staff", "GET", path, 401)
            active = request(
                "x4",
                "GET",
                EMPLOYEE_API,
                query_string={
                    "fields": '["name","employee_name","user_id","wecom_userid"]',
                    "filters": json.dumps(
                        [["wecom_userid", "=", "p2-staff"], ["status", "=", "Active"]]
                    ),
                },
            )
            self.assertEqual(active["data"], [])
            restore()
            self.assertFalse(frappe.db.get_value("User", "p2-staff@test.invalid", "enabled"))
        finally:
            restore()
            x4 = frappe.get_doc("User", "eb-x4@integration.invalid")
            x4.enabled, x4.api_key, x4.api_secret = 0, None, None
            x4.save()
            frappe.db.commit()


class TestReviewFixes(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")

    def test_legacy_published_page_aligned(self):
        from enterprise_base.permissions import align_wiki_review_state

        wiki = frappe.get_doc(
            {
                "doctype": "Wiki Space",
                "space_name": "P2 Legacy",
                "route": "p2-legacy-" + frappe.generate_hash(length=8),
            }
        ).insert()
        page = frappe.get_doc(
            {
                "doctype": "Wiki Document",
                "title": "P2 Legacy Policy",
                "parent_wiki_document": wiki.root_group,
                "content": "Legacy",
                "is_published": 0,
            }
        ).insert()
        # 模拟启用审核流程前就已发布的页面：发布标记是 1，审核状态却是草稿
        frappe.db.set_value(
            "Wiki Document",
            page.name,
            {"is_published": 1, "eb_review_state": "EB Draft"},
            update_modified=False,
        )
        align_wiki_review_state()
        page.reload()
        self.assertEqual((page.is_published, page.eb_review_state), (1, "EB Published"))
        page.content = "Legacy edited by manager"
        page.save()
        align_wiki_review_state()
        page.reload()
        self.assertEqual(page.eb_review_state, "EB Published")

    def test_hr_scope_keeps_manual_restriction(self):
        data = json.loads(Path(__file__).with_name("p2_data.json").read_text())
        for row in data["records"]:
            if row["doctype"] != "Version" and not frappe.db.exists(
                row["doctype"], row.get("name")
            ):
                frappe.get_doc(row).insert()
        email = "p2-scope@test.invalid"
        user = frappe.get_doc(
            {**data["user"], "email": email, "roles": [{"role": "HR User"}]}
        ).insert()
        own, other = (
            frappe.get_doc(
                {
                    **data["employee"],
                    "first_name": label,
                    "employee_number": "P2-scope-" + label,
                    "user_id": email if label == "own" else None,
                    "wecom_userid": "p2-scope-" + label,
                }
            ).insert()
            for label in ("own", "other")
        )
        manual = frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": email,
                "allow": "Employee",
                "for_value": other.name,
            }
        ).insert(ignore_permissions=True)
        user.reload()
        user.save()
        self.assertFalse(
            frappe.db.exists(
                "User Permission", {"user": email, "allow": "Employee", "for_value": own.name}
            )
        )
        self.assertTrue(frappe.db.exists("User Permission", manual.name))


class TestX4Duties(FrappeTestCase):
    def test_x4_reads_designation_duties_only(self):
        site = frappe.local.site
        frappe.set_user("Administrator")
        data = json.loads(Path(__file__).with_name("p2_data.json").read_text())
        for row in data["records"]:
            if row["doctype"] != "Version" and not frappe.db.exists(
                row["doctype"], row.get("name")
            ):
                frappe.get_doc(row).insert()
        suffix = frappe.generate_hash(length=8)
        designation = frappe.get_doc(
            {
                "doctype": "Designation",
                "designation_name": "P2 Duty " + suffix,
                "eb_responsibilities": "<p>Synthetic duty</p>",
            }
        ).insert()
        frappe.get_doc(
            {
                **data["employee"],
                "first_name": "duty",
                "employee_number": "P2-duty-" + suffix,
                "user_id": None,
                "leave_approver": None,
                "create_user_permission": 0,
                "wecom_userid": "p2-duty-" + suffix,
                "designation": designation.name,
            }
        ).insert()
        x4 = frappe.get_doc("User", "eb-x4@integration.invalid")
        x4.enabled, x4.api_key, x4.api_secret = 1, "synthetic-duty-x4", "synthetic-duty-secret"
        x4.save()
        frappe.db.commit()
        client = Client(application, Response, use_cookies=False)
        auth = {"Host": site, "Authorization": "token synthetic-duty-x4:synthetic-duty-secret"}

        def get(path, expected=200, **query):
            response = client.get(path, headers=auth, query_string=query)
            self.assertEqual(response.status_code, expected, f"{path} {query}")
            return (response.get_json() or {}).get("data")

        own = [["name", "=", designation.name]]
        try:
            rows = get(
                EMPLOYEE_API,
                fields='["name","employee_name","user_id","designation"]',
                filters=json.dumps([["wecom_userid", "=", "p2-duty-" + suffix]]),
            )
            self.assertEqual(rows[0]["designation"], designation.name)
            rows = get(
                "/api/resource/Designation",
                fields='["name","eb_responsibilities"]',
                filters=json.dumps(own),
            )
            self.assertEqual(
                rows, [{"name": designation.name, "eb_responsibilities": "<p>Synthetic duty</p>"}]
            )
            fields = '["name","eb_responsibilities"]'
            get("/api/resource/Designation", 403, fields=fields)
            bad_filters: tuple[object, ...] = (
                [],
                None,
                {},
                [["name", "=", ""]],
                [["name", "=", "   "]],
                [["name", "=", None]],
                [["eb_responsibilities", "=", "<p>Synthetic duty</p>"]],
                own + [["eb_responsibilities", "=", "<p>Synthetic duty</p>"]],
                [["status", "=", "Active"]],
            )
            for filters in bad_filters:
                get("/api/resource/Designation", 403, fields=fields, filters=json.dumps(filters))
            # 岗位只能按名称精确查名称和职责两个字段，不能取整条记录、其它字段或模糊查
            get("/api/resource/Designation", 403, fields='["appraisal_template"]')
            get("/api/resource/Designation", 403, filters=json.dumps(own))
            get(
                "/api/resource/Designation",
                403,
                fields='["name"]',
                filters=json.dumps([["name", "like", "%"]]),
            )
            get("/api/resource/Designation", 403, fields='["name"]', order_by="name asc")
            get("/api/resource/Designation/" + designation.name, 403)
            response = client.put(
                "/api/resource/Designation/" + designation.name,
                headers=auth,
                json={"eb_responsibilities": "changed"},
            )
            self.assertEqual(response.status_code, 403)
        finally:
            frappe.set_user("Administrator")
            x4 = frappe.get_doc("User", "eb-x4@integration.invalid")
            x4.enabled, x4.api_key, x4.api_secret = 0, None, None
            x4.save()
            frappe.db.commit()
