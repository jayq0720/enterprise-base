"""文档可见范围。

X5 同步制度时在文档元数据 `eb_scope` 里写入可见范围，格式是用英文逗号包起来的标记：

- `,all,`：知识空间内所有人可见
- `,dept:人事部,desig:HRBP,`：人事部的人，或岗位是 HRBP 的人可见

部门、岗位名本身不能含英文逗号（X5 写入前负责检查）。
"""

from collections.abc import Mapping

from kb_gateway.models import EmployeeProfile

ALL = "all"


def viewer_tokens(department: str | None, designation: str | None) -> list[str]:
    """提问人的范围标记。查不到岗位时只看全员可见内容（任务清单 P1-C1）。"""
    if not designation:
        return [ALL]
    tokens = [ALL, f"desig:{designation}"]
    if department:
        tokens.append(f"dept:{department}")
    return tokens


def profile_tokens(profile: EmployeeProfile) -> list[str]:
    return viewer_tokens(profile.department, profile.designation)


def metadata_conditions(field: str, tokens: list[str]) -> dict[str, object]:
    """交给 Dify 的元数据过滤条件：任一标记命中即可。"""
    return {
        "logical_operator": "or",
        "conditions": [
            {"name": field, "comparison_operator": "contains", "value": f",{t},"} for t in tokens
        ],
    }


def is_visible(metadata: Mapping[str, object], field: str, tokens: list[str]) -> bool:
    """本地复核：不依赖 Dify 的过滤是否生效。没有范围标记的文档一律不可见。"""
    scope = metadata.get(field)
    if not isinstance(scope, str) or not scope:
        return False
    return any(f",{t}," in scope for t in tokens)
