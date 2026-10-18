"""服务器私有配置经 stdin 写入站点；不在命令参数、输出中传递密钥。"""

import base64
import json
import re
import subprocess
import sys
from pathlib import Path


def settings(path):
    lines = [
        line.split("=", 1)
        for line in Path(path).read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    ]
    if len({k for k, v in lines}) != len(lines) or Path(path).stat().st_mode & 0o077:
        raise ValueError
    return dict(lines)


def configuration(values):
    mode = values.get("EB_WECOM_CALLBACK_MODE", "bridge")
    if mode == "bridge":
        return {"enabled": False}
    if mode != "approvals" or values.get("EB_SCHEME") != "https":
        raise ValueError
    keys = {
        "corpid": "WECHATCOM_CORP_ID",
        "agentid": "WECHATCOMAPP_AGENT_ID",
        "app_secret": "WECHATCOMAPP_SECRET",
        "token": "WECHATCOMAPP_TOKEN",
        "aes_key": "WECHATCOMAPP_AES_KEY",
    }
    cfg = {key: values[source] for key, source in keys.items()}
    if (
        not all(cfg.values())
        or not values.get("DIFY_API_KEY")
        or values["DIFY_API_KEY"] == "CHANGE_ME"
        or any(v == "CHANGE_ME" for v in cfg.values())
        or not re.fullmatch(r"[1-9][0-9]*", cfg["agentid"])
    ):
        raise ValueError
    key = cfg["aes_key"]
    if (
        not re.fullmatch(r"[A-Za-z0-9+/]{43}", key)
        or len(base64.b64decode(key + "=", validate=True)) != 32
    ):
        raise ValueError
    domain = values["EB_DOMAIN"]
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", domain):
        raise ValueError
    return dict(cfg, enabled=True, desk_url="https://hr." + domain)


APPLY = """import json, os, pathlib, sys, tempfile
import frappe
site = sys.argv[1]
frappe.init(site=site, sites_path="sites")
frappe.connect()
cfg = json.load(sys.stdin)
if cfg["enabled"]:
    import enterprise_base.wecom.crypto, enterprise_base.wecom.card_actions
    import enterprise_base.wecom.card_delivery
    meta = frappe.get_meta("EB Approval Card")
    assert meta.has_field("update_status") and meta.get_field("sent_at").fieldtype == "Data"
    assert frappe.db.get_value("Workflow", "EB Leave Review", "is_active")
    events = frappe.get_hooks("doc_events").get("Leave Application", {}).get("on_update", [])
    assert "enterprise_base.wecom.card_delivery.on_update" in events
    assert "enterprise_base.wecom.callback.guard" in frappe.get_hooks("before_request")
path = pathlib.Path("sites") / site / "site_config.json"
data = json.loads(path.read_text())
data["eb_wecom_cards"] = cfg
if cfg["enabled"]:
    data.update(enable_frappe_logger=False, monitor=False)
with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as target:
    json.dump(data, target)
os.chmod(target.name, 0o600)
os.replace(target.name, path)
frappe.clear_cache()
frappe.destroy()
"""


def main():
    try:
        if len(sys.argv) not in (2, 3) or (
            len(sys.argv) == 3 and sys.argv[2] not in {"--check", "--network"}
        ):
            raise ValueError
        values = settings(sys.argv[1])
        cfg = configuration(values)
        if len(sys.argv) > 2 and sys.argv[2] == "--check":
            print("企业微信卡片配置格式通过（未输出配置，未修改站点）。")
            return
        if len(sys.argv) > 2 and sys.argv[2] == "--network":
            if cfg["enabled"]:
                result = subprocess.run(
                    [
                        "docker",
                        "compose",
                        "--env-file",
                        sys.argv[1],
                        "-f",
                        "docker-compose.yml",
                        "--profile",
                        "wechat",
                        "up",
                        "-d",
                        "wechat",
                    ],
                    capture_output=True,
                    check=False,
                )
                if result.returncode:
                    raise ValueError
            return
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                sys.argv[1],
                "-f",
                "data/frappe/eb-compose.yaml",
                "exec",
                "-T",
                "backend",
                "env/bin/python",
                "-c",
                APPLY,
                "hr." + values["EB_DOMAIN"],
            ],
            input=json.dumps(cfg),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise ValueError
        print("已应用企业微信卡片私有配置；仍须重启服务并做联测。")
    except (OSError, ValueError, KeyError, IndexError):
        sys.exit(
            "企业微信部署检查未通过（含 bridge）；检查私有文件600权限/重复键、模式必填值、前置PR及服务。原始输出已隐藏。"
        )


if __name__ == "__main__":
    main()
