"""员工前台审批：真实原生 Workflow；X4 账号只能调这一个写方法，且只能替当前审批人本人操作。"""

from unittest.mock import patch

import frappe
from frappe.app import application
from frappe.tests.utils import FrappeTestCase
from werkzeug.test import Client
from werkzeug.wrappers import Response

from enterprise_base.tests.card_support import records
from enterprise_base.wecom import portal_actions as portal

X4 = "eb-x4@integration.invalid"


class TestPortalActions(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.data, self.users, self.employees = records()
        self.doc = frappe.get_doc(
            {
                "doctype": "Leave Application",
                **self.data["leave"],
                "employee": self.employees["staff"].name,
                "leave_approver": self.users["manager"],
                "eb_hr_approver": self.users["hr"],
            }
        ).insert()

    def tearDown(self):
        frappe.set_user("Administrator")

    def decide(self, label, action="approve", leave=None):
        frappe.set_user(X4)
        try:
            return portal.decide(self.employees[label].wecom_userid, leave or self.doc.name, action)
        finally:
            frappe.set_user("Administrator")

    def test_two_stages_as_actual_approver(self):
        with patch.object(frappe, "enqueue"):
            self.assertEqual(self.decide("staff"), {"ok": False, "reason": "forbidden"})
            self.assertEqual(self.decide("hr"), {"ok": False, "reason": "forbidden"})
            self.assertEqual(self.decide("manager"), {"ok": True, "stage": "EB Leave HR"})
            self.doc.reload()
            self.assertEqual(self.doc.modified_by, self.users["manager"])
            # 主管环节已过，重复点不再生效
            self.assertFalse(self.decide("manager")["ok"])
            self.assertTrue(self.decide("hr")["ok"])
            self.doc.reload()
            self.assertEqual((self.doc.status, self.doc.docstatus), ("Approved", 1))
            self.assertEqual(self.doc.modified_by, self.users["hr"])
            self.assertFalse(self.decide("hr")["ok"])
        self.assertEqual(frappe.session.user, "Administrator")

    def test_return_left_unknown_and_invalid(self):
        with patch.object(frappe, "enqueue"), patch.object(frappe, "logger") as logger:
            self.employees["manager"].db_set("status", "Left")
            self.assertFalse(self.decide("manager")["ok"])
            self.employees["manager"].db_set("status", "Active")
            frappe.set_user(X4)
            self.assertFalse(portal.decide("unknown-user", self.doc.name, "approve")["ok"])
            self.assertEqual(
                portal.decide(self.employees["manager"].wecom_userid, self.doc.name, "submit"),
                {"ok": False, "reason": "invalid"},
            )
            frappe.set_user("Administrator")
            self.assertFalse(self.decide("manager", leave="HR-LAP-NOT-EXIST")["ok"])
            self.assertTrue(self.decide("manager", action="return")["ok"])
            self.doc.reload()
            self.assertEqual(self.doc.eb_leave_state, "EB Leave Returned")
            for value in self.data["private_samples"]:
                self.assertNotIn(value, str(logger.mock_calls))

    def test_only_x4_can_call(self):
        for user in ("Administrator", self.users["manager"]):
            frappe.set_user(user)
            with self.assertRaises(frappe.PermissionError):
                portal.decide(self.employees["manager"].wecom_userid, self.doc.name, "approve")
        frappe.set_user("Administrator")
        self.doc.reload()
        self.assertEqual(self.doc.eb_leave_state, "EB Leave Manager")

    def test_x4_http_only_post(self):
        x4 = frappe.get_doc("User", X4)
        x4.enabled, x4.api_key, x4.api_secret = 1, "synthetic-portal-x4", "synthetic-portal-secret"
        x4.save()
        frappe.db.commit()
        site = frappe.local.site
        token = "token synthetic-portal-x4:synthetic-portal-secret"
        headers = {"Host": site, "Authorization": token}
        body = dict(
            userid=self.employees["manager"].wecom_userid, leave=self.doc.name, action="approve"
        )
        path = "/api/method/" + portal.METHOD
        try:
            client = Client(application, Response, use_cookies=False)
            self.assertEqual(client.get(path, headers=headers, query_string=body).status_code, 403)
            with patch.object(frappe, "enqueue"):
                response = client.post(path, headers=headers, json=body)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["message"], {"ok": True, "stage": "EB Leave HR"})
        finally:
            if not getattr(frappe.local, "db", None):
                frappe.init(site=site)
                frappe.connect()
            frappe.set_user("Administrator")
            x4 = frappe.get_doc("User", X4)
            x4.enabled, x4.api_key, x4.api_secret = 0, None, None
            x4.save()
            frappe.delete_doc("Leave Application", self.doc.name, force=1, ignore_permissions=True)
            frappe.db.commit()
