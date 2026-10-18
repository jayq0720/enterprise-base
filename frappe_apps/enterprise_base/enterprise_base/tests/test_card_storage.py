"""卡片底座的原生权限/加密存储与合成 HTTP 回归；不发送真实消息。"""

from unittest.mock import Mock, patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase

from enterprise_base.wecom import card_api


class TestCardStorage(FrappeTestCase):
    def test_encrypted_code_and_permission(self):
        frappe.set_user("Administrator")
        secret = "synthetic-card-response-code"
        doc = frappe.get_doc(
            dict(
                doctype="EB Approval Card",
                task_id=frappe.generate_hash(length=32),
                leave_application="synthetic-leave",
                stage="EB Leave Manager",
                recipient="Administrator",
                userid="synthetic",
                sent_at="2026-10-18T00:00:00+00:00",
            )
        )
        doc.response_code = secret
        doc.insert(ignore_links=True)
        self.assertNotIn(secret, str(frappe.db.get_value(doc.doctype, doc.name, "*", as_dict=True)))
        doc.reload()
        self.assertEqual(doc.get_password("response_code"), secret)
        self.assertEqual(doc.sent_at, "2026-10-18T00:00:00+00:00")
        self.assertEqual(doc.update_status, "Pending")
        self.assertFalse(frappe.has_permission(doc=doc, user="Guest"))
        self.assertFalse(frappe.has_permission(doc=doc, user="eb-x4@integration.invalid"))

    def test_api_uses_fixed_url_and_rejects_failures(self):
        cfg = dict(
            enabled=True,
            corpid="synthetic-" + frappe.generate_hash(length=8),
            agentid="1001",
            app_secret="synthetic-secret",
        )
        with (
            patch.dict(frappe.conf, eb_wecom_cards=cfg),
            patch.object(
                card_api, "_get", return_value=dict(access_token="synthetic-token", expires_in=7200)
            ),
            patch.object(card_api.requests, "post") as post,
        ):
            post.return_value = Mock(
                status_code=200,
                json=Mock(return_value=dict(errcode=0, response_code="synthetic-code")),
            )
            self.assertEqual(
                card_api.request("message/send", {"touser": "synthetic"})["response_code"],
                "synthetic-code",
            )
            self.assertEqual(
                post.call_args.args[0], "https://qyapi.weixin.qq.com/cgi-bin/message/send"
            )
            self.assertFalse(post.call_args.kwargs["allow_redirects"])
            for response in (
                Mock(status_code=302),
                Mock(
                    status_code=200,
                    json=Mock(return_value=dict(errcode=0, invaliduser="synthetic")),
                ),
                Mock(status_code=200, json=Mock(return_value=dict(errcode=40014))),
            ):
                post.return_value = response
                with self.assertRaises(card_api.CardAPIError):
                    card_api.request("message/send", {})
            post.side_effect = requests.Timeout("synthetic-private-exception")
            with self.assertRaises(card_api.CardAPIError) as error:
                card_api.request("message/send", {})
            self.assertEqual(str(error.exception), "企业微信卡片接口调用失败。")
            with self.assertRaises(card_api.CardAPIError):
                card_api.request("user/delete", {})
