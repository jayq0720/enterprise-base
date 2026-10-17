"""原生入职单、Project/Task/ToDo 空库回归，仅使用合成数据。"""

import json
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase


class TestOnboarding(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.data = json.loads(Path(__file__).with_name("p2_data.json").read_text())
        for row in self.data["records"]:
            if row["doctype"] != "Version" and not frappe.db.exists(
                row["doctype"], row.get("name")
            ):
                frappe.get_doc(row).insert()
        self.suffix = frappe.generate_hash(length=8)
        self.hr = "boarding-hr-" + self.suffix + "@test.invalid"
        self.clerk = "boarding-clerk-" + self.suffix + "@test.invalid"
        for email, roles in ((self.hr, ["HR Manager", "HR User"]), (self.clerk, ["HR User"])):
            frappe.get_doc(
                {**self.data["user"], "email": email, "roles": [{"role": r} for r in roles]}
            ).insert()
        self.designation = (
            frappe.get_doc(dict(doctype="Designation", designation_name="Boarding " + self.suffix))
            .insert()
            .name
        )
        self.applicant = frappe.get_doc(
            dict(
                doctype="Job Applicant",
                applicant_name="Synthetic Boarding",
                email_id="boarding-" + self.suffix + "@test.invalid",
                status="Accepted",
            )
        ).insert()
        self.offer = frappe.get_doc(
            dict(
                doctype="Job Offer",
                job_applicant=self.applicant.name,
                applicant_name=self.applicant.applicant_name,
                company="P2 Synthetic",
                designation=self.designation,
                offer_date="2026-10-01",
                status="Accepted",
            )
        ).insert()
        self.offer.submit()
        frappe.set_user(self.hr)
        template = frappe.copy_doc(
            frappe.get_doc("Employee Onboarding Template", "EB Synthetic Onboarding")
        )
        template.title = "Boarding " + self.suffix
        template.company, template.designation = "P2 Synthetic", self.designation
        for activity in template.activities:
            activity.user = self.hr
        template.activities[-1].user = self.clerk
        self.template = template.insert()

    def employee(self, applicant=True):
        return frappe.get_doc(
            {
                **self.data["employee"],
                "first_name": "Synthetic Boarding",
                "employee_number": "BOARD-" + self.suffix,
                "wecom_userid": None
                if frappe.session.user == self.clerk
                else "board-" + self.suffix,
                "user_id": None,
                "create_user_permission": 0,
                "leave_approver": None,
                "designation": self.designation,
                "job_applicant": self.applicant.name if applicant else None,
            }
        ).insert()

    def test_creates_native_tasks_and_is_idempotent(self):
        with patch.object(frappe, "logger") as logger:
            employee = self.employee()
            names = frappe.get_all(
                "Employee Onboarding", filters={"employee": employee.name}, pluck="name"
            )
            self.assertEqual(len(names), 1)
            onboarding = frappe.get_doc("Employee Onboarding", names[0])
            self.assertEqual(
                (int(onboarding.docstatus), onboarding.job_offer), (1, self.offer.name)
            )
            self.assertEqual(len(onboarding.activities), 3)
            tasks = [a.task for a in onboarding.activities]
            self.assertTrue(all(tasks))
            self.assertEqual(frappe.db.count("Task", {"project": onboarding.project}), 3)
            for activity in onboarding.activities:
                task = activity.task
                self.assertEqual(
                    frappe.db.count(
                        "ToDo",
                        {
                            "reference_type": "Task",
                            "reference_name": task,
                            "allocated_to": activity.user,
                            "status": "Open",
                        },
                    ),
                    1,
                )
            self.assertTrue(
                frappe.has_permission(doc=frappe.get_doc("Task", tasks[-1]), user=self.clerk)
            )
            frappe.set_user("Administrator")
            unrelated = frappe.get_doc(
                dict(doctype="Task", subject="Unrelated synthetic task")
            ).insert()
            self.assertFalse(frappe.has_permission(doc=unrelated, user=self.hr))
            frappe.set_user(self.hr)
            employee.save()
            self.assertEqual(frappe.db.count("Employee Onboarding", {"employee": employee.name}), 1)
            self.assertEqual(frappe.db.count("Task", {"project": onboarding.project}), 3)
            for value in self.data["private_samples"]:
                self.assertNotIn(value, str(logger.mock_calls))

    def test_missing_recruitment_does_not_invent_records(self):
        applicants, offers = frappe.db.count("Job Applicant"), frappe.db.count("Job Offer")
        with patch.object(frappe, "msgprint") as message:
            employee = self.employee(applicant=False)
            self.assertIn("真实应聘者", str(message.mock_calls))
        self.assertFalse(frappe.db.exists("Employee Onboarding", {"employee": employee.name}))
        self.assertEqual(
            (frappe.db.count("Job Applicant"), frappe.db.count("Job Offer")), (applicants, offers)
        )
        employee.job_applicant = self.applicant.name
        employee.save()
        self.assertTrue(
            frappe.db.exists("Employee Onboarding", {"employee": employee.name, "docstatus": 1})
        )

    def test_missing_offer_assignment_and_ambiguity(self):
        employee = self.employee(applicant=False)
        employee.job_applicant = self.applicant.name
        frappe.set_user("Administrator")
        self.offer.cancel()
        employee.save()
        self.assertFalse(frappe.db.exists("Employee Onboarding", {"employee": employee.name}))
        offer = frappe.copy_doc(self.offer)
        offer.docstatus = 0
        offer.amended_from = self.offer.name
        offer.insert().submit()
        frappe.set_user(self.hr)
        self.template.activities[0].user = None
        self.template.save()
        employee.save()
        self.assertFalse(frappe.db.exists("Employee Onboarding", {"employee": employee.name}))
        self.template.activities[0].user = self.hr
        self.template.save()
        duplicate = frappe.copy_doc(self.template)
        duplicate.title = "Duplicate " + self.suffix
        duplicate.insert()
        employee.save()
        self.assertFalse(frappe.db.exists("Employee Onboarding", {"employee": employee.name}))

    def test_clerk_cannot_edit_template_or_auto_submit(self):
        frappe.set_user(self.clerk)
        with self.assertRaises(frappe.PermissionError):
            self.template.save()
        employee = self.employee()
        self.assertFalse(frappe.db.exists("Employee Onboarding", {"employee": employee.name}))
        frappe.set_user(self.hr)
        employee.save()
        self.assertTrue(
            frappe.db.exists("Employee Onboarding", {"employee": employee.name, "docstatus": 1})
        )
