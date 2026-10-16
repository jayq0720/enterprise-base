from kb_gateway.scope import is_visible, metadata_conditions, profile_tokens, viewer_tokens

from .conftest import HRBP


def test_viewer_tokens() -> None:
    assert profile_tokens(HRBP) == ["all", "desig:HRBP", "dept:人事部"]
    # 查不到岗位时只看全员可见
    assert viewer_tokens("人事部", None) == ["all"]


def test_conditions_are_or_of_wrapped_tokens() -> None:
    c = metadata_conditions("eb_scope", ["all", "desig:HRBP"])
    assert c["logical_operator"] == "or"
    assert c["conditions"] == [
        {"name": "eb_scope", "comparison_operator": "contains", "value": ",all,"},
        {"name": "eb_scope", "comparison_operator": "contains", "value": ",desig:HRBP,"},
    ]


def test_visible() -> None:
    tokens = profile_tokens(HRBP)
    assert is_visible({"eb_scope": ",all,"}, "eb_scope", tokens)
    assert is_visible({"eb_scope": ",dept:财务部,desig:HRBP,"}, "eb_scope", tokens)


def test_not_visible_without_scope_or_on_prefix_match() -> None:
    tokens = profile_tokens(HRBP)
    assert not is_visible({}, "eb_scope", tokens)
    assert not is_visible({"eb_scope": ""}, "eb_scope", tokens)
    assert not is_visible({"eb_scope": None}, "eb_scope", tokens)
    # 岗位名是前缀时不能误命中
    assert not is_visible({"eb_scope": ",desig:HRBP主管,"}, "eb_scope", tokens)
