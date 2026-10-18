"""原生工作流事件与卡片记录，HTTP 为合成替代，不发送真实消息。"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests.utils import FrappeTestCase

from enterprise_base.tests.card_support import records
from enterprise_base.wecom import card_delivery as delivery


class TestCardDelivery(FrappeTestCase):
    def test_delivery_backend_completion_and_retries(self):
        frappe.set_user("Administrator")
        data, users, employees = records()
        cfg = dict(enabled=True, desk_url="https://hr.synthetic.invalid")
        with (
            patch.dict(frappe.conf, eb_wecom_cards=cfg),
            patch.object(frappe, "enqueue"),
            patch.object(
                delivery, "api", return_value=dict(response_code="synthetic-code")
            ) as send,
            patch.object(frappe, "logger") as logger,
        ):
            doc = frappe.get_doc(
                {
                    "doctype": "Leave Application",
                    **data["leave"],
                    "employee": employees["staff"].name,
                    "leave_approver": users["manager"],
                    "eb_hr_approver": users["hr"],
                }
            ).insert()
            first = frappe.get_doc(delivery.DT, {"leave_application": doc.name})
            delivery.on_update(doc)
            self.assertEqual(frappe.db.count(delivery.DT, {"leave_application": doc.name}), 1)
            delivery.process(first.name)
            first.reload()
            self.assertEqual(first.status, "Sent")
            count = send.call_count
            delivery.process(first.name)
            self.assertEqual(send.call_count, count)
            for value in data["private_samples"]:
                self.assertNotIn(value, str(send.call_args))
            frappe.set_user(users["manager"])
            doc = apply_workflow(doc.as_json(), "EB Manager Approve")
            first.reload()
            self.assertEqual((first.status, first.handled_by), ("Done", users["manager"]))
            second = frappe.get_doc(
                delivery.DT, {"leave_application": doc.name, "stage": "EB Leave HR"}
            )
            frappe.enqueue.reset_mock()
            delivery.retry()
            self.assertEqual(
                {c.kwargs["card"] for c in frappe.enqueue.call_args_list}, {first.name, second.name}
            )
            delivery.process(first.name)
            first.reload()
            self.assertEqual(first.update_status, "Done")
            self.assertEqual(send.call_args.args[0], "message/update_template_card")
            send.side_effect = ValueError("synthetic-private-error")
            delivery.process(second.name)
            second.reload()
            self.assertEqual((second.status, second.attempts), ("Failed", 1))
            send.side_effect = None
            delivery.process(second.name)
            second.reload()
            self.assertEqual(second.status, "Sent")
            frappe.set_user(users["hr"])
            apply_workflow(doc.as_json(), "EB HR Approve")
            second.reload()
            second.db_set("sent_at", (datetime.now(UTC) - timedelta(hours=73)).isoformat())
            delivery.process(second.name)
            second.reload()
            self.assertEqual(second.update_status, "Expired")
            self.assertNotIn("synthetic-private-error", str(logger.mock_calls))
            for value in data["private_samples"]:
                self.assertNotIn(value, str(logger.mock_calls))
