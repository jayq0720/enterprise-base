"""可见范围标记，格式和 X3 的 `kb_gateway.scope` 一致：`,all,`、`,dept:人事部 - TC,desig:HRBP,`。"""

import hashlib


class ScopeError(ValueError):
    pass


def build_scope(tokens: list[str]) -> str:
    clean = sorted({t.strip() for t in tokens if t.strip()})
    if not clean:
        raise ScopeError("可见范围为空")
    for t in clean:
        if "," in t:
            # 逗号是分隔符，名称里带逗号会让 X3 的包含匹配出错
            raise ScopeError("部门、岗位名称不能含英文逗号")
    return "," + ",".join(clean) + ","


def content_hash(title: str, content: str) -> str:
    return hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()[:32]


def render_text(title: str, content: str) -> str:
    return f"# {title}\n\n{content}".strip()
