"""发布与回滚命令行；不接受自动绕过数据库恢复确认。"""

import argparse
import os
import sys
from pathlib import Path

from backup_collect import CollectionError
from release import ROOT, DatabaseRestoreRequired, InteractiveTerminalRequired, release, rollback


def main():
    parser = argparse.ArgumentParser(description="手动发布和回滚；迁移回滚需二次确认")
    parser.add_argument("action", choices=("release", "rollback"))
    parser.add_argument("--revision", default="")
    parser.add_argument("--record")
    parser.add_argument("--identity")
    parser.add_argument("--restore-data", action="store_true")
    parser.add_argument("--env", default=str(ROOT / "secrets/.env"))
    args = parser.parse_args()
    if args.action == "release" and not args.revision:
        parser.error("release必须指定REVISION为完整main提交号")
    if Path(args.env).resolve() != (ROOT / "secrets/.env").resolve():
        parser.error("发布和回滚只接受当前部署默认secrets/.env")
    os.umask(0o077)
    try:
        release(args.revision) if args.action == "release" else rollback(
            args.record or None, args.identity or None, args.restore_data
        )
    except Exception as error:
        if isinstance(error, DatabaseRestoreRequired):
            print("回滚:需RESTORE_DATA=1及离线IDENTITY，并在交互终端完成二次确认", file=sys.stderr)
            return 1
        if isinstance(error, InteractiveTerminalRequired):
            print("回滚:失败:数据库恢复必须在交互终端完成两次确认", file=sys.stderr)
            return 1
        print(
            args.action
            + ":失败:"
            + (str(error) if isinstance(error, CollectionError) else type(error).__name__)
            + ";如已生成发布记录，按记录执行make rollback",
            file=sys.stderr,
        )
        return 1
    print(args.action + ":成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
