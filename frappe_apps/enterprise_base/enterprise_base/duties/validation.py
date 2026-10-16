from typing import Any

import frappe


def validate_scope_name(doc: Any, method: str | None = None) -> None:
    # Department 最终 name 可能带公司缩写；同时校验命名来源和最终名称。
    for field in ("name", "department_name", "designation_name"):
        if "," in str(doc.get(field) or ""):
            frappe.throw("部门和岗位名称不能含英文逗号。")


def validate_scope_rename(doc: Any, method: str, old: str, new: str, merge: bool = False) -> None:
    if "," in new:
        frappe.throw("部门和岗位名称不能含英文逗号。")
