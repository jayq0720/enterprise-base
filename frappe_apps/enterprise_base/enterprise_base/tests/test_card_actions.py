"""真实原生 Workflow/权限；事件是合成数据，尚不代表企业微信验签联测。"""

from unittest.mock import patch

import frappe
from frappe.app import application
from frappe.tests.utils import FrappeTestCase
from werkzeug.test import Client
from werkzeug.wrappers import Response

from enterprise_base.tests.card_support import records
from enterprise_base.wecom import card_actions as actions


class TestCardActions(FrappeTestCase):
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
        self.cfg = dict(enabled=True, corpid="synthetic-corp", agentid="1001")

    def card(self, label="manager", stage="EB Leave Manager"):
        return frappe.get_doc(
            dict(
                doctype=actions.DT,
                task_id=frappe.generate_hash(length=32),
                leave_application=self.doc.name,
                stage=stage,
                recipient=self.users[label],
                userid=self.employees[label].wecom_userid,
                status="Sent",
            )
        ).insert(ignore_permissions=True)

    def event(self, card, label="manager", action="approve"):
        return dict(
            ToUserName=self.cfg["corpid"],
            AgentID=self.cfg["agentid"],
            FromUserName=self.employees[label].wecom_userid,
            MsgType="event",
            Event="template_card_event",
            CardType="button_interaction",
            EventKey=action,
            TaskId=card.name,
            ResponseCode="synthetic-action-code",
        )

    def test_actual_actor_two_stages_and_duplicate(self):
        card = self.card()
        with (
            patch.dict(frappe.conf, eb_wecom_cards=self.cfg),
            patch.object(frappe, "enqueue"),
            patch.object(actions, "apply_workflow", wraps=actions.apply_workflow) as apply,
        ):
            self.assertFalse(actions.apply(self.event(card, "staff")))
            self.assertTrue(actions.apply(self.event(card)))
            self.doc.reload()
            self.assertEqual(self.doc.eb_leave_state, "EB Leave HR")
            self.assertEqual(self.doc.modified_by, self.users["manager"])
            self.assertTrue(actions.apply(self.event(card)))
            self.assertEqual(apply.call_count, 1)
            hr_card = self.card("hr", "EB Leave HR")
            self.assertTrue(actions.apply(self.event(hr_card, "hr")))
            self.doc.reload()
            self.assertEqual((self.doc.status, self.doc.docstatus), ("Approved", 1))
            self.assertEqual(self.doc.modified_by, self.users["hr"])
            self.assertEqual(frappe.session.user, "Administrator")

    def test_unknown_left_stale_and_return(self):
        card = self.card()
        with (
            patch.dict(frappe.conf, eb_wecom_cards=self.cfg),
            patch.object(frappe, "enqueue"),
            patch.object(frappe, "logger") as logger,
        ):
            self.assertFalse(actions.apply({**self.event(card), "FromUserName": "unknown"}))
            self.employees["manager"].db_set("status", "Left")
            self.assertFalse(actions.apply(self.event(card)))
            self.employees["manager"].db_set("status", "Active")
            stale = self.card("hr", "EB Leave HR")
            self.assertFalse(actions.apply(self.event(stale, "hr")))
            self.assertTrue(actions.apply(self.event(card, action="return")))
            self.doc.reload()
            self.assertEqual(self.doc.eb_leave_state, "EB Leave Returned")
            for value in [*self.data["private_samples"], "synthetic-action-code"]:
                self.assertNotIn(value, str(logger.mock_calls))

    def test_self_approval_and_public_method_denied(self):
        user = frappe.get_doc("User", self.users["staff"])
        user.add_roles("Leave Approver")
        self.doc.db_set("leave_approver", user.name)
        card = self.card("staff")
        with patch.dict(frappe.conf, eb_wecom_cards=self.cfg), patch.object(frappe, "enqueue"):
            self.assertFalse(actions.apply(self.event(card, "staff")))
        site = frappe.local.site
        frappe.db.commit()
        response = Client(application, Response, use_cookies=False).post(
            "/api/method/enterprise_base.wecom.card_actions.apply",
            headers={"Host": site},
            json={"event": self.event(card)},
        )
        self.assertEqual(response.status_code, 403)
        if not getattr(frappe.local, "db", None):
            frappe.init(site=site)
            frappe.connect()
        frappe.set_user("Administrator")
