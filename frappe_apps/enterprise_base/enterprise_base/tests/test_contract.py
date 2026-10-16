"""需在装有 ERPNext/HRMS/Wiki 的真实测试站点用 bench run-tests 执行。"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from enterprise_base.duties.access import restrict_integration
from enterprise_base.duties.api import knowledge_spaces


class TestContract(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_scope_and_duplicates(self):
        for row in frappe.get_all(
            "Custom DocPerm",
            filters={"role": "EB Integration"},
            fields=["read", "write", "create", "delete", "export"],
        ):
            self.assertEqual(
                [row.read, row.write, row.create, row.delete, row.export], [1, 0, 0, 0, 0]
            )
        self.assertNotIn("Wiki User", frappe.get_roles("eb-x3@integration.invalid"))
        if not frappe.db.exists("Warehouse Type", "Transit"):
            frappe.get_doc({"doctype": "Warehouse Type", "name": "Transit"}).insert()
        company = frappe.get_doc(
            {
                "doctype": "Company",
                "company_name": "EB Test",
                "abbr": "EBT",
                "country": "China",
                "default_currency": "CNY",
            }
        ).insert()
        department = frappe.get_doc(
            {"doctype": "Department", "department_name": "人事部", "company": company.name}
        ).insert()
        designation = frappe.get_doc(
            {"doctype": "Designation", "designation_name": "EB HRBP"}
        ).insert()
        for public, dept, job, expected in (
            (1, False, False, ["all"]),
            (0, True, False, [f"dept:{department.name}"]),
            (0, False, True, [f"desig:{designation.name}"]),
            (0, False, False, []),
            (0, True, True, sorted([f"dept:{department.name}", f"desig:{designation.name}"])),
        ):
            wiki = frappe.get_doc(
                {
                    "doctype": "Wiki Space",
                    "space_name": "EB Test",
                    "route": "eb-" + frappe.generate_hash(length=10),
                }
            ).insert()
            space = frappe.get_doc(
                {
                    "doctype": "EB Knowledge Space",
                    "space_name": wiki.name,
                    "wiki_space": wiki.name,
                    "visible_to_all": public,
                    "dify_dataset_id": "synthetic-dataset",
                    "departments": [{"department": department.name}] if dept else [],
                }
            ).insert()
            if job:
                designation.append("eb_knowledge_spaces", {"knowledge_space": space.name})
                designation.save()
            frappe.set_user("eb-x3@integration.invalid")
            row = next(r for r in knowledge_spaces() if r["space"] == space.name)
            self.assertEqual(row["scope"], expected)
            self.assertEqual(set(row), {"space", "wiki_space", "dify_dataset_id", "scope"})
            frappe.set_user("Administrator")
            with self.assertRaises(frappe.ValidationError):
                frappe.get_doc(
                    {
                        "doctype": "EB Knowledge Space",
                        "space_name": "duplicate",
                        "wiki_space": wiki.name,
                    }
                ).insert()

    def test_commas_and_role_rejection(self):
        for doctype, field in (
            ("Department", "department_name"),
            ("Designation", "designation_name"),
        ):
            with self.assertRaisesRegex(frappe.ValidationError, "英文逗号"):
                frappe.get_doc({"doctype": doctype, field: "invalid,name"}).insert()
        designation = frappe.get_doc(
            {"doctype": "Designation", "designation_name": "EB Rename"}
        ).insert()
        with self.assertRaises(frappe.ValidationError):
            frappe.rename_doc("Designation", designation.name, "invalid,name")
        user_without_role = frappe.get_doc(
            {
                "doctype": "User",
                "email": "eb-no-integration@integration.invalid",
                "first_name": "No Integration",
                "send_welcome_email": 0,
                "enabled": 0,
            }
        ).insert()
        for user in ("Guest", user_without_role.name):
            frappe.set_user(user)
            with self.assertRaises(frappe.PermissionError):
                knowledge_spaces()

    def test_employee_fields_and_generic_api_bypasses(self):
        old_request, old_form = getattr(frappe.local, "request", None), frappe.local.form_dict
        frappe.set_user("eb-x5@integration.invalid")
        try:
            for path, method, fields in (
                ("/api/resource/Employee", "GET", ["name", "department", "designation"]),
                ("/api/resource/Employee", "GET", ["*"]),
                ("/api/resource/Employee", "GET", ["cell_number", "bank_ac_no"]),
                ("/api/resource/Employee/employee-1", "GET", ["name"]),
                ("/api/resource/Department", "POST", ["name"]),
                ("/api/method/frappe.client.get_list", "GET", ["name"]),
            ):
                frappe.local.request = frappe._dict(path=path, method=method)
                frappe.local.form_dict = frappe._dict(fields=json.dumps(fields))
                if path == "/api/resource/Employee" and fields == [
                    "name",
                    "department",
                    "designation",
                ]:
                    restrict_integration()
                else:
                    with self.assertRaises(frappe.PermissionError):
                        restrict_integration()
        finally:
            frappe.local.request, frappe.local.form_dict = old_request, old_form
