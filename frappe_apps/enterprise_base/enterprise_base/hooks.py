app_name = "enterprise_base"
app_title = "Enterprise Base"
app_publisher = "Enterprise Base contributors"
app_description = "X1 岗位职责与知识空间"
app_email = "maintainers@example.invalid"
app_license = "GPL-3.0"
required_apps = ["hrms", "wiki"]
before_install = "enterprise_base.install.ensure_role"
after_sync = ["enterprise_base.install.configure", "enterprise_base.integration.configure_webhooks"]
after_migrate = after_sync
doc_events = {
    doctype: {
        "validate": "enterprise_base.duties.validation.validate_scope_name",
        "before_rename": "enterprise_base.duties.validation.validate_scope_rename",
    }
    for doctype in ("Department", "Designation")
}
fixtures = [
    {"dt": "Role", "filters": [["name", "=", "EB Integration"]]},
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
                ],
            ]
        ],
    },
]

auth_hooks = ["enterprise_base.duties.access.restrict_integration"]
fixtures.extend(
    [
        {"dt": "Custom DocPerm", "filters": [["role", "=", "EB Integration"]]},
        {
            "dt": "User",
            "filters": [["name", "in", ["eb-x3@integration.invalid", "eb-x5@integration.invalid"]]],
        },
    ]
)

fixtures.append({"dt": "Webhook", "filters": [["name", "in", ["EB Wiki Update", "EB Wiki Trash"]]]})
