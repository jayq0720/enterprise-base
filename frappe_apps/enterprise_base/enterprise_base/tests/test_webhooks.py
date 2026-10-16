"""验证原生 Webhook 的字段、最小载荷和真实签名，不发网络请求。"""

import base64
import hashlib
import hmac
import os

import frappe
from frappe.integrations.doctype.webhook.webhook import get_webhook_data, get_webhook_headers
from frappe.tests.utils import FrappeTestCase


class TestWebhooks(FrappeTestCase):
    def test_native_signed_payload(self):
        page = frappe.new_doc("Wiki Document")
        page.name = "EB-Webhook-Probe"
        payload = {"doctype": "Wiki Document", "name": page.name}
        for name, event in (("EB Wiki Update", "on_update"), ("EB Wiki Trash", "on_trash")):
            hook = frappe.get_doc("Webhook", name)
            self.assertEqual(hook.webhook_docevent, event)
            self.assertTrue(hook.enabled and hook.enable_security)
            self.assertEqual(hook.request_url, "http://policy-sync:8080/v1/hooks/wiki")
            self.assertEqual(hook.request_method, "POST")
            self.assertEqual(get_webhook_data(page, hook), payload)
            headers = get_webhook_headers(page, hook, payload)
            self.assertEqual(set(headers), {"X-Frappe-Webhook-Signature"})
            expected = base64.b64encode(
                hmac.new(
                    os.environ["EB_SYNC_WEBHOOK_SECRET"].encode(),
                    frappe.as_json(payload).encode(),
                    hashlib.sha256,
                ).digest()
            )
            self.assertTrue(hmac.compare_digest(headers["X-Frappe-Webhook-Signature"], expected))
