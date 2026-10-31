import frappe
from frappe.model.document import Document


class EBAuditLog(Document):
    def validate(self):
        # 审计记录只增不改；到期清理走 audit.purge（直接删表行，不经过这里）
        if not self.is_new():
            frappe.throw("审计记录不能修改。", frappe.PermissionError)

    def on_trash(self):
        frappe.throw("审计记录不能删除。", frappe.PermissionError)
