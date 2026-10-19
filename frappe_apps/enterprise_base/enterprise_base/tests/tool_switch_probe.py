"""仅 CI 临时站点：模拟旧列并核对安装、真实迁移及关闭值保持。"""

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


def seed_before_migrate():
    if (
        frappe.local.site != "contract.local"
        or not frappe.conf.allow_tests
        or "enterprise_base" not in frappe.get_installed_apps()
    ):
        frappe.throw("只允许在已安装 App、启用测试的临时 contract.local 运行。")
    verify_after_install()  # 必须是安装前造出的三条合成记录，先核对再改旧列。
    columns = ", ".join(f"modify column `{field}` int null default null" for field in FIELDS)
    frappe.db.sql_ddl(f"alter table `tabDesignation` {columns}")
    assignments = ", ".join(f"`{field}`=null" for field in FIELDS)
    frappe.db.sql(
        f"update `tabDesignation` set {assignments} where name=%s", "EB Tools Legacy Unset"
    )
    frappe.db.commit()
