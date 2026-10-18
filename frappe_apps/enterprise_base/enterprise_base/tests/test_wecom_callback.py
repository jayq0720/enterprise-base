"""真实 HTTP 与签名回归；桥接/动作含替代对象，非企业微信联测。"""

import base64
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.app import application
from frappe.tests.utils import FrappeTestCase
from werkzeug.test import Client
from werkzeug.wrappers import Response

from enterprise_base.wecom import callback, crypto


class TestWecomCallback(FrappeTestCase):
    def test_http_guards_routing_and_retries(self):
        site = frappe.local.site
        cfg = dict(
            enabled=True,
            corpid="synthetic-corp",
            agentid="1001",
            token="synthetic-token",
            aes_key="synthetic-test-key",
        )
        # 每次请求会重新 init；在 guard 接缝写入测试专用配置。
        original_guard = callback.guard

        def guard():
            frappe.conf.eb_wecom_cards = cfg
            original_guard()

        event = dict(
            ToUserName=cfg["corpid"],
            AgentID=cfg["agentid"],
            MsgType="text",
            FromUserName="synthetic",
            MsgId=frappe.generate_hash(length=8),
        )
        codec = SimpleNamespace(
            decrypt=Mock(return_value=b"message"),
            xml_fields=lambda body: {"Encrypt": "synthetic-cipher"} if body == b"<xml/>" else event,
        )
        client = Client(application, Response, use_cookies=False)
        args = dict(msg_signature="synthetic-signature", timestamp="1", nonce="synthetic")

        def request(query=None, method="POST", **kwargs):
            return client.open(
                callback.PATH,
                method=method,
                query_string=args if query is None else query,
                headers={"Host": site},
                content_type="application/xml",
                data=b"<xml/>" if method == "POST" else None,
                **kwargs,
            )

        with (
            patch.object(callback, "guard", side_effect=guard),
            patch.object(callback, "protocol", return_value=codec),
            patch.object(callback, "approve", return_value=True) as approve,
            patch.object(callback, "forward", return_value=b"success") as forward,
            patch.object(frappe, "logger") as logger,
        ):
            disabled = cfg.pop("enabled")
            self.assertEqual(request().status_code, 503)
            cfg["enabled"] = disabled
            for path in (
                callback.PATH + "/",
                callback.PATH.replace("/api/", "/api/v1/"),
                callback.PATH.replace("/api/", "/api/v2/"),
                "/",
            ):
                response = client.post(
                    path,
                    query_string={**args, "cmd": callback.PATH.rsplit("/", 1)[1]},
                    headers={"Host": site, "Cookie": "sid=synthetic"},
                    data=b"<xml/>",
                    content_type="application/xml",
                )
                self.assertEqual(response.status_code, 404)
            codec.decrypt.assert_not_called()
            for query in (
                {**args, "cmd": "frappe.auth.get_logged_user"},
                [(k, v) for k, v in args.items()] + [("nonce", "duplicate")],
                {k: v for k, v in args.items() if k != "nonce"},
            ):
                self.assertEqual(request(query).status_code, 400)
            codec.decrypt.side_effect = ValueError("synthetic-private-error")
            self.assertEqual(request().status_code, 403)
            self.assertFalse(forward.called)
            codec.decrypt.side_effect = None
            self.assertEqual(request({**args, "echostr": "synthetic"}, "GET").data, b"message")
            self.assertEqual(request().data, b"success")
            self.assertEqual(request().data, b"success")
            self.assertEqual(forward.call_count, 1)
            self.assertEqual(forward.call_args.args[:2], (b"<xml/>", args))
            event["AgentID"] = "other-app"
            self.assertEqual(request().status_code, 403)
            event["AgentID"] = cfg["agentid"]
            event.update(MsgType="event", Event="template_card_event")
            self.assertEqual(request().data, b"success")
            self.assertEqual(approve.call_args.args[0], event)
            approve.return_value = False
            self.assertEqual(request().status_code, 403)
            self.assertEqual(forward.call_count, 1)
            event.pop("Event")
            event["MsgId"] = frappe.generate_hash(length=8)
            forward.return_value = None
            self.assertEqual(request().status_code, 502)
            self.assertEqual(request().status_code, 503)
            self.assertEqual(forward.call_count, 2)
            self.assertNotIn("synthetic-private-error", str(logger.mock_calls))
        if not getattr(frappe.local, "db", None):
            frappe.init(site=site)
            frappe.connect()
        frappe.set_user("Administrator")

    def test_real_signature_http(self):
        self.assertIs(callback.protocol(), crypto)
        site = frappe.local.site
        cfg = dict(
            corpid="synthetic-corp",
            agentid="1001",
            token="synthetic-token",
            aes_key=base64.b64encode(bytes(range(32))).decode().rstrip("="),
        )
        original_guard = callback.guard

        def guard():
            frappe.conf.eb_wecom_cards = dict(cfg, enabled=True)
            original_guard()

        client = Client(application, Response, use_cookies=False)

        def request(message, method="POST", wrong_corp=False, **changes):
            outer = crypto.encrypt(
                message,
                token=cfg["token"],
                key=cfg["aes_key"],
                corpid="wrong" if wrong_corp else cfg["corpid"],
            )
            fields = crypto.xml_fields(outer)
            args = dict(
                msg_signature=fields["MsgSignature"],
                timestamp=fields["TimeStamp"],
                nonce=fields["Nonce"],
            )
            if method == "GET":
                args["echostr"] = fields["Encrypt"]
            args.update(changes)
            if "timestamp" in changes:
                args["msg_signature"] = crypto.signature(
                    cfg["token"], args["timestamp"], args["nonce"], fields["Encrypt"]
                )
            return client.open(
                callback.PATH,
                method=method,
                query_string=args,
                headers={"Host": site},
                content_type="application/xml",
                data=outer if method == "POST" else None,
            )

        with (
            patch.object(callback, "guard", side_effect=guard),
            patch.object(callback, "forward", return_value=b"success") as forward,
        ):
            self.assertEqual(request(b"synthetic-echo", "GET").data, b"synthetic-echo")
            message = (
                b"<xml><ToUserName>synthetic-corp</ToUserName><AgentID>1001</AgentID>"
                b"<MsgType>text</MsgType><Content><![CDATA[look <!-- <!DOCTYPE html> here]]>"
                b"</Content></xml>"
            )
            self.assertEqual(request(message).data, b"success")
            self.assertEqual(forward.call_count, 1)
            for changes in (
                dict(msg_signature="0" * 40),
                dict(timestamp=str(int(time.time()) - 301)),
                dict(wrong_corp=True),
            ):
                self.assertEqual(request(message, **changes).status_code, 403)
            self.assertEqual(forward.call_count, 1)
        print("test_real_signature_http：GET/POST、签名时效/CorpID/CDATA及越权拒绝通过。")
        if not getattr(frappe.local, "db", None):
            frappe.init(site=site)
            frappe.connect()
        frappe.set_user("Administrator")

    def test_bridge_fixed_target_and_filtered_response(self):
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        response = Mock(status_code=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [b"success"]
        session.post.return_value = response
        codec = Mock()
        with patch.object(callback.requests, "Session", return_value=session):
            self.assertEqual(callback.forward(b"cipher", {"nonce": "synthetic"}, codec), b"success")
            self.assertEqual(session.post.call_args.args[0], "http://wechat:9898/wxcomapp")
            self.assertFalse(session.trust_env)
            self.assertFalse(session.post.call_args.kwargs["allow_redirects"])
            response.status_code = 302
            self.assertIsNone(callback.forward(b"cipher", {}, codec))
            response.status_code = 200
            response.iter_content.return_value = [b"private-error"]
            codec.xml_fields.return_value = {"error": "private-error"}
            self.assertIsNone(callback.forward(b"cipher", {}, codec))
            response.iter_content.return_value = [b"x" * (callback.LIMIT + 1)]
            self.assertIsNone(callback.forward(b"cipher", {}, codec))
