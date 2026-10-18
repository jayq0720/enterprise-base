"""只调企业微信官方发送/更新接口；禁止泄露 HTTP 原始异常。"""

import hashlib

import frappe
import requests

from enterprise_base.wecom.login import _get


class CardAPIError(ValueError):
    def __init__(self):
        super().__init__("企业微信卡片接口调用失败。")


def settings():
    cfg = frappe.conf.get("eb_wecom_cards")
    if not isinstance(cfg, dict) or cfg.get("enabled") is not True:
        return None
    if not all(isinstance(cfg.get(k), str) and cfg[k] for k in ("corpid", "agentid", "app_secret")):
        raise CardAPIError()
    if not cfg["agentid"].isdigit() or int(cfg["agentid"]) <= 0:
        raise CardAPIError()
    return cfg


def request(path, body):
    cfg = settings()
    if not cfg or path not in {"message/send", "message/update_template_card"}:
        raise CardAPIError()
    cache = (
        "eb-wecom-card-token:"
        + hashlib.sha256((cfg["corpid"] + ":" + cfg["app_secret"]).encode()).hexdigest()
    )
    for attempt in range(2):
        token = frappe.cache.get_value(cache)
        if not token:
            auth = _get("gettoken", {"corpid": cfg["corpid"], "corpsecret": cfg["app_secret"]})
            token, expiry = auth.get("access_token"), auth.get("expires_in")
            if (
                not isinstance(token, str)
                or not token
                or not isinstance(expiry, int)
                or expiry <= 120
            ):
                raise CardAPIError()
            frappe.cache.set_value(cache, token, expires_in_sec=min(expiry - 120, 6600))
        try:
            response = requests.post(
                "https://qyapi.weixin.qq.com/cgi-bin/" + path,
                params={"access_token": token},
                json={**body, "agentid": int(cfg["agentid"])},
                timeout=(3.05, 10),
                allow_redirects=False,
            )
            result = response.json() if response.status_code == 200 else None
            if not isinstance(result, dict):
                raise CardAPIError()
            if result.get("errcode") in {40014, 42001} and attempt == 0:
                frappe.cache.delete_value(cache)
                continue
            if result.get("errcode") == 0 and not any(
                result.get(k)
                for k in ("invaliduser", "invalidparty", "invalidtag", "unlicenseduser")
            ):
                return result
        except (requests.RequestException, ValueError):
            raise CardAPIError() from None
        break
    raise CardAPIError()
