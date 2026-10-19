"""正式字段/旧数据升级、HR 权限及 X4 精确只读 REST 回归。"""

import json
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.app import application
from frappe.tests.utils import FrappeTestCase
from werkzeug.test import Client
from werkzeug.wrappers import Response

from enterprise_base.duties import tool_switches as switches


class TestToolSwitches(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")

    def test_schema_defaults_and_repeat_migration(self):
        meta = frappe.get_meta("Designation")
        section = meta.get_field("eb_assistant_tools_section")
        self.assertEqual(
            (section.label, section.insert_after), ("企业微信助手", "eb_responsibilities")
        )
        for field in switches.FIELDS:
            df = meta.get_field(field)
            self.assertEqual((df.fieldtype, str(df.default), df.permlevel), ("Check", "1", 0))
            self.assertIn("HR Manager", df.read_only_depends_on)
        cases = []
        for value in (0, 1):
            doc = frappe.get_doc(
                dict(
                    doctype="Designation",
                    designation_name="G3 Repeat " + frappe.generate_hash(length=8),
                    **dict.fromkeys(switches.FIELDS, value),
                )
            ).insert()
            cases.append((doc.name, value))
        for _ in range(2):
            for name, expected in cases:
                self.assertEqual(
                    tuple(frappe.db.get_value("Designation", name, list(switches.FIELDS))),
                    (expected,) * 4,
                )
            switches.configure()
            for handler in frappe.get_hooks("after_migrate"):
                if handler == "enterprise_base.duties.tool_switches.backfill":
                    frappe.get_attr(handler)()
                    break
            else:
                self.fail("缺少 after_migrate 补空值钩子。")
        doc = frappe.get_doc(
            dict(doctype="Designation", designation_name="G3 New " + frappe.generate_hash(length=8))
        ).insert()
        self.assertEqual([doc.get(f) for f in switches.FIELDS], [1] * 4)

    def test_http_readonly_scope_and_manager_writes(self):
        site, suffix = frappe.local.site, frappe.generate_hash(length=8)
        data = json.loads(Path(__file__).with_name("p2_data.json").read_text())
        for label, roles in (
            ("manager", ["HR Manager"]),
            ("hr", ["HR User"]),
            ("staff", ["Employee"]),
        ):
            user = frappe.get_doc(
                {
                    **data["user"],
                    "email": f"g3-{label}-{suffix}@test.invalid",
                    "roles": [dict(role=role) for role in roles],
                }
            ).insert()
            user.api_key, user.api_secret = "synthetic-g3-" + label + suffix, "synthetic-g3-secret"
            user.save()
        doc = frappe.get_doc(
            dict(doctype="Designation", designation_name="G3 HTTP " + suffix)
        ).insert()
        x4 = frappe.get_doc("User", "eb-x4@integration.invalid")
        previous = x4.enabled, x4.api_key, x4.get_password("api_secret", raise_exception=False)
        x4.enabled, x4.api_key, x4.api_secret = 1, "synthetic-g3-x4" + suffix, "synthetic-g3-secret"
        x4.save()
        frappe.db.commit()
        client = Client(application, Response, use_cookies=False)
        path = "/api/resource/Designation"
        fields = ["name", *switches.FIELDS]
        query = dict(
            fields=json.dumps(fields),
            filters=json.dumps([["name", "=", doc.name]]),
            limit_page_length=1,
        )

        def request(label, method="GET", target=path, expected=200, **kwargs):
            response = client.open(
                target,
                method=method,
                headers={
                    "Host": site,
                    "Authorization": f"token synthetic-g3-{label}{suffix}:synthetic-g3-secret",
                },
                **kwargs,
            )
            self.assertEqual(response.status_code, expected, label + " " + method)
            for value in data["private_samples"]:
                self.assertNotIn(value.encode(), response.data)
            return (response.get_json() or {}).get("data")

        try:
            with patch.object(frappe, "logger") as logger:
                self.assertEqual(
                    request("x4", query_string=query),
                    [{"name": doc.name, **dict.fromkeys(switches.FIELDS, 1)}],
                )
                for label in ("hr", "staff", "x4"):
                    for field in switches.FIELDS:
                        request(label, "PUT", path + "/" + doc.name, 403, json={field: 0})
                changed = request(
                    "manager", "PUT", path + "/" + doc.name, json=dict.fromkeys(switches.FIELDS, 0)
                )
                self.assertEqual([changed[f] for f in switches.FIELDS], [0] * 4)
                for field in switches.FIELDS:
                    request("hr", "PUT", path + "/" + doc.name, 403, json={field: 1})
                self.assertEqual(
                    request("x4", query_string=query),
                    [{"name": doc.name, **dict.fromkeys(switches.FIELDS, 0)}],
                )
                for value in data["private_samples"]:
                    self.assertNotIn(value, str(logger.mock_calls))
                for bad in (
                    dict(query, fields='["name","appraisal_template"]'),
                    dict(query, fields='["name","eb_knowledge_spaces"]'),
                    dict(query, fields='["name","employee.cell_number"]'),
                    dict(query, filters="[]"),
                    dict(query, filters='[["name","like","%"]]'),
                    dict(query, filters='[["name","=",""]]'),
                ):
                    request("x4", expected=403, query_string=bad)
                request("x4", target=path + "/" + doc.name, expected=403)
                request(
                    "hr",
                    "POST",
                    json={"designation_name": "G3 Denied " + suffix, switches.FIELDS[0]: 0},
                    expected=403,
                )
                new = request("hr", "POST", json={"designation_name": "G3 HR Default " + suffix})
                self.assertEqual([new[f] for f in switches.FIELDS], [1] * 4)
                print("test_tool_switches：岗位默认/开关权限/精确只读及越权拒绝通过。")
        finally:
            if not getattr(frappe.local, "db", None):
                frappe.init(site=site)
                frappe.connect()
            frappe.set_user("Administrator")
            x4 = frappe.get_doc("User", "eb-x4@integration.invalid")
            x4.enabled, x4.api_key, x4.api_secret = previous
            x4.save()
            frappe.db.commit()
