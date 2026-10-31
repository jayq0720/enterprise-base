"""询盘中心：评分规则、进件去重和分配、负责人本人才能查改、开关权限、X4 只能 POST。"""

import json
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.app import application
from frappe.tests.utils import FrappeTestCase
from werkzeug.test import Client
from werkzeug.wrappers import Response

from enterprise_base.marketing import alerts, api, inquiry, scoring

X4 = "eb-x4@integration.invalid"
PHONE = "+86 199 0000 1234"


class TestScoring(FrappeTestCase):
    def test_grades_and_reasons(self):
        rich = {
            "requirement": "We need 500 pcs of stainless valves DN50 for a water project, "
            "delivery before December.",
            "email": "buyer@acme-industrial.example",
            "company": "Acme Industrial",
            "product": "Valve",
            "timeline": "2026-12",
            "phone": PHONE,
        }
        points, grade, reasons = scoring.score(rich)
        self.assertEqual(grade, "A")
        self.assertGreaterEqual(points, scoring.GRADE_A)
        self.assertIn("企业邮箱 +20", reasons)
        self.assertEqual(scoring.score({"requirement": "price?", "email": "x@gmail.com"})[1], "C")
        self.assertEqual(scoring.score({"requirement": "", "email": "x@gmail.com"})[1], "无效")
        spam = scoring.score({"requirement": "Cheap SEO service and backlinks", "company": "X"})
        self.assertEqual(spam[1], "无效")
        self.assertEqual(
            scoring.score({"requirement": "需要 20 台设备，请报价", "company": "某厂"})[1], "C"
        )

    def test_ai_engine(self):
        self.assertEqual(scoring.ai_engine("https://chatgpt.com/c/123"), "ChatGPT")
        self.assertEqual(scoring.ai_engine("https://www.perplexity.ai/search"), "Perplexity")
        self.assertEqual(scoring.ai_engine("", "doubao.com"), "豆包")
        self.assertEqual(scoring.ai_engine("", "deepseek"), "DeepSeek")
        self.assertEqual(scoring.ai_engine("https://www.baidu.com/s?wd=x", "baidu"), "")
        self.assertEqual(scoring.ai_engine("https://notchatgpt.com/"), "")


class TestInquiry(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        data = json.loads(Path(__file__).with_name("card_flow_data.json").read_text())
        for row in data["records"]:
            if not frappe.db.exists(row["doctype"], row.get("name")):
                frappe.get_doc(row).insert()
        tag = frappe.generate_hash(length=8)
        cls.on = frappe.get_doc(
            dict(doctype="Designation", designation_name="MK Sales " + tag, eb_tool_inquiry=1)
        ).insert()
        off = frappe.get_doc(
            dict(doctype="Designation", designation_name="MK Other " + tag)
        ).insert()
        cls.users, cls.employees = {}, {}
        for label, designation in (("s1", cls.on), ("s2", cls.on), ("other", off)):
            email = f"mk-{label}-{tag}@test.invalid"
            frappe.get_doc(
                {**data["user"], "email": email, "roles": [dict(role="Employee")]}
            ).insert()
            cls.users[label] = email
            cls.employees[label] = frappe.get_doc(
                {
                    **data["employee"],
                    "first_name": label,
                    "employee_number": tag + label,
                    "wecom_userid": tag + label,
                    "user_id": email,
                    "designation": designation.name,
                    "leave_approver": None,
                }
            ).insert()
        cls.tag = tag

    def setUp(self):
        frappe.set_user("Administrator")
        # 其他测试留下的岗位一律关掉“处理询盘”，分配结果只取决于本测试造的人
        frappe.db.sql("update `tabDesignation` set eb_tool_inquiry=0, eb_tool_inquiry_stats=0")
        frappe.db.set_value("Designation", self.on.name, "eb_tool_inquiry", 1)
        # 各测试互不影响：本测试的人名下询盘清零，负载从 0 开始
        frappe.db.sql(
            "update `tabLead` set lead_owner=null where lead_owner in %s",
            [tuple(self.users.values())],
        )
        for label in self.employees:
            self.employees[label].db_set("status", "Active")

    def tearDown(self):
        frappe.set_user("Administrator")

    def call(self, method, *args, **kwargs):
        frappe.set_user(X4)
        try:
            return method(*args, **kwargs)
        finally:
            frappe.set_user("Administrator")

    def uid(self, label):
        return self.employees[label].wecom_userid

    def intake(self, **extra):
        payload = {
            "name": "Alice",
            "company": "Acme",
            "email": f"alice-{frappe.generate_hash(length=6)}@acme.example",
            "phone": "+86 199 "
            + str(frappe.utils.random_string(8).encode().hex())[:8].translate(
                str.maketrans("abcdef", "123456")
            ),
            "requirement": "Need 200 pcs valves for project, please quote with delivery time.",
            "channel": "网站表单",
            "referrer": "https://chatgpt.com/",
            "utm_source": "chatgpt.com",
            **extra,
        }
        return self.call(api.intake, json.dumps(payload)), payload

    def test_switch_defaults_and_manager_only(self):
        meta = frappe.get_meta("Designation")
        for field in inquiry.SWITCH_FIELDS:
            df = meta.get_field(field)
            self.assertEqual((df.fieldtype, str(df.default)), ("Check", "0"))
        new = frappe.get_doc(
            dict(doctype="Designation", designation_name="MK Default " + self.tag)
        ).insert()
        self.assertEqual([new.get(f) for f in inquiry.SWITCH_FIELDS], [0, 0])
        frappe.set_user(self.users["s1"])
        doc = frappe.get_doc("Designation", new.name)
        doc.eb_tool_inquiry = 1
        with self.assertRaises(frappe.PermissionError):
            inquiry.validate_switches(doc)

    def test_intake_scores_assigns_by_load_and_merges_duplicates(self):
        first, payload = self.intake()
        self.assertTrue(first["ok"])
        self.assertFalse(first["duplicate"])
        self.assertTrue(first["assigned"])
        self.assertNotIn("owner", json.dumps(first))
        lead = frappe.get_doc("Lead", first["id"])
        self.assertEqual(
            (lead.eb_channel, lead.eb_ai_engine, lead.status), ("网站表单", "ChatGPT", "Lead")
        )
        self.assertIn(lead.eb_grade, ("A", "B"))
        # 没给国家时不沿用系统默认国家
        self.assertFalse(lead.country)
        self.assertTrue(lead.eb_score_reasons)
        owners = {lead.lead_owner}
        second, _ = self.intake()
        owners.add(frappe.db.get_value("Lead", second["id"], "lead_owner"))
        # 两个开通的人各分一条，没开通的人分不到
        self.assertEqual(owners, {self.users["s1"], self.users["s2"]})

        again = self.call(api.intake, {"email": payload["email"].upper(), "requirement": "follow"})
        self.assertEqual((again["id"], again["duplicate"]), (first["id"], True))
        lead.reload()
        self.assertIn("重复询盘", lead.notes[-1].note)

        spam, _ = self.intake(requirement="Cheap SEO service and backlinks for your site")
        self.assertEqual((spam["grade"], spam["assigned"]), ("无效", False))
        self.assertFalse(frappe.db.get_value("Lead", spam["id"], "lead_owner"))
        self.assertEqual(
            self.call(api.intake, {"name": "x"}), {"ok": False, "reason": "no_contact"}
        )
        self.assertEqual(self.call(api.intake, "not json"), {"ok": False, "reason": "invalid"})

    def test_unassigned_when_nobody_enabled(self):
        frappe.db.sql("update `tabDesignation` set eb_tool_inquiry=0, eb_tool_inquiry_stats=0")
        result, _ = self.intake(channel="unknown")
        self.assertFalse(result["assigned"])
        lead = frappe.get_doc("Lead", result["id"])
        self.assertFalse(lead.lead_owner)
        self.assertEqual(lead.eb_channel, "其他")

    def test_owner_only_contact_and_follow_up(self):
        result, payload = self.intake()
        name = result["id"]
        owner = frappe.db.get_value("Lead", name, "lead_owner")
        label = next(k for k, v in self.users.items() if v == owner)
        stranger = next(k for k in ("s1", "s2") if k != label)

        mine = self.call(api.my_inquiries, self.uid(label))
        self.assertIn(name, [r["name"] for r in mine["inquiries"]])
        theirs = self.call(api.my_inquiries, self.uid(stranger))
        self.assertNotIn(name, [r["name"] for r in theirs["inquiries"]])

        detail = self.call(api.inquiry, self.uid(label), name)
        self.assertTrue(detail["ok"])
        self.assertNotIn(payload["email"], json.dumps(detail))
        self.assertNotIn("1234", json.dumps(detail))
        contact = self.call(api.inquiry, self.uid(label), name, 1)
        self.assertEqual(contact["inquiry"]["email_id"], payload["email"])
        forbidden = {"ok": False, "reason": "forbidden"}
        for uid, lead in ((self.uid(stranger), name), (self.uid("other"), name), ("nobody", name)):
            self.assertEqual(self.call(api.inquiry, uid, lead, 1), forbidden)
            self.assertEqual(self.call(api.follow_up, uid, lead, "Replied", "x"), forbidden)
        self.assertEqual(self.call(api.inquiry, self.uid(label), "CRM-LEAD-NONE"), forbidden)

        invalid = {"ok": False, "reason": "invalid"}
        self.assertEqual(self.call(api.follow_up, self.uid(label), name, "Converted", ""), invalid)
        self.assertEqual(self.call(api.follow_up, self.uid(label), name, "", ""), invalid)
        done = self.call(api.follow_up, self.uid(label), name, "Replied", "已发报价 <b>草稿</b>")
        self.assertEqual(done, {"ok": True, "status": "Replied"})
        lead = frappe.get_doc("Lead", name)
        self.assertEqual(lead.modified_by, owner)
        self.assertTrue(lead.eb_first_response_at)
        self.assertEqual(lead.notes[-1].added_by, owner)
        self.assertNotIn("<b>", lead.notes[-1].note)
        detail = self.call(api.inquiry, self.uid(label), name)
        self.assertIn("已发报价", detail["notes"][0]["note"])

        # 员工离职后不再能查自己名下的询盘
        self.employees[label].db_set("status", "Left")
        self.assertEqual(self.call(api.inquiry, self.uid(label), name), forbidden)

    def test_stats_without_contacts(self):
        _, payload = self.intake()
        result = self.call(api.stats, self.uid("other"), 7)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["total"], 1)
        self.assertGreaterEqual(result["by_ai_engine"].get("ChatGPT", 0), 1)
        self.assertNotIn(payload["email"], json.dumps(result))
        self.assertEqual(self.call(api.stats, "nobody"), {"ok": False, "reason": "forbidden"})

    def test_only_x4_can_call(self):
        for user in ("Administrator", self.users["s1"]):
            frappe.set_user(user)
            for method, args in (
                (api.intake, ({"email": "a@b.example"},)),
                (api.my_inquiries, (self.uid("s1"),)),
                (api.stats, (self.uid("s1"),)),
            ):
                with self.assertRaises(frappe.PermissionError):
                    method(*args)
        frappe.set_user("Administrator")

    def test_x4_http_post_only_and_no_lead_rest(self):
        x4 = frappe.get_doc("User", X4)
        x4.enabled, x4.api_key, x4.api_secret = 1, "synthetic-mk-x4", "synthetic-mk-secret"
        x4.save()
        frappe.db.commit()
        site = frappe.local.site
        headers = {"Host": site, "Authorization": "token synthetic-mk-x4:synthetic-mk-secret"}
        path = "/api/method/enterprise_base.marketing.api.intake"
        body = {"data": {"email": f"http-{self.tag}@acme.example", "requirement": "need quote"}}
        created = None
        try:
            client = Client(application, Response, use_cookies=False)
            self.assertEqual(
                client.get(path, headers=headers, query_string={"data": "{}"}).status_code, 403
            )
            with patch.object(frappe, "enqueue"):
                response = client.post(path, headers=headers, json=body)
            self.assertEqual(response.status_code, 200)
            created = response.json["message"]["id"]
            for target in ("/api/resource/Lead", "/api/resource/Lead/" + created):
                self.assertEqual(client.get(target, headers=headers).status_code, 403)
            fields = '["name","eb_tool_inquiry","eb_tool_inquiry_stats"]'
            designation = self.employees["s1"].designation
            ok = client.get(
                "/api/resource/Designation",
                headers=headers,
                query_string={
                    "fields": fields,
                    "filters": json.dumps([["name", "=", designation]]),
                },
            )
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json["data"][0]["eb_tool_inquiry"], 1)
        finally:
            if not getattr(frappe.local, "db", None):
                frappe.init(site=site)
                frappe.connect()
            frappe.set_user("Administrator")
            x4 = frappe.get_doc("User", X4)
            x4.enabled, x4.api_key, x4.api_secret = 0, None, None
            x4.save()
            if created:
                frappe.delete_doc("Lead", created, force=1, ignore_permissions=True)
            frappe.db.commit()

    def cards(self, **alerts):
        conf = {"eb_wecom_cards": {"enabled": True, "desk_url": "https://hr.example.com"}}
        if alerts:
            conf["eb_marketing_alerts"] = alerts
        return patch.dict(frappe.local.conf, conf)

    @staticmethod
    def recorder(sent):
        def request(path, body):
            sent.append(body)
            return {"errcode": 0}

        return request

    def age(self, name, hours):
        when = frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-hours)
        frappe.db.sql("update `tabLead` set creation=%s where name=%s", (when, name))

    def test_alerts_new_overdue_and_escalate(self):
        sent: list[dict] = []
        with (
            self.cards(),
            patch(
                "enterprise_base.wecom.card_api.request",
                side_effect=self.recorder(sent),
            ),
            patch("frappe.enqueue", side_effect=lambda m, **kw: alerts.notify_new(kw["lead"])),
        ):
            result, payload = self.intake()
            name = result["id"]
            self.assertEqual(result["grade"], "A")
            owner = frappe.db.get_value("Lead", name, "lead_owner")
            label = next(k for k, v in self.users.items() if v == owner)
            mine = [b for b in sent if name in json.dumps(b, ensure_ascii=False)]
            self.assertEqual(len(mine), 1)
            card = mine[0]
            self.assertEqual((card["touser"], card["msgtype"]), (self.uid(label), "textcard"))
            self.assertEqual(card["textcard"]["url"], "https://portal.example.com/#inquiry=" + name)
            text = json.dumps(card, ensure_ascii=False)
            for secret in (payload["email"], payload["phone"], "Alice", "Acme"):
                self.assertNotIn(secret, text)
            self.assertEqual(frappe.db.get_value("Lead", name, "eb_alert_level"), 1)

            # 超过 4 小时没跟进：提醒负责人；超过 8 小时：提醒开通“查询盘统计”的市场负责人
            frappe.db.set_value("Designation", self.on.name, "eb_tool_inquiry_stats", 1)
            sent.clear()
            self.age(name, 5)
            alerts.overdue()
            mine = [b for b in sent if name in json.dumps(b, ensure_ascii=False)]
            self.assertEqual([b["touser"] for b in mine], [self.uid(label)])
            self.assertIn("询盘待跟进", mine[0]["textcard"]["title"])
            sent.clear()
            self.age(name, 9)
            alerts.overdue()
            mine = [b for b in sent if name in json.dumps(b, ensure_ascii=False)]
            self.assertEqual(sorted(b["touser"] for b in mine), [self.uid("s1"), self.uid("s2")])
            self.assertEqual(frappe.db.get_value("Lead", name, "eb_alert_level"), 3)
            sent.clear()
            alerts.overdue()
            self.assertFalse([b for b in sent if name in json.dumps(b, ensure_ascii=False)])

            # 跟进过的不再提醒；负责人离职的不发
            second, _ = self.intake()
            other = frappe.db.get_value("Lead", second["id"], "lead_owner")
            self.call(
                api.follow_up,
                self.uid(next(k for k, v in self.users.items() if v == other)),
                second["id"],
                "Replied",
                "",
            )
            self.age(second["id"], 5)
            sent.clear()
            alerts.overdue()
            self.assertFalse([b for b in sent if second["id"] in json.dumps(b, ensure_ascii=False)])
            frappe.db.set_value("Designation", self.on.name, "eb_tool_inquiry_stats", 0)

    def test_alerts_off_and_left_owner(self):
        sent: list[dict] = []
        request = patch(
            "enterprise_base.wecom.card_api.request",
            side_effect=self.recorder(sent),
        )
        with request, patch("frappe.enqueue") as enqueue:
            self.intake()  # 卡片没开：不排队、不发
            with self.cards(enabled=False):
                self.intake()
            enqueue.assert_not_called()
        with self.cards(sla_hours={"B": 2, "A": 1}), request:
            result, _ = self.intake()
            owner = frappe.db.get_value("Lead", result["id"], "lead_owner")
            label = next(k for k, v in self.users.items() if v == owner)
            self.employees[label].db_set("status", "Left")
            self.age(result["id"], 1.5)
            alerts.overdue()
            self.assertFalse([b for b in sent if result["id"] in json.dumps(b, ensure_ascii=False)])
            self.assertEqual(alerts.settings()["sla_hours"], {"A": 1, "B": 2, "C": 24})

    def test_email_inquiry(self):
        from email.mime.text import MIMEText

        from frappe.email.receive import InboundMail

        address = f"buyer-{self.tag}@weber-haus.example"
        mail = MIMEText(
            "<p>Hello, we need <b>300 pcs</b> ball valves DN50 for a project in Germany, "
            "delivery before December. Please quote.</p>",
            "html",
        )
        mail["Subject"], mail["From"] = "RFQ ball valves " + self.tag, f"Markus <{address}>"
        mail["To"], mail["Message-ID"] = "sales@ours.example", f"<{self.tag}@weber-haus.example>"
        account = frappe._dict(
            name=None, email_id="sales@ours.example", append_to="Lead", use_imap=0
        )
        with patch("frappe.enqueue"):
            communication = InboundMail(mail.as_bytes(), account).process()
        self.assertEqual(communication.reference_doctype, "Lead")
        lead = frappe.get_doc("Lead", communication.reference_name)
        self.assertEqual((lead.eb_channel, lead.email_id), ("邮件", address))
        self.assertIn("300 pcs", lead.eb_requirement)
        self.assertNotIn("<b>", lead.eb_requirement)
        self.assertIn(lead.eb_grade, ("A", "B"))
        self.assertIn(lead.lead_owner, (self.users["s1"], self.users["s2"]))
        self.assertFalse(lead.country)

        # 客户回复同一询盘：留在时间线里，不改需求和负责人
        reply = MIMEText("Any update?", "plain")
        reply["Subject"], reply["From"] = "Re: RFQ ball valves " + self.tag, address
        reply["To"], reply["Message-ID"] = "sales@ours.example", f"<r{self.tag}@weber-haus.example>"
        reply["In-Reply-To"] = f"<{self.tag}@weber-haus.example>"
        with patch("frappe.enqueue"):
            InboundMail(reply.as_bytes(), account).process()
        again = frappe.get_doc("Lead", lead.name)
        self.assertEqual(
            (again.eb_requirement, again.lead_owner), (lead.eb_requirement, lead.lead_owner)
        )

    def test_funnel_by_channel_and_ai_engine(self):
        first, _ = self.intake()
        second, _ = self.intake(channel="展会", referrer="", utm_source="")
        self.intake(
            requirement="Cheap SEO backlinks service", channel="展会", referrer="", utm_source=""
        )
        customer = frappe._dict(name="MK Buyer " + self.tag)
        frappe.db.sql(
            "insert into `tabCustomer` (name, customer_name, lead_name) values (%s, %s, %s)",
            (customer.name, customer.name, first["id"]),
        )
        frappe.db.set_value("Lead", first["id"], "status", "Converted")
        # 销售订单走完整流程要建物料、仓库；这里直接写一条已提交订单，只验证统计口径
        for name, docstatus, amount in (
            ("MK-SO-1-" + self.tag, 1, 1200.5),
            ("MK-SO-2-" + self.tag, 2, 999),
        ):
            frappe.db.sql(
                "insert into `tabSales Order`"
                " (name, customer, docstatus, base_grand_total, company)"
                " values (%s, %s, %s, %s, %s)",
                (
                    name,
                    customer.name,
                    docstatus,
                    amount,
                    frappe.db.get_value("Company", {}, "name"),
                ),
            )
        result = self.call(api.stats, self.uid("other"), 1)
        funnel = result["funnel"]
        chat = funnel["by_ai_engine"]["ChatGPT"]
        self.assertGreaterEqual(chat["won"], 1)
        self.assertGreaterEqual(chat["revenue"], 1200.5)
        self.assertGreaterEqual(chat["opportunity"], 1)
        fair = funnel["by_channel"]["展会"]
        self.assertGreaterEqual(fair["total"] - fair["valid"], 1)
        self.assertEqual(funnel["all"]["total"], result["total"])
        self.assertNotIn(customer.name, json.dumps(result, ensure_ascii=False))
        company = frappe.db.get_value("Company", {}, "name")
        self.assertEqual(
            result["currency"], frappe.db.get_value("Company", company, "default_currency")
        )
