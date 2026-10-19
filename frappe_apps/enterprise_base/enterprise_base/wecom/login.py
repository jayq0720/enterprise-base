"""企业微信内部应用扫码登录；只匹配已有、在职且启用的员工账号。"""

import hashlib
import re
import secrets
from urllib.parse import urlencode, urlsplit

import frappe
import requests
from werkzeug.utils import redirect

CALLBACK = "/api/method/enterprise_base.wecom.login.callback"
COOKIE = "__Host-eb_wecom_login"
RANDOM = re.compile(r"[A-Za-z0-9_-]{43}\Z")


def _settings():
    cfg = frappe.conf.get("eb_wecom_login")
    if not isinstance(cfg, dict) or cfg.get("enabled") is not True:
        return None
    if not all(
        isinstance(cfg.get(k), str) and cfg[k]
        for k in ("corpid", "agentid", "app_secret", "callback_url")
    ):
        return None
    try:
        url = urlsplit(cfg["callback_url"])
    except ValueError:
        return None
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.path != CALLBACK
        or url.query
        or url.fragment
    ):
        return None
    return cfg


def _get(path, params):
    # 不向 Frappe 抛出携带 URL/密钥/响应正文的 requests 异常。
    try:
        response = requests.get(
            "https://qyapi.weixin.qq.com/cgi-bin/" + path,
            params=params,
            timeout=(3.05, 10),
            allow_redirects=False,
        )
        if response.status_code == 200:
            body = response.json()
            if isinstance(body, dict) and body.get("errcode", 0) == 0:
                return body
    except (requests.RequestException, ValueError):
        pass
    return {}


def _identity(code):
    cfg = _settings()
    if not cfg:
        return None
    token = _get("gettoken", {"corpid": cfg["corpid"], "corpsecret": cfg["app_secret"]})
    if not isinstance(token.get("access_token"), str) or not token["access_token"]:
        return None
    result = _get("auth/getuserinfo", {"access_token": token["access_token"], "code": code})
    userid = result.get("userid")
    return userid if isinstance(userid, str) and 0 < len(userid) <= 64 else None


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _key(state):
    return frappe.cache.make_key("eb-wecom-login:" + _digest(state))


def _redirect(location):
    response = redirect(location)
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    return response


@frappe.whitelist(allow_guest=True, methods=["GET"])
def start():
    cfg = _settings()
    # 生产 gunicorn 不信任代理转发的协议头，request.scheme 恒为 http；
    # HTTPS 由 callback_url 校验和 __Host- Cookie 保证。
    if not cfg or frappe.request.args:
        return _redirect("/login?wecom=failed")
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    frappe.cache.setex(_key(state), 300, _digest(nonce))
    frappe.local.cookie_manager.set_cookie(
        COOKIE, nonce, max_age=300, secure=True, httponly=True, samesite="Lax"
    )
    query = urlencode(
        dict(
            login_type="CorpApp",
            appid=cfg["corpid"],
            agentid=cfg["agentid"],
            redirect_uri=cfg["callback_url"],
            state=state,
        )
    )
    return _redirect("https://login.work.weixin.qq.com/wwlogin/sso/login?" + query)


def _consume(state):
    nonce = frappe.request.cookies.get(COOKIE, "")
    if not RANDOM.fullmatch(state) or not RANDOM.fullmatch(nonce):
        return False
    # Redis GETDEL 原子消费；普通缓存读取后删除会允许并发重放。
    expected = frappe.cache.getdel(_key(state))
    return bool(expected and secrets.compare_digest(expected.decode(), _digest(nonce)))


def _employee_user(userid):
    rows = frappe.get_all(
        "Employee",
        filters={"wecom_userid": userid},
        fields=["status", "user_id"],
        limit_page_length=2,
    )
    if len(rows) != 1 or rows[0].status != "Active":
        return None
    user = rows[0].user_id
    if not user or user in ("Administrator", "Guest"):
        return None
    enabled, user_type = frappe.db.get_value("User", user, ["enabled", "user_type"]) or (0, "")
    roles = set(frappe.get_roles(user))
    # 系统管理员权限最大，企业微信被盗即可进后台，只允许密码登录（第二轮安全审查 L2）。
    if (
        enabled
        and user_type == "System User"
        and "Employee" in roles
        and not roles.intersection({"EB Integration", "EB X4", "System Manager"})
    ):
        return user
    return None


@frappe.whitelist(allow_guest=True, methods=["GET"])
def callback(code="", state=""):
    failed = _redirect("/login?wecom=failed")
    frappe.local.cookie_manager.set_cookie(COOKIE, "", max_age=0, secure=True, httponly=True)
    args = frappe.request.args
    if (
        set(args) != {"code", "state"}
        or any(len(args.getlist(k)) != 1 for k in args)
        or not isinstance(code, str)
        or not 0 < len(code.encode()) <= 512
        or not isinstance(state, str)
        or not _consume(state)
    ):
        return failed
    userid = _identity(code)
    user = _employee_user(userid) if userid else None
    if not user:
        return failed
    frappe.local.login_manager.login_as(user)
    frappe.db.commit()
    return _redirect("/app")
