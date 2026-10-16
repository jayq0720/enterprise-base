import pytest

from frappe_mcp import session

KEY = b"k" * 32


def test_roundtrip() -> None:
    token = session.issue(KEY, "zhangsan", 900, now=1000)
    assert session.verify(KEY, token, now=1500) == "zhangsan"


def test_expired() -> None:
    token = session.issue(KEY, "zhangsan", 900, now=1000)
    with pytest.raises(session.SessionError, match="过期"):
        session.verify(KEY, token, now=2000)


@pytest.mark.parametrize("bad", ["", "abc", "sess-lisi", "a.b"])
def test_garbage_rejected(bad: str) -> None:
    with pytest.raises(session.SessionError):
        session.verify(KEY, bad, now=0)


def test_tampered_payload_or_other_key_rejected() -> None:
    token = session.issue(KEY, "zhangsan", 900, now=1000)
    other = session.issue(KEY, "lisi", 900, now=1000)
    forged = other.split(".")[0] + "." + token.split(".")[1]
    with pytest.raises(session.SessionError):
        session.verify(KEY, forged, now=1000)
    with pytest.raises(session.SessionError):
        session.verify(b"x" * 32, token, now=1000)
