import frappe
from frappe.model.document import Document


class EBKnowledgeSpace(Document):
    def validate(self) -> None:
        if self.wiki_space and frappe.db.exists(
            "EB Knowledge Space", {"wiki_space": self.wiki_space, "name": ["!=", self.name]}
        ):
            frappe.throw("同一 Wiki 空间只能绑定一个知识空间。")
