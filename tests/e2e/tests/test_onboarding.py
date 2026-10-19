"""入职待办（P2-G1）：HR 保存新员工档案后，按岗位模板生成入职单和负责人的任务。

准备：新岗位 D、合成应聘者和已提交的录用通知、岗位 D 的入职模板（两项活动，负责人是 A 和 B）。
新建岗位 D 的员工后：自动生成一张已提交的入职单，两项活动各一个任务，分给对应负责人；
负责人能看到自己的任务，看不到入职单、录用通知和新员工档案；任务都办完后入职单变成已完成。
"""

import datetime
from collections.abc import Callable

from e2e.clients import Frappe
from e2e.settings import E2ESettings
from e2e.world import World


def test_onboarding_tasks_from_designation_template(
    settings: E2ESettings, frappe: Frappe, world: World
) -> None:
    tag, today = world.tag, datetime.date.today().isoformat()
    undo = world.cleanup

    def made(
        doctype: str, doc: dict[str, object], drop: Callable[[str], None] | None = None
    ) -> str:
        name = str(frappe.insert(doctype, doc)["name"])
        undo.append(lambda: (drop or (lambda n: frappe.delete(doctype, n)))(name))
        return name

    def cancel_then_delete(doctype: str) -> Callable[[str], None]:
        def drop(name: str) -> None:
            if frappe.get(doctype, name)["docstatus"] == 1:
                frappe.update(doctype, name, {"docstatus": 2})
            frappe.delete(doctype, name)

        return drop

    designation = made("Designation", {"designation_name": f"{tag} 岗位D"})
    applicant = made(
        "Job Applicant",
        {
            "applicant_name": f"{tag}D",
            "email_id": f"{tag.lower()}-d@example.invalid",
            "status": "Accepted",
        },
    )
    offer = made(
        "Job Offer",
        {
            "job_applicant": applicant,
            "applicant_name": f"{tag}D",
            "company": world.company,
            "designation": designation,
            "offer_date": today,
            "status": "Accepted",
            "docstatus": 1,
        },
        cancel_then_delete("Job Offer"),
    )
    owners = {f"{tag} 开通账号": world.a.user, f"{tag} 领电脑": world.b.user}
    made(
        "Employee Onboarding Template",
        {
            "title": f"{tag} 入职",
            "company": world.company,
            "designation": designation,
            "activities": [
                {"activity_name": name, "user": user, "begin_on": 0, "duration": 1}
                for name, user in owners.items()
            ],
        },
    )

    def drop_onboarding(employee: str) -> None:
        # 入职单引用员工，任务引用项目；按引用倒序删，ToDo 随任务的分配一起清掉
        for onboarding in frappe.names("Employee Onboarding", [["employee", "=", employee]]):
            doc = frappe.get("Employee Onboarding", onboarding)
            tasks = [a["task"] for a in doc["activities"] if a.get("task")]
            cancel_then_delete("Employee Onboarding")(onboarding)
            for task in tasks:
                for todo in frappe.names(
                    "ToDo", [["reference_type", "=", "Task"], ["reference_name", "=", task]]
                ):
                    frappe.delete("ToDo", todo)
                frappe.delete("Task", task)
            if doc.get("project"):
                frappe.delete("Project", doc["project"])
        frappe.delete("Employee", employee)

    # 管理员 Key 等同 HR Manager 保存档案，才会自动生成入职单
    employee = made(
        "Employee",
        {
            "first_name": f"{tag}D",
            "gender": "Male",
            "date_of_birth": "1990-01-01",
            "date_of_joining": today,
            "company": world.company,
            "department": world.department,
            "designation": designation,
            "holiday_list": f"{tag} 假期表",
            "status": "Active",
            "job_applicant": applicant,
            "create_user_permission": 0,
        },
        drop_onboarding,
    )

    names = frappe.names("Employee Onboarding", [["employee", "=", employee]])
    assert len(names) == 1
    onboarding = frappe.get("Employee Onboarding", names[0])
    assert (onboarding["docstatus"], onboarding["job_offer"]) == (1, offer)
    assert onboarding["boarding_status"] == "Pending"
    tasks = {a["activity_name"]: a["task"] for a in onboarding["activities"]}
    assert set(tasks) == set(owners) and all(tasks.values())

    for activity, user in owners.items():
        todos = frappe.names(
            "ToDo",
            [
                ["reference_type", "=", "Task"],
                ["reference_name", "=", tasks[activity]],
                ["allocated_to", "=", user],
                ["status", "=", "Open"],
            ],
        )
        assert len(todos) == 1
        # 负责人只看得到分给自己的任务，看不到入职单、录用通知和新员工档案
        person = frappe.as_user(user)
        assert person.get("Task", tasks[activity])["name"] == tasks[activity]
        for doctype, name in (
            ("Employee Onboarding", onboarding["name"]),
            ("Job Offer", offer),
            ("Employee", employee),
        ):
            assert person.http.get(f"/api/resource/{doctype}/{name}").status_code == 403

    # 再次保存员工档案不会重复生成
    frappe.update("Employee", employee, {"first_name": f"{tag}D"})
    assert frappe.names("Employee Onboarding", [["employee", "=", employee]]) == names

    # 任务都办完，入职单变成已完成
    for task in tasks.values():
        frappe.update("Task", task, {"status": "Completed"})
    assert frappe.get("Employee Onboarding", onboarding["name"])["boarding_status"] == "Completed"
