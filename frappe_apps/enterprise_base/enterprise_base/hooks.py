app_name = "enterprise_base"
app_title = "Enterprise Base"
app_publisher = "Enterprise Base contributors"
app_description = "X1 岗位职责与知识空间"
app_email = "maintainers@example.invalid"
app_license = "GPL-3.0"
required_apps = ["hrms", "wiki"]
before_install = "enterprise_base.install.ensure_role"
after_sync = ["enterprise_base.install.configure", "enterprise_base.integration.configure_webhooks"]
after_migrate = [*after_sync, "enterprise_base.permissions.align_wiki_review_state"]
after_install = ["enterprise_base.permissions.align_wiki_review_state"]
doc_events = {
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
                    "Employee-wecom_userid",
                    "Department-eb_wecom_department_id",
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
doc_events["Employee"] = {"on_update": "enterprise_base.permissions.disable_left_user"}
doc_events["Wiki Document"] = {"before_validate": "enterprise_base.permissions.wiki_review"}
doc_events["Leave Application"] = {"before_validate": "enterprise_base.permissions.x4_draft"}
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

fixtures.append({"dt": "Webhook", "filters": [["name", "in", ["EB Wiki Update", "EB Wiki Trash"]]]})
fixtures.extend(
    {"dt": dt, "filters": [["name", "like", "EB %"]]}
    for dt in ("Workflow", "Workflow State", "Workflow Action Master")
)

persistent_cache_keys = ["eb-org-import"]
scheduler_events = {"hourly": ["enterprise_base.wecom.sync.hourly"]}
