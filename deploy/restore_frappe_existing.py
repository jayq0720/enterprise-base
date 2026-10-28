"""仅供已二次确认的回滚，在停写期间恢复已有站点；不提供独立公开CLI。"""

import json
import os
import re
import shutil
import sys
from pathlib import Path

from backup import private
from restore_frappe import RestoreError, command, settings
from restore_guard import SITE_FILES, site_settings


def restore(site, source, root_password, admin_password, run=command):
    if site == "all" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,252}", site):
        raise ValueError("回滚站点名无效")
    source, target = Path(source).absolute(), Path("sites") / site
    if any(p.is_symlink() for p in (source, *source.parents, target, Path("sites"))):
        raise ValueError("站点与输入目录不能是链接")
    if (
        not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(source))
        or not root_password
        or not admin_password
    ):
        raise ValueError("回滚输入无效")
    private(source, True)
    for name in SITE_FILES:
        private(source / name)
        if not (source / name).stat().st_size:
            raise ValueError("回滚输入不能为空")
    config = target / "site_config.json"
    if config.is_symlink() or not config.is_file():
        raise ValueError("回滚只接受已存在的目标站点")
    current = json.loads(config.read_text())
    original = json.loads((source / "site_config.json").read_text())
    result = site_settings(original, current)
    key = result.get("backup_encryption_key")
    if key and not re.fullmatch(r"[A-Za-z0-9_=-]{1,256}", key):
        raise ValueError("备份解密参数无效")
    for folder in (target / "public/files", target / "private/files"):
        if folder.is_symlink() or folder.parent.is_symlink():
            raise ValueError("附件目录不能是链接")
    step = "配置"
    try:
        settings(config, result)
        # 先去掉备份时间点之后新增的附件，避免只解包留下多余文件。
        step = "附件"
        for folder in (target / "public/files", target / "private/files"):
            if folder.exists():
                shutil.rmtree(folder)
        step = "数据库"
        run(
            [
                "bench",
                "--site",
                site,
                "restore",
                str(source / "database.sql.gz"),
                "--force",
                "--db-root-password",
                root_password,
                "--admin-password",
                admin_password,
                "--with-public-files",
                str(source / "files.tar"),
                "--with-private-files",
                str(source / "private-files.tar"),
            ]
        )
        restored = json.loads(config.read_text())
        if any(restored.get(k) != current[k] for k in ("db_name", "db_password")):
            raise ValueError("回滚改写了目标数据库身份")
        settings(config, site_settings(original, current))
        step = "迁移"
        run(["bench", "--site", site, "migrate"])
        step = "管理员"
        run(
            ["bench", "--site", site, "set-admin-password", admin_password, "--logout-all-sessions"]
        )
        # 成功后恢复目标原先的通知配置；密钥和连接信息不从源配置覆盖。
        result.update(
            pause_scheduler=current.get("pause_scheduler", 0),
            mute_emails=current.get("mute_emails", 0),
        )
        result["eb_wecom_cards"] = current.get("eb_wecom_cards", {"enabled": False})
        settings(config, result)
    except Exception as error:
        raise RestoreError(f"Frappe回滚:{step}:{type(error).__name__}") from None
    finally:
        failed = sys.exception() is not None
        for name in ("files.tar", "private-files.tar"):
            try:
                (target / name).unlink(missing_ok=True)
            except OSError:
                if not failed:
                    raise RestoreError("Frappe回滚:清理:OSError") from None


if __name__ == "__main__":
    os.umask(0o077)
    try:
        if len(sys.argv) != 3:
            raise ValueError("只供维护入口传入站点及私有输入目录")
        if os.environ.get("EB_RESTORE_EXISTING_APPROVED") != sys.argv[1]:
            raise ValueError("只接受二次确认维护入口的明确授权")
        restore(
            sys.argv[1],
            sys.argv[2],
            os.environ["EB_RESTORE_DB_ROOT_PASSWORD"],
            os.environ["EB_RESTORE_ADMIN_PASSWORD"],
        )
    except Exception as error:
        print(
            str(error) if isinstance(error, RestoreError) else "Frappe回滚:" + type(error).__name__
        )
        sys.exit(1)
