"""X4 审计记录：只有 X4 账号能写，HR 只读、不能改删，字段有白名单，到期清理。"""

import frappe
from frappe.app import application
from frappe.tests.utils import FrappeTestCase
from werkzeug.test import Client
from werkzeug.wrappers import Response

from enterprise_base.duties import audit

X4 = "eb-x4@integration.invalid"


class TestAudit(FrappeTestCase):
    def tearDown(self):
        frappe.set_user("Administrator")

    def _record(self, **kw):
        frappe.set_user(X4)
        try:
            return audit.record(**{"channel": "portal", "tool": "decide", "outcome": "ok", **kw})
        finally:
            frappe.set_user("Administrator")

    def test_x4_writes_and_record_is_immutable(self):
        target = "HR-LAP-" + frappe.generate_hash(length=8)
        self.assertEqual(
            self._record(userid="synthetic", employee="no-such-employee", target=target),
            {"ok": True},
        )
        name = frappe.db.get_value(audit.DT, {"target": target})
        doc = frappe.get_doc(audit.DT, name)
        self.assertEqual((doc.channel, doc.tool, doc.employee), ("portal", "decide", ""))
        doc.outcome = "denied"
        self.assertRaises(frappe.PermissionError, doc.save)
        self.assertRaises(frappe.PermissionError, doc.delete)
        self.assertFalse(frappe.has_permission(audit.DT, "read", doc=doc, user=X4))
        self.assertFalse(frappe.has_permission(audit.DT, "create", user=X4))
        self.assertFalse(frappe.has_permission(audit.DT, "read", doc=doc, user="Guest"))

    def test_only_x4_and_invalid_values(self):
        with self.assertRaises(frappe.PermissionError):
            audit.record(channel="portal", tool="decide", outcome="ok")
        for bad in (
            dict(channel="web"),
            dict(outcome="maybe"),
            dict(tool="Drop Table"),
            dict(target="x" * 141),
            dict(reason="请假事由不该记"),
            dict(userid=["a"]),
        ):
            self.assertEqual(self._record(**bad), {"ok": False, "reason": "invalid"}, bad)

    def test_purge_keeps_recent(self):
        target = "purge-" + frappe.generate_hash(length=8)
        self._record(target=target + "-old")
        self._record(target=target + "-new")
        old = frappe.db.get_value(audit.DT, {"target": target + "-old"})
        frappe.db.set_value(
            audit.DT,
            old,
            "creation",
            frappe.utils.add_days(frappe.utils.now(), -400),
            update_modified=False,
        )
        audit.purge()
        self.assertFalse(frappe.db.exists(audit.DT, old))
        self.assertTrue(frappe.db.exists(audit.DT, {"target": target + "-new"}))

    def test_x4_http_post(self):
        x4 = frappe.get_doc("User", X4)
        x4.enabled, x4.api_key, x4.api_secret = 1, "synthetic-audit-x4", "synthetic-audit-secret"
        x4.save()
        frappe.db.commit()
        site = frappe.local.site
        headers = {"Host": site, "Authorization": "token synthetic-audit-x4:synthetic-audit-secret"}
        target = "http-" + frappe.generate_hash(length=8)
        body = dict(channel="dify", tool="leave_balance", outcome="ok", target=target)
        path = "/api/method/enterprise_base.duties.audit.record"
        try:
            client = Client(application, Response, use_cookies=False)
            self.assertEqual(client.get(path, headers=headers, query_string=body).status_code, 403)
            response = client.post(path, headers=headers, json=body)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["message"], {"ok": True})
        finally:
            if not getattr(frappe.local, "db", None):
                frappe.init(site=site)
                frappe.connect()
            frappe.set_user("Administrator")
            x4 = frappe.get_doc("User", X4)
            x4.enabled, x4.api_key, x4.api_secret = 0, None, None
            x4.save()
            frappe.db.delete(audit.DT, {"target": target})
            frappe.db.commit()
