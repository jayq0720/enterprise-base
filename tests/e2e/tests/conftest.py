"""没配置测试环境（EB_E2E_FRAPPE_URL 为空）时整套跳过（test_dsl.py 除外），CI 里只做静态检查。"""

import datetime
import os
import warnings
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from e2e.clients import X3, X4, DifyDatasets, Frappe, HRAssistant
from e2e.settings import E2ESettings
from e2e.world import World, build, teardown


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if not os.environ.get("EB_E2E_FRAPPE_URL"):
        skip = pytest.mark.skip(reason="未配置测试环境（EB_E2E_*），跳过端到端测试")
        for item in items:
            # 离线检查（DSL 结构）不需要测试环境
            if item.path.name != "test_dsl.py":
                item.add_marker(skip)


@pytest.fixture(scope="session")
def settings() -> E2ESettings:
    try:
        return E2ESettings()
    except ValidationError as e:
        pytest.fail(f"EB_E2E_* 配置不全：{e.error_count()} 项有误")


@pytest.fixture(scope="session")
def frappe(settings: E2ESettings) -> Frappe:
    return Frappe(settings)


@pytest.fixture(scope="session")
def x3(settings: E2ESettings) -> X3:
    return X3(settings)


@pytest.fixture(scope="session")
def x4(settings: E2ESettings) -> X4:
    return X4(settings)


@pytest.fixture(scope="session")
def world(settings: E2ESettings, frappe: Frappe) -> Iterator[World]:
    w = build(settings, frappe, DifyDatasets(settings))
    yield w
    failed = teardown(w.cleanup)
    if failed:
        warnings.warn(f"有 {len(failed)} 条合成数据没删掉，标记 {w.tag}：{failed}", stacklevel=1)


@pytest.fixture(scope="session")
def hr(settings: E2ESettings) -> HRAssistant:
    if settings.hr_app_key is None:
        pytest.skip("未配置 EB_E2E_HR_APP_KEY，跳过 HR 助手对话测试")
    return HRAssistant(settings, settings.hr_app_key.get_secret_value())


@dataclass
class Flow:
    hr_wecom: str
    hr_user: str


@pytest.fixture(scope="session")
def flow(settings: E2ESettings, frappe: Frappe, world: World) -> Iterator[Flow]:
    """两级审批要的人和权限：B 加请假审批人角色，新建 HR 审批人 C，给 A 分配假期。"""
    tag = world.tag
    email = f"{tag.lower()}-c@example.invalid"
    user = str(
        frappe.insert(
            "User",
            {
                "email": email,
                "first_name": f"{tag}C",
                "send_welcome_email": 0,
                "roles": [{"role": "Employee"}, {"role": "HR User"}],
            },
        )["name"]
    )
    world.cleanup.append(lambda: frappe.delete("User", user))
    hr_wecom = f"{tag.lower()}-c"
    employee = str(
        frappe.insert(
            "Employee",
            {
                "first_name": f"{tag}C",
                "gender": "Male",
                "date_of_birth": "1990-01-01",
                "date_of_joining": f"{datetime.date.today().year - 1}-01-01",
                "company": world.company,
                "department": world.department,
                "designation": world.b.designation,
                "status": "Active",
                "user_id": user,
                "wecom_userid": hr_wecom,
                "create_user_permission": 0,
            },
        )["name"]
    )
    world.cleanup.append(lambda: frappe.delete("Employee", employee))
    b_roles = frappe.get("User", world.b.user)["roles"]
    frappe.update(
        "User",
        world.b.user,
        {"roles": [{"role": r["role"]} for r in b_roles] + [{"role": "Leave Approver"}]},
    )
    frappe.update("Employee", world.a.employee, {"eb_hr_approver": user})

    def unlink_hr() -> None:
        # 不先解除，删 C 的用户时会因为还被 A 的档案引用而失败
        frappe.update("Employee", world.a.employee, {"eb_hr_approver": None})

    world.cleanup.append(unlink_hr)
    world.allocate_leave(frappe, settings.leave_type)
    yield Flow(hr_wecom=hr_wecom, hr_user=user)


def assert_clean(world: World, *outputs: object) -> None:
    """合成的手机号、身份证号、银行卡号不能出现在任何返回里。"""
    text = repr(outputs)
    leaked = [v for v in world.sensitive if v in text]
    assert not leaked, f"返回里出现了敏感字段（{len(leaked)} 项）"
