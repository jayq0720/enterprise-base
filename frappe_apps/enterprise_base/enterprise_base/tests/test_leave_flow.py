"""真实 Workflow/HTTP/余额回归；仅使用合成人员和临时站点。"""

import json
from pathlib import Path

import frappe
from frappe.app import application
from frappe.model.workflow import apply_workflow
from frappe.tests.utils import FrappeTestCase
from hrms.hr.doctype.leave_application.leave_application import get_leave_balance_on
from werkzeug.test import Client
from werkzeug.wrappers import Response


class TestLeaveFlow(FrappeTestCase):
    def setUp(self):
        self.site = frappe.local.site
        frappe.set_user("Administrator")
        data = json.loads(Path(__file__).with_name("p2_data.json").read_text())
        for row in data["records"]:
            if row["doctype"] != "Version" and not frappe.db.exists(
                row["doctype"], row.get("name")
            ):
                frappe.get_doc(row).insert()
        self.suffix = frappe.generate_hash(length=8)
        self.users = {}
        for label, roles in {
            "staff": ["Employee", "Leave Approver"],
            "manager": ["Employee", "Leave Approver"],
            "hr": ["Employee", "HR Manager"],
            "hruser": ["Employee", "HR User"],
            "other": ["Employee", "HR Manager", "Leave Approver"],
        }.items():
            email = f"leave-{label}-{self.suffix}@test.invalid"
            frappe.get_doc(
                {**data["user"], "email": email, "roles": [{"role": r} for r in roles]}
            ).insert()
            self.users[label] = email
        self.employee = frappe.get_doc(
            {
                **data["employee"],
                "first_name": "Leave Synthetic",
                "employee_number": self.suffix,
                "user_id": self.users["staff"],
                "wecom_userid": "leave-" + self.suffix,
                "leave_approver": self.users["manager"],
                "eb_hr_approver": self.users["hr"],
            }
        ).insert()
        self.leave_type = "EB Paid " + self.suffix
        frappe.get_doc(
            dict(doctype="Leave Type", leave_type_name=self.leave_type, include_holiday=1)
        ).insert()
        allocation = frappe.get_doc(
            dict(
                doctype="Leave Allocation",
                employee=self.employee.name,
                leave_type=self.leave_type,
                from_date="2026-01-01",
                to_date="2026-12-31",
                new_leaves_allocated=10,
            )
        )
        allocation.insert().submit()

    def restore(self, label="Administrator"):
        if not getattr(frappe.local, "db", None):
            frappe.init(site=self.site)
            frappe.connect()
        frappe.set_user(self.users.get(label, label))

    def new_leave(self, day=19, x4=False):
        if x4:
            self.restore()
            user = frappe.get_doc("User", "eb-x4@integration.invalid")
            previous = (
                user.enabled,
                user.api_key,
                user.get_password("api_secret", raise_exception=False),
            )
            user.enabled, user.api_key, user.api_secret = (
                1,
                "synthetic-flow-x4",
                "synthetic-flow-secret",
            )
            user.save()
            frappe.db.commit()
            client = Client(application, Response, use_cookies=False)
            auth = {
                "Host": self.site,
                "Authorization": "token synthetic-flow-x4:synthetic-flow-secret",
            }
            response = client.post(
                "/api/resource/Leave Application", headers=auth, json=self.body(day)
            )
            self.assertEqual(response.status_code, 200)
            name = response.get_json()["data"]["name"]
            for method, body in (("PUT", {"docstatus": 1}), ("DELETE", None)):
                response = client.open(
                    "/api/resource/Leave Application/" + name,
                    method=method,
                    headers=auth,
                    json=body,
                )
                self.assertEqual(response.status_code, 403)
            response = client.post(
                "/api/method/frappe.model.workflow.apply_workflow",
                headers=auth,
                json={
                    "doc": {"doctype": "Leave Application", "name": name},
                    "action": "EB Manager Approve",
                },
            )
            self.assertEqual(response.status_code, 403)
            self.restore()
            user = frappe.get_doc("User", "eb-x4@integration.invalid")
            user.enabled, user.api_key, user.api_secret = previous
            user.save()
            frappe.db.commit()
            return frappe.get_doc("Leave Application", name)
        frappe.set_user(self.users["staff"])
        return frappe.get_doc({"doctype": "Leave Application", **self.body(day)}).insert()

    def body(self, day):
        return dict(
            employee=self.employee.name,
            company=self.employee.company,
            leave_type=self.leave_type,
            from_date=f"2026-10-{day}",
            to_date=f"2026-10-{day}",
            posting_date="2026-10-16",
            leave_approver=self.users["manager"],
            status="Open",
            follow_via_email=0,
        )

    def action(self, doc, label, action):
        frappe.set_user(self.users[label])
        return apply_workflow(doc.as_json(), action)

    def assert_state(self, doc, state, status="Open", submitted=0):
        doc.reload()
        self.assertEqual(
            (doc.eb_leave_state, doc.status, int(doc.docstatus)), (state, status, submitted)
        )

    def test_employee_and_x4_follow_two_stages(self):
        for day, x4, hr in ((19, False, "hr"), (20, True, "hruser")):
            self.restore()
            self.employee.db_set("eb_hr_approver", self.users[hr])
            doc = self.new_leave(day, x4)
            self.assert_state(doc, "EB Leave Manager")
            self.assertEqual(doc.eb_hr_approver, self.users[hr])
            for label, action in (
                ("staff", "EB Manager Approve"),
                ("other", "EB Manager Approve"),
                ("hr", "EB HR Approve"),
            ):
                with self.assertRaises(frappe.ValidationError):
                    self.action(doc, label, action)
            doc = self.action(doc, "manager", "EB Manager Approve")
            self.assert_state(doc, "EB Leave HR")
            with self.assertRaises(frappe.ValidationError):
                self.action(doc, "other", "EB HR Approve")
            doc = self.action(doc, hr, "EB HR Approve")
            self.assert_state(doc, "EB Leave Approved", "Approved", 1)
        self.restore(hr)
        self.assertEqual(get_leave_balance_on(self.employee.name, self.leave_type, "2026-10-21"), 8)
        forged = frappe.get_doc("Leave Application", doc.name)
        forged.eb_leave_state = "EB Leave Cancelled"
        with self.assertRaises(frappe.PermissionError):
            forged.save()
        doc = self.action(doc, hr, "EB Cancel Leave")
        self.assert_state(doc, "EB Leave Cancelled", "Cancelled", 2)
        self.restore()
        self.assertEqual(get_leave_balance_on(self.employee.name, self.leave_type, "2026-10-21"), 9)

    def test_return_reject_and_tampering(self):
        doc = self.new_leave()
        doc = self.action(doc, "manager", "EB Return Leave")
        self.assert_state(doc, "EB Leave Returned")
        doc = self.action(doc, "staff", "EB Resend Leave")
        doc = self.action(doc, "manager", "EB Manager Approve")
        doc = self.action(doc, "hr", "EB Return Leave")
        self.assert_state(doc, "EB Leave Returned")
        doc = self.action(doc, "staff", "EB Resend Leave")
        self.restore("other")
        for field, value in (
            ("status", "Approved"),
            ("docstatus", 1),
            ("eb_hr_approver", self.users["other"]),
            ("description", "tampered"),
        ):
            forged = frappe.get_doc("Leave Application", doc.name)
            forged.set(field, value)
            with self.assertRaises(frappe.PermissionError):
                forged.save()
        doc = self.action(doc, "manager", "EB Reject Leave")
        self.assert_state(doc, "EB Leave Rejected", "Rejected", 1)
        self.restore()
        self.assertEqual(
            get_leave_balance_on(self.employee.name, self.leave_type, "2026-10-21"), 10
        )

    def test_self_approval_and_missing_hr_rejected(self):
        frappe.db.set_value("Employee", self.employee.name, "leave_approver", self.users["staff"])
        body = self.body(19)
        body["leave_approver"] = self.users["staff"]
        frappe.set_user(self.users["staff"])
        doc = frappe.get_doc({"doctype": "Leave Application", **body}).insert()
        with self.assertRaises(frappe.ValidationError):
            self.action(doc, "staff", "EB Manager Approve")
        self.restore()
        frappe.get_doc("User", self.users["staff"]).add_roles("HR User")
        for hr in (None, self.users["staff"]):
            frappe.db.set_value(
                "Employee",
                self.employee.name,
                {"leave_approver": self.users["manager"], "eb_hr_approver": hr},
            )
            doc = self.new_leave(20 if hr is None else 21)
            with self.assertRaises(frappe.ValidationError):
                self.action(doc, "manager", "EB Manager Approve")

    def test_department_approver_fallback(self):
        department = frappe.get_doc(
            dict(
                doctype="Department",
                department_name="Leave " + self.suffix,
                company=self.employee.company,
                leave_approvers=[dict(approver=self.users["manager"])],
            )
        ).insert()
        frappe.db.set_value(
            "Employee", self.employee.name, {"leave_approver": None, "department": department.name}
        )
        doc = self.new_leave()
        self.assertEqual(doc.leave_approver, self.users["manager"])
        self.assert_state(self.action(doc, "manager", "EB Manager Approve"), "EB Leave HR")
