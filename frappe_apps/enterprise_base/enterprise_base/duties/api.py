"""X3/X5 的配置接口；只返回空间绑定，不读取员工个人信息。"""

from typing import Any

import frappe


@frappe.whitelist(methods=["GET"])
def knowledge_spaces() -> list[dict[str, Any]]:
    if frappe.session.user == "Guest" or "EB Integration" not in frappe.get_roles():
        frappe.throw("仅集成角色可读取知识空间。", frappe.PermissionError)
    spaces = frappe.get_all(
        "EB Knowledge Space",
        fields=["name", "space_name", "wiki_space", "dify_dataset_id", "visible_to_all"],
        order_by="name",
    )
    departments = frappe.get_all(
        "EB Space Department",
        filters={"parenttype": "EB Knowledge Space", "parentfield": "departments"},
        fields=["parent", "department"],
    )
    designations = frappe.get_all(
        "EB Designation Space",
        filters={"parenttype": "Designation", "parentfield": "eb_knowledge_spaces"},
        fields=["parent", "knowledge_space"],
    )
    result = []
    for space in spaces:
        tokens = {f"dept:{r.department}" for r in departments if r.parent == space.name}
        tokens.update(f"desig:{r.parent}" for r in designations if r.knowledge_space == space.name)
        # 旧数据若带逗号也一律拒绝，不能产生被 X5 误拆分的授权标记。
        if any("," in token for token in tokens):
            frappe.throw("存在无效的部门或岗位名称，请管理员修正。")
        result.append(
            {
                "space": space.space_name,
                "wiki_space": space.wiki_space,
                "dify_dataset_id": space.dify_dataset_id or "",
                "scope": ["all"] if space.visible_to_all else sorted(tokens),
            }
        )
    return result
