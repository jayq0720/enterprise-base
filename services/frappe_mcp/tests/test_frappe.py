import json

from frappe_mcp.frappe import _rejected


def test_rejected_parses_server_messages() -> None:
    # 本地 Frappe HR 余额不足时 417 响应的形状
    msg = {"message": "Insufficient leave balance for Leave Type <strong>年假</strong>"}
    data = {
        "exc_type": "InsufficientLeaveBalanceError",
        "_server_messages": json.dumps([json.dumps(msg)]),
    }
    e = _rejected(data)
    assert e.exc_type == "InsufficientLeaveBalanceError"
    assert e.message == "Insufficient leave balance for Leave Type 年假"
    assert _rejected({"exc_type": "X"}).message == ""
