before_request = ["enterprise_base.wecom.callback.guard"]

app_name = "enterprise_base"
app_title = "Enterprise Base"
app_publisher = "Enterprise Base contributors"
app_description = "X1 岗位职责与知识空间"
app_email = "maintainers@example.invalid"
app_license = "GPL-3.0"
required_apps = ["hrms", "wiki"]
before_install = "enterprise_base.install.ensure_role"
after_sync = [
    "enterprise_base.install.configure",
    "enterprise_base.integration.configure_webhooks",
    "enterprise_base.leave_flow.configure",
]
after_migrate = [*after_sync, "enterprise_base.permissions.align_wiki_review_state"]
after_install = ["enterprise_base.permissions.align_wiki_review_state"]
after_sync.append("enterprise_base.onboarding.configure")
after_migrate.append("enterprise_base.onboarding.configure")
doc_events: dict[str, dict[str, str | list[str]]] = {
    doctype: {
        "validate": "enterprise_base.duties.validation.validate_scope_name",
        "before_rename": "enterprise_base.duties.validation.validate_scope_rename",
    }
    for doctype in ("Department", "Designation")
}
fixtures = [
    {"dt": "Role", "filters": [["name", "in", ["EB Integration", "EB X4"]]]},
    {
        "dt": "Custom Field",
        "filters": [
            [
                "name",
                "in",
                [
                    "Designation-eb_responsibilities",
                    "Designation-eb_knowledge_spaces",
                    "Designation-eb_assistant_tools_section",
                    "Designation-eb_tool_leave_query",
                    "Designation-eb_tool_leave_apply",
                    "Designation-eb_tool_approval",
                    "Designation-eb_tool_duties",
                    "Employee-wecom_userid",
                    "Department-eb_wecom_department_id",
                    "Employee-eb_hr_approver",
                    "Leave Application-eb_hr_approver",
                    "Leave Application-eb_leave_state",
                ],
            ]
        ],
    },
]

auth_hooks = ["enterprise_base.duties.access.restrict_integration"]
auth_hooks.append("enterprise_base.permissions.x4_api")
override_doctype_class = {"Employee": "enterprise_base.permissions.Employee"}
has_permission = {"Employee": "enterprise_base.permissions.employee_access"}
permission_query_conditions = {"Employee": "enterprise_base.permissions.employee_query"}
doc_events["Employee"] = {
    "on_update": [
        "enterprise_base.permissions.disable_left_user",
        "enterprise_base.onboarding.generate",
    ]
}
doc_events["Wiki Document"] = {"before_validate": "enterprise_base.permissions.wiki_review"}
doc_events["Leave Application"] = {
    "before_validate": "enterprise_base.leave_flow.validate",
    "before_cancel": "enterprise_base.leave_flow.validate",
    "before_update_after_submit": "enterprise_base.leave_flow.validate",
}
doc_events["ToDo"] = {"on_update": "enterprise_base.onboarding.complete_assigned_task"}
doc_events["Version"] = {"before_insert": "enterprise_base.permissions.redact_version"}
doc_events["User"] = {"on_update": "enterprise_base.permissions.hr_scope"}
fixtures.extend(
    [
        {"dt": "Custom DocPerm", "filters": [["role", "=", "EB Integration"]]},
        {
            "dt": "User",
            "filters": [
                [
                    "name",
                    "in",
                    [
                        "eb-x3@integration.invalid",
                        "eb-x5@integration.invalid",
                        "eb-x4@integration.invalid",
                    ],
                ]
            ],
        },
    ]
)
fixtures.append({"dt": "Client Script", "filters": [["name", "=", "EB Employee Self Fields"]]})
fixtures.append(
    {"dt": "Employee Onboarding Template", "filters": [["name", "=", "EB Synthetic Onboarding"]]}
)

fixtures.append({"dt": "Webhook", "filters": [["name", "in", ["EB Wiki Update", "EB Wiki Trash"]]]})
fixtures.extend(
    {"dt": dt, "filters": [["name", "like", "EB %"]]}
    for dt in ("Workflow", "Workflow State", "Workflow Action Master")
)

doc_events["Leave Application"]["on_update"] = "enterprise_base.wecom.card_delivery.on_update"

persistent_cache_keys = ["eb-org-import"]
scheduler_events = {"hourly": ["enterprise_base.wecom.sync.hourly"]}
scheduler_events["hourly"].append("enterprise_base.wecom.card_delivery.retry")

before_migrate = ["enterprise_base.duties.tool_switches.backfill"]
after_sync.append("enterprise_base.duties.tool_switches.configure")
after_migrate.extend(
    [
        "enterprise_base.duties.tool_switches.configure",
        "enterprise_base.duties.tool_switches.backfill",
    ]
)
after_install.append("enterprise_base.duties.tool_switches.backfill")
doc_events["Designation"]["validate"] = [
    "enterprise_base.duties.validation.validate_scope_name",
    "enterprise_base.duties.tool_switches.validate",
    "enterprise_base.marketing.inquiry.validate_switches",
]

from enterprise_base.marketing.fields import lead_fields as _lead_fields  # noqa: E402

# 营销：询盘中心（ERPNext Lead 上的字段和钩子）、营销助手岗位开关
after_sync.append("enterprise_base.marketing.inquiry.configure")
after_migrate.append("enterprise_base.marketing.inquiry.configure")
scheduler_events["hourly"].append("enterprise_base.marketing.alerts.overdue")
doc_events["Communication"] = {"after_insert": "enterprise_base.marketing.email.on_communication"}
doc_events["Lead"] = {
    "before_insert": "enterprise_base.marketing.inquiry.before_insert",
    "validate": "enterprise_base.marketing.inquiry.validate",
}
_custom_field_names: list[str] = fixtures[1]["filters"][0][2]  # type: ignore[index,assignment]
_custom_field_names.extend(
    [
        "Designation-eb_marketing_tools_section",
        "Designation-eb_tool_inquiry",
        "Designation-eb_tool_inquiry_stats",
        *(f"Lead-{field['fieldname']}" for field in _lead_fields()),
    ]
)

# X4 工具调用审计：按保留天数每天清理
scheduler_events["daily"] = ["enterprise_base.duties.audit.purge"]
