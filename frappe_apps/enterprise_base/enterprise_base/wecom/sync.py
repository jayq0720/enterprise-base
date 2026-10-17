"""最小字段通讯录同步；外部数据先完整校验，再原子更新已绑定的档案。"""

import json

import frappe
import requests

from enterprise_base.permissions import disable_left_user


def _request(method, path, **kwargs):
    try:
        response = requests.request(
            method,
            "https://qyapi.weixin.qq.com/cgi-bin/" + path,
            timeout=(3.05, 10),
            allow_redirects=False,
            **kwargs,
        )
        if response.status_code == 200:
            body = response.json()
            if isinstance(body, dict) and body.get("errcode") == 0:
                return body
    except (requests.RequestException, ValueError):
        pass
    # 只抛固定错误，调用层也不会记录原始 traceback/请求/响应。
    raise ValueError("upstream_failed")


def _require(condition):
    if not condition:
        raise ValueError("invalid_directory")


def _text(value):
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= 140
        and not any(c in value for c in "<>\x00\r\n")
    )


def _snapshot(cfg):
    def token(secret):
        result = _request("GET", "gettoken", params={"corpid": cfg["corpid"], "corpsecret": secret})
        _require(isinstance(result.get("access_token"), str) and result["access_token"])
        return result["access_token"]

    app, directory = token(cfg["app_secret"]), token(cfg["directory_secret"])
    departments = _request("GET", "department/list", params={"access_token": app})["department"]
    # 部门数不设上限：department/list 一次返回全部，按客户规模写死上限会让大企业整批失败。
    _require(isinstance(departments, list) and departments)
    depts: dict[int, dict] = {}
    members: dict[str, dict] = {}
    for row in departments:
        _require(type(row["id"]) is int and row["id"] > 0 and row["id"] not in depts)
        _require(_text(row["name"]) and type(row["parentid"]) is int)
        depts[row["id"]] = {k: row[k] for k in ("name", "parentid")}
        users = _request(
            "GET",
            "user/simplelist",
            params={"access_token": app, "department_id": row["id"], "fetch_child": 0},
        )["userlist"]
        _require(isinstance(users, list))
        for user in users:
            _require(set(user) <= {"userid", "name", "department", "open_userid"})
            _require(_text(user["userid"]) and _text(user["name"]))
            _require(isinstance(user["department"], list) and user["department"])
            _require(all(type(d) is int and d > 0 for d in user["department"]))
            clean = {k: user[k] for k in ("userid", "name", "department")}
            _require(user["userid"] not in members or members[user["userid"]] == clean)
            members[user["userid"]] = clean
    ids, cursors, cursor = set(), set(), ""
    for _ in range(20):
        page = _request(
            "POST",
            "user/list_id",
            params={"access_token": directory},
            json={"cursor": cursor, "limit": 10000},
        )
        _require(isinstance(page["dept_user"], list))
        for row in page["dept_user"]:
            _require(_text(row["userid"]) and type(row["department"]) is int)
            ids.add(row["userid"])
        cursor = page["next_cursor"]
        _require(isinstance(cursor, str) and cursor not in cursors)
        if not cursor:
            _require(ids and set(members) <= ids)
            return depts, members, ids
        cursors.add(cursor)
    raise ValueError("incomplete_directory")


def _apply(cfg, depts, members, ids):
    result = dict(updated=0, left=0, pending=0, skipped=0)
    mapped: dict[int, str] = {}
    pending = dict(depts)
    while pending:
        progress = False
        for key, row in list(pending.items()):
            if row["parentid"] in pending:
                continue
            progress = True
            del pending[key]
            if "," in row["name"] or (row["parentid"] and row["parentid"] not in mapped):
                result["skipped"] += 1
                continue
            name = frappe.db.get_value("Department", {"eb_wecom_department_id": str(key)}, "name")
            doc = frappe.get_doc("Department", name) if name else frappe.new_doc("Department")
            _require(not name or doc.company == cfg["company"])
            values = dict(
                company=cfg["company"],
                department_name=row["name"],
                eb_wecom_department_id=str(key),
                parent_department=mapped.get(row["parentid"]),
                is_group=1,
            )
            if doc.is_new() or any(doc.get(k) != v for k, v in values.items()):
                doc.update(values).save(ignore_permissions=True)
            mapped[key] = doc.name
        if not progress:
            result["skipped"] += len(pending)
            break
    employees = frappe.get_all(
        "Employee",
        filters={"company": cfg["company"]},
        fields=["name", "wecom_userid", "status", "department"],
    )
    bound = [e.wecom_userid for e in employees if e.wecom_userid]
    _require(len(bound) == len(set(bound)))
    result["pending"] = len(set(members) - set(bound))
    for employee in employees:
        uid = employee.wecom_userid
        if not uid or employee.status == "Left":
            continue
        if uid not in ids:
            if cfg.get("full_directory_verified") is not True:
                result["skipped"] += 1
                continue
            doc = frappe.get_doc("Employee", employee.name)
            # 原生 save 要求离职日期/解除下属；此处只撤销访问，HR 后续补离职手续。
            doc.db_set("status", "Left")
            disable_left_user(doc)
            result["left"] += 1
        elif employee.status == "Active" and uid in members:
            row = members[uid]
            targets = [mapped.get(d) for d in set(row["department"])]
            if len(targets) != 1 or not targets[0]:
                result["skipped"] += 1
                continue
            # 不调用 Employee.on_update：它会把人工停用的 Active 用户重新启用。
            values = dict(
                first_name=row["name"],
                middle_name=None,
                last_name=None,
                employee_name=row["name"],
                department=targets[0],
            )
            old = frappe.db.get_value("Employee", employee.name, list(values), as_dict=True)
            if any(old[k] != v for k, v in values.items()):
                frappe.db.set_value("Employee", employee.name, values)
                frappe.clear_document_cache("Employee", employee.name)
                result["updated"] += 1
    return result


def hourly():
    cfg = frappe.conf.get("eb_wecom_sync")
    if not isinstance(cfg, dict) or cfg.get("enabled") is not True:
        return {"disabled": True}
    result = {"failed": True}
    messages = list(frappe.local.message_log or [])
    try:
        with frappe.cache.lock(
            frappe.cache.make_key("eb-org-import"), timeout=1800, blocking_timeout=1
        ):
            depts, members, ids = _snapshot(cfg)
            _require(frappe.db.exists("Company", cfg["company"]))
            frappe.db.savepoint("eb_wecom_sync")
            try:
                result = _apply(cfg, depts, members, ids)
            except Exception:
                frappe.db.rollback(save_point="eb_wecom_sync")
                result = {"failed": True}
    except Exception:
        pass
    finally:
        frappe.local.message_log = messages
    if result.get("failed") or result.get("skipped") or result.get("pending"):
        frappe.logger("wecom_sync").warning("通讯录同步需核对：%s", json.dumps(result))
    return result


@frappe.whitelist(methods=["POST"])
def sync_now():
    if frappe.session.user != "Administrator" and "HR Manager" not in frappe.get_roles():
        frappe.throw("仅 HR Manager 可以同步通讯录。", frappe.PermissionError)
    return hourly()
