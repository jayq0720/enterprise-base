"""真实 Frappe 会话 + 合成企业微信响应；不调用外部企业。"""

import json
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import frappe
import requests
from frappe.app import application
from frappe.tests.utils import FrappeTestCase
from werkzeug.test import Client
from werkzeug.wrappers import Response

from enterprise_base.wecom import login


class TestWecomLogin(FrappeTestCase):
    def test_native_session_and_rejections(self):
        site = frappe.local.site
        data = json.loads(Path(__file__).with_name("p2_data.json").read_text())
        for row in data["records"]:
            if not frappe.db.exists(row["doctype"], row.get("name")):
                frappe.get_doc(row).insert()
        email = "wecom-login@test.invalid"
        user = frappe.get_doc(
            {**data["user"], "email": email, "roles": [{"role": "Employee"}]}
        ).insert()
        employee = frappe.get_doc(
            {
                **data["employee"],
                "first_name": "扫码合成测试",
                "employee_number": "WECOM-LOGIN",
                "user_id": email,
                "wecom_userid": "synthetic-member",
                "leave_approver": None,
                "create_user_permission": 0,
            }
        ).insert()
        frappe.db.commit()
        cfg = dict(
            enabled=True,
            corpid="synthetic-corp",
            agentid="1000001",
            app_secret="synthetic-secret",
            callback_url="https://" + site + login.CALLBACK,
        )
        client = Client(application, Response)

        def restore():
            frappe.init(site=site)
            frappe.connect()
            frappe.set_user("Administrator")

        def get(path, browser=client, scheme="https", **kwargs):
            response = browser.get(path, base_url=scheme + "://" + site, **kwargs)
            restore()
            return response

        def start():
            response = get("/api/method/enterprise_base.wecom.login.start")
            self.assertEqual(response.status_code, 302)
            cookie = next(c for c in response.headers.getlist("Set-Cookie") if login.COOKIE in c)
            for attribute in ("HttpOnly", "Secure", "SameSite=Lax", "Max-Age=300"):
                self.assertIn(attribute, cookie)
            state = parse_qs(urlsplit(response.location).query)["state"][0]
            self.assertGreater(frappe.cache.ttl(login._key(state)), 0)
            return state

        def callback(state, expected="/login?wecom=failed", browser=client):
            response = get(login.CALLBACK, browser, query_string={"state": state, "code": "test"})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.location, expected)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
            for value in data["private_samples"] + ["synthetic-secret", "synthetic-token"]:
                self.assertNotIn(value.encode(), response.data)

        def upstream(_url, **kwargs):
            self.assertFalse(kwargs["allow_redirects"])
            self.assertEqual(kwargs["timeout"], (3.05, 10))
            body = (
                {"access_token": "synthetic-token"}
                if _url.endswith("gettoken")
                else {"errcode": 0, "userid": "synthetic-member"}
            )
            return Mock(status_code=200, json=lambda: body)

        try:
            with (
                patch.object(login, "_settings", return_value=cfg),
                patch.object(login.requests, "get", side_effect=upstream) as send,
            ):
                # 生产经反向代理到 gunicorn 时 scheme 为 http，入口仍须可用。
                response = get("/api/method/enterprise_base.wecom.login.start", scheme="http")
                self.assertTrue(response.location.startswith("https://login.work.weixin.qq.com/"))
                state = start()
                callback(state, browser=Client(application, Response))
                send.assert_not_called()
                callback(state, "/app")
                response = get("/api/method/frappe.auth.get_logged_user")
                self.assertEqual(response.json["message"], email)
                client.delete_cookie("sid", domain=site)
                send.reset_mock()
                callback(state)
                send.assert_not_called()
                state = start()
                frappe.cache.delete(login._key(state))
                callback(state)
                send.assert_not_called()
                for field, value in (
                    ("status", "Left"),
                    ("wecom_userid", "another-member"),
                    ("user_id", None),
                ):
                    old = employee.get(field)
                    employee.db_set(field, value, commit=True)
                    callback(start())
                    employee.db_set(field, old, commit=True)
                user.db_set("enabled", 0, commit=True)
                callback(start())
                user.db_set("enabled", 1, commit=True)
                for roles in (
                    [],
                    ["Employee", "EB X4"],
                    ["Employee", "EB Integration"],
                    ["Employee", "System Manager"],
                ):
                    with patch.object(frappe, "get_roles", return_value=roles):
                        self.assertIsNone(login._employee_user("synthetic-member"))
                with patch.object(login, "_get", return_value={"openid": "external"}):
                    callback(start())
                send.side_effect = requests.Timeout("synthetic-secret")
                callback(start())
        finally:
            restore()
            frappe.db.delete("Employee", {"name": employee.name})
            frappe.delete_doc("User", email, ignore_permissions=True, force=True)
            frappe.db.commit()

    def test_disabled_and_invalid_settings(self):
        for cfg in (None, {}, {"enabled": True}, {"enabled": False}):
            with patch.dict(frappe.conf, eb_wecom_login=cfg):
                self.assertIsNone(login._settings())
        for url in (
            "http://hr.example.com" + login.CALLBACK,
            "https://hr.example.com/other",
            "https://hr.example.com" + login.CALLBACK + "?x=1",
        ):
            with patch.dict(
                frappe.conf,
                eb_wecom_login=dict(
                    enabled=True,
                    corpid="synthetic",
                    agentid="1",
                    app_secret="synthetic",
                    callback_url=url,
                ),
            ):
                self.assertIsNone(login._settings())
        for response in (
            Mock(status_code=302),
            Mock(status_code=500),
            Mock(status_code=200, json=lambda: {"errcode": 40029}),
            Mock(status_code=200, json=lambda: []),
        ):
            with patch.object(login.requests, "get", return_value=response):
                self.assertEqual(login._get("gettoken", {}), {})
