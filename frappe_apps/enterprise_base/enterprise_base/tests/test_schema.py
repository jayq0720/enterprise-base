"""需在真实 Frappe 测试站点执行。"""

import frappe
from frappe.tests.utils import FrappeTestCase


class TestSchema(FrappeTestCase):
    def test_wiki_binding_is_unique(self):
        wiki = frappe.get_doc(
            {
                "doctype": "Wiki Space",
                "space_name": "EB Test",
                "route": "eb-" + frappe.generate_hash(length=10),
            }
        ).insert()
        for name in ("EB Test 1", "EB Test 2"):
            doc = frappe.get_doc(
                {"doctype": "EB Knowledge Space", "space_name": name, "wiki_space": wiki.name}
            )
            if name.endswith("1"):
                doc.insert()
            else:
                with self.assertRaisesRegex(frappe.ValidationError, "同一 Wiki 空间"):
                    doc.insert()

    def test_comma_is_rejected(self):
        for doctype, field in (
            ("Department", "department_name"),
            ("Designation", "designation_name"),
        ):
            with self.assertRaisesRegex(frappe.ValidationError, "英文逗号"):
                frappe.get_doc({"doctype": doctype, field: "invalid,name"}).insert()

    def test_guest_cannot_create_knowledge_space(self):
        frappe.set_user("Guest")
        try:
            with self.assertRaises(frappe.PermissionError):
                frappe.get_doc({"doctype": "EB Knowledge Space", "space_name": "denied"}).insert()
        finally:
            frappe.set_user("Administrator")
