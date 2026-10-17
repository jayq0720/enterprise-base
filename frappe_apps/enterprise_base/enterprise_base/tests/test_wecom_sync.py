"""真实空库同步回归，企业微信上游仅使用合成数据。"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from enterprise_base.wecom import sync


class TestWecomSync(FrappeTestCase):
    def test_native_sync_and_departure(self):
        data = json.loads(Path(__file__).with_name("p2_data.json").read_text())
        for row in data["records"]:
            if not frappe.db.exists(row["doctype"], row.get("name")):
                frappe.get_doc(row).insert()
        company = frappe.get_doc(
            dict(
                doctype="Company",
                company_name="WeCom Synthetic",
                abbr="WCS",
                country="China",
                default_currency="CNY",
            )
        ).insert()
        user = frappe.get_doc(
            {**data["user"], "email": "wecom-sync@test.invalid", "roles": [{"role": "Employee"}]}
        ).insert()
        employee = frappe.get_doc(
            {
                **data["employee"],
                "company": company.name,
                "first_name": "同步测试",
                "employee_number": "WECOM-SYNC",
                "user_id": user.name,
                "wecom_userid": "synthetic-sync",
                "leave_approver": None,
                "create_user_permission": 0,
            }
        ).insert()
        user.db_set("enabled", 0)
        cfg = dict(enabled=True, company=company.name, full_directory_verified=True)
        depts = {9001: dict(name="合成同步部门", parentid=0)}
        member = dict(userid="synthetic-sync", name="更新后的姓名", department=[9001])
        snapshot = (depts, {member["userid"]: member}, {member["userid"]})
        try:
            with (
                patch.dict(frappe.conf, eb_wecom_sync=cfg),
                patch.object(sync, "_snapshot", return_value=snapshot) as upstream,
                patch.object(frappe, "logger") as logger,
            ):
                result = sync.sync_now()
                self.assertEqual(result, dict(updated=1, left=0, pending=0, skipped=0))
                employee.reload()
                self.assertEqual(employee.employee_name, member["name"])
                self.assertEqual(employee.cell_number, data["employee"]["cell_number"])
                self.assertFalse(frappe.db.get_value("User", user.name, "enabled"))
                self.assertEqual(sync.sync_now()["updated"], 0)
                self.assertEqual(
                    frappe.db.count("Department", {"eb_wecom_department_id": "9001"}), 1
                )
                # 应用可见范围缩小：全量 ID 仍存在，不能误判离职。
                upstream.return_value = (depts, {}, {member["userid"]})
                self.assertEqual(sync.sync_now()["left"], 0)
                # 未核对全企业通讯录授权时，即使全量响应缺人也不自动停用。
                upstream.return_value = (depts, {}, {"synthetic-other"})
                cfg["full_directory_verified"] = False
                self.assertEqual(sync.sync_now()["skipped"], 1)
                cfg["full_directory_verified"] = True
                user.db_set("enabled", 1)
                with patch.object(sync, "disable_left_user", side_effect=ValueError("private")):
                    self.assertEqual(sync.sync_now(), {"failed": True})
                self.assertEqual(frappe.db.get_value("Employee", employee.name, "status"), "Active")
                self.assertTrue(frappe.db.get_value("User", user.name, "enabled"))
                self.assertEqual(sync.sync_now()["left"], 1)
                self.assertEqual(frappe.db.get_value("Employee", employee.name, "status"), "Left")
                self.assertFalse(frappe.db.get_value("User", user.name, "enabled"))
                self.assertEqual(sync.sync_now()["left"], 0)
                employee.db_set("status", "Inactive")
                user.db_set("enabled", 1)
                self.assertEqual(sync.sync_now()["left"], 1)
                self.assertFalse(frappe.db.get_value("User", user.name, "enabled"))
                upstream.return_value = (
                    {9002: dict(name="invalid,department", parentid=0)},
                    {},
                    {"x"},
                )
                self.assertEqual(sync.sync_now()["skipped"], 1)
                self.assertFalse(frappe.db.exists("Department", {"eb_wecom_department_id": "9002"}))
                upstream.return_value = (depts, {"new-member": member}, {"new-member"})
                self.assertEqual(sync.sync_now()["pending"], 1)
                self.assertFalse(frappe.db.exists("Employee", {"wecom_userid": "new-member"}))
                upstream.side_effect = ValueError("synthetic-private-value")
                self.assertEqual(sync.sync_now(), {"failed": True})
                for value in data["private_samples"] + ["synthetic-private-value"]:
                    self.assertNotIn(value, str(logger.mock_calls))
                frappe.set_user(user.name)
                with self.assertRaises(frappe.PermissionError):
                    sync.sync_now()
                with patch.object(frappe, "get_roles", return_value=["HR User"]):
                    with self.assertRaises(frappe.PermissionError):
                        sync.sync_now()
                    department = frappe.get_doc("Department", employee.department)
                    department.eb_wecom_department_id = "9003"
                    with self.assertRaises(frappe.PermissionError):
                        department.save(ignore_permissions=True)
        finally:
            frappe.set_user("Administrator")
            frappe.db.delete("Employee", {"name": employee.name})
            frappe.delete_doc("User", user.name, ignore_permissions=True, force=True)

    def test_minimum_fields_and_complete_pages(self):
        cfg = dict(corpid="synthetic", app_secret="synthetic-app", directory_secret="synthetic-dir")

        def request(method, path, **kwargs):
            if path == "gettoken":
                return {"access_token": "synthetic-token"}
            if path == "department/list":
                return {"department": [dict(id=1, name="synthetic", parentid=0)]}
            if path == "user/simplelist":
                return {"userlist": [dict(userid="synthetic", name="synthetic", department=[1])]}
            self.assertEqual((method, path), ("POST", "user/list_id"))
            cursor = kwargs["json"]["cursor"]
            return {
                "dept_user": [dict(userid="synthetic" if not cursor else "other", department=1)],
                "next_cursor": "next" if not cursor else "",
            }

        with patch.object(sync, "_request", side_effect=request):
            self.assertEqual(sync._snapshot(cfg)[2], {"synthetic", "other"})
        for defect in ("private", "cursor", "partial"):

            def broken(method, path, defect=defect, **kwargs):
                response = request(method, path, **kwargs)
                if defect == "private" and path == "user/simplelist":
                    response["userlist"][0]["mobile"] = "19900000000"
                if path == "user/list_id" and kwargs["json"]["cursor"]:
                    if defect == "cursor":
                        response["next_cursor"] = "next"
                    elif defect == "partial":
                        raise ValueError("incomplete")
                return response

            with (
                patch.object(sync, "_request", side_effect=broken),
                self.assertRaises((AssertionError, ValueError)),
            ):
                sync._snapshot(cfg)
        with patch.object(sync.requests, "request", return_value=Mock(status_code=302)):
            with self.assertRaises(ValueError):
                sync._request("GET", "gettoken")
