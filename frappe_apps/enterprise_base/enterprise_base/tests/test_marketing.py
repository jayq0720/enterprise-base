"""询盘中心：评分规则、进件去重和分配、负责人本人才能查改、开关权限、X4 只能 POST。"""

import json
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.app import application
from frappe.tests.utils import FrappeTestCase
from werkzeug.test import Client
from werkzeug.wrappers import Response

from enterprise_base.marketing import api, inquiry, scoring

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
        frappe.db.sql("update `tabDesignation` set eb_tool_inquiry=0")
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
        frappe.db.sql("update `tabDesignation` set eb_tool_inquiry=0")
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
