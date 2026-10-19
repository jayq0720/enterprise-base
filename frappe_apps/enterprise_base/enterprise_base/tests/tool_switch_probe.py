"""仅 CI 新站点：在安装 App 前模拟可空旧列，不操作已安装站点。"""

import frappe

from enterprise_base.duties.tool_switches import FIELDS

LEGACY = {"EB Tools Legacy Unset": 1, "EB Tools Legacy Off": 0, "EB Tools Legacy On": 1}


def seed_before_install():
    if not frappe.conf.allow_tests or "enterprise_base" in frappe.get_installed_apps():
        frappe.throw("只允许在启用测试、尚未安装 enterprise_base 的临时站点执行。")
    for name in LEGACY:
        frappe.get_doc(dict(doctype="Designation", designation_name=name)).insert()
    columns = ", ".join(f"add column `{field}` int null default null" for field in FIELDS)
    frappe.db.sql_ddl(f"alter table `tabDesignation` {columns}")
    for name, value in LEGACY.items():
        if name != "EB Tools Legacy Unset":
            assignments = ", ".join(f"`{field}`=%s" for field in FIELDS)
            frappe.db.sql(
                f"update `tabDesignation` set {assignments} where name=%s",
                [value] * len(FIELDS) + [name],
            )
    frappe.db.commit()


def verify_after_install():
    if not frappe.conf.allow_tests:
        frappe.throw("仅测试站点可运行升级验证。")
    for name, expected in LEGACY.items():
        values = frappe.db.get_value("Designation", name, list(FIELDS))
        if not values or tuple(values) != (expected,) * len(FIELDS):
            raise AssertionError("岗位开关升级未保持空值/关闭/开启语义。")
    print("岗位开关升级：旧空值补1、关闭保留0、开启保留1通过。")
