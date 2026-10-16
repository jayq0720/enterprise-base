"""准备一套合成数据，测完全部删掉。

两个员工、两个岗位：
- A（岗位 A）：只能看全员空间
- B（岗位 B）：还能看岗位 B 专属空间；同时是 A 的请假审批人
两个知识空间各绑一个 Wiki 空间，都写进同一个临时 Dify 知识库。
员工档案里填了合成的手机号、身份证号、银行卡号，用来检查它们不会出现在任何回复里。
"""

import datetime
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field

from e2e.clients import DifyDatasets, Frappe
from e2e.settings import E2ESettings


@dataclass
class Person:
    wecom: str
    user: str
    employee: str
    designation: str
    duties: str


@dataclass
class World:
    tag: str
    company: str
    department: str
    a: Person
    b: Person
    wiki_all: str
    wiki_b: str
    dataset: str
    # 合成的敏感值：任何服务的返回里都不能出现
    sensitive: list[str]
    cleanup: list[Callable[[], None]] = field(default_factory=list)

    def root(self, frappe: Frappe, wiki_space: str) -> str:
        return str(frappe.get("Wiki Space", wiki_space)["root_group"])


def build(s: E2ESettings, frappe: Frappe, dify: DifyDatasets) -> World:
    tag = "E2E" + secrets.token_hex(3).upper()
    undo: list[Callable[[], None]] = []

    def made(doctype: str, doc: dict[str, object]) -> str:
        name = str(frappe.insert(doctype, doc)["name"])
        undo.append(lambda: frappe.delete(doctype, name))
        return name

    try:
        company = s.company or frappe.names("Company", [], 1)[0]
        today = datetime.date.today()
        holidays = made(
            "Holiday List",
            {
                "holiday_list_name": f"{tag} 假期表",
                "from_date": f"{today.year - 1}-01-01",
                "to_date": f"{today.year + 1}-12-31",
            },
        )
        department = made(
            "Department",
            {
                "department_name": f"{tag} 测试部",
                "company": company,
                "parent_department": "All Departments",
            },
        )
        sensitive: list[str] = []
        people: dict[str, Person] = {}
        for key in ("a", "b"):
            designation = made(
                "Designation",
                {
                    "designation_name": f"{tag} 岗位{key.upper()}",
                    "eb_responsibilities": f"<ol><li>{tag} 岗位{key.upper()}职责一</li>"
                    f"<li>{tag} 岗位{key.upper()}职责二</li></ol>",
                },
            )
            email = f"{tag.lower()}-{key}@example.invalid"
            user = made(
                "User",
                {
                    "email": email,
                    "first_name": f"{tag}{key.upper()}",
                    "send_welcome_email": 0,
                    "roles": [{"role": "Employee"}],
                },
            )
            phone, account, national = (
                "199" + secrets.token_hex(4)[:8].translate(str.maketrans("abcdef", "123456")),
                f"{tag}-ACCOUNT-{key}",
                f"{tag}-ID-{key}",
            )
            sensitive += [phone, account, national]
            wecom = f"{tag.lower()}-{key}"
            employee = made(
                "Employee",
                {
                    "first_name": f"{tag}{key.upper()}",
                    "gender": "Male",
                    "date_of_birth": "1990-01-01",
                    "date_of_joining": f"{today.year - 1}-01-01",
                    "company": company,
                    "department": department,
                    "designation": designation,
                    "status": "Active",
                    "user_id": user,
                    "wecom_userid": wecom,
                    "holiday_list": holidays,
                    "cell_number": phone,
                    "bank_ac_no": account,
                    "eb_national_id": national,
                    "create_user_permission": 0,
                },
            )
            people[key] = Person(
                wecom=wecom,
                user=user,
                employee=employee,
                designation=designation,
                duties=f"{tag} 岗位{key.upper()}职责一",
            )
        a, b = people["a"], people["b"]
        frappe.update("Employee", a.employee, {"leave_approver": b.user})

        def unlink_approver() -> None:
            # 不先解除，删 B 的用户时会因为还被 A 的档案引用而失败
            frappe.update("Employee", a.employee, {"leave_approver": None})

        undo.append(unlink_approver)

        wiki_all = made(
            "Wiki Space", {"space_name": f"{tag} 全员制度", "route": f"{tag.lower()}-all"}
        )
        wiki_b = made("Wiki Space", {"space_name": f"{tag} 岗位B制度", "route": f"{tag.lower()}-b"})
        dataset = dify.create(f"{tag.lower()}-e2e", s.dify_indexing_technique)
        undo.append(lambda: dify.delete(dataset))
        made(
            "EB Knowledge Space",
            {
                "space_name": f"{tag} 全员",
                "wiki_space": wiki_all,
                "dify_dataset_id": dataset,
                "visible_to_all": 1,
            },
        )
        space_b = made(
            "EB Knowledge Space",
            {
                "space_name": f"{tag} 岗位B",
                "wiki_space": wiki_b,
                "dify_dataset_id": dataset,
                "visible_to_all": 0,
            },
        )
        frappe.update(
            "Designation", b.designation, {"eb_knowledge_spaces": [{"knowledge_space": space_b}]}
        )

        # 删知识空间前要先解除岗位上的绑定
        def unbind() -> None:
            frappe.update("Designation", b.designation, {"eb_knowledge_spaces": []})

        undo.append(unbind)
        return World(
            tag=tag,
            company=company,
            department=department,
            a=a,
            b=b,
            wiki_all=wiki_all,
            wiki_b=wiki_b,
            dataset=dataset,
            sensitive=sensitive,
            cleanup=undo,
        )
    except BaseException:
        teardown(undo)
        raise


def teardown(undo: list[Callable[[], None]]) -> list[str]:
    """倒序清理；某一步失败不影响后面的，最后返回失败列表。"""
    failed = []
    while undo:
        step = undo.pop()
        try:
            step()
        except Exception as e:
            failed.append(str(e)[:200])
    return failed
