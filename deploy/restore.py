"""恢复到隔离新项目；任何失败停用本次容器并清理明文暂存。"""

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import restore_guard as guard
import restore_workspace as ws
from backup_collect import CollectionError
from restore_runtime import Target

ROOT = Path(__file__).resolve().parent


def restore(archive, identity, project, port):
    target = None
    destination = ROOT / "data/restores" / project

    def failed():
        if target:
            pending = target.stop()
            try:
                ws.write(
                    target.deploy / "data/restore-result.json",
                    json.dumps(dict(state="failed", project=project, stop_failed=pending)),
                )
            except Exception:
                print("恢复:失败状态写入失败", file=sys.stderr)
            if pending:
                print("恢复:停服失败:" + ",".join(pending), file=sys.stderr)

    with guard.prepared(archive, identity, project, destination, on_error=failed) as data:
        manifest, stage, sites, _databases = data
        deploy, primary = ws.create(
            ROOT.parent, manifest, stage / "backup/secrets", sites, project, destination, port
        )
        target = Target(deploy, project)
        target.restore(manifest, stage, sites, primary, port)
        ws.write(
            deploy / "data/restore-result.json",
            json.dumps(dict(state="ready", project=project, port=port)),
        )


def main():
    parser = argparse.ArgumentParser(description="恢复到新的隔离项目；不覆盖原环境")
    parser.add_argument("--backup", required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--project", default="")
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()
    if not args.backup or not args.identity:
        parser.error("必须指定 BACKUP 和 IDENTITY 文件")
    project = args.project or "restore-" + uuid4().hex[:12]
    os.umask(0o077)
    try:
        restore(args.backup, args.identity, project, args.port)
    except Exception as error:
        print(
            "恢复:失败:"
            + (str(error) if isinstance(error, CollectionError) else type(error).__name__),
            file=sys.stderr,
        )
        return 1
    print(f"恢复:成功:{project}:127.0.0.1:{args.port}；队列、定时任务及企业微信保持停用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
