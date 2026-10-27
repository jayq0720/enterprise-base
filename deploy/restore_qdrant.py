"""使用锁定 Qdrant 原生全存储快照恢复到新项目空卷。"""

import json
import re
import time
from pathlib import Path

import restore_postgres as pg
from backup import private
from backup_collect import LABEL, CollectionError


def recover(image, volume, snapshot, run=pg.command, sleep=time.sleep):
    """内部执行部分；无网络、关闭遥测，不导入业务环境变量。"""
    private(Path(snapshot))
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*", volume
    ):
        raise ValueError("镜像或卷名称无效")
    mount = f"type=volume,src={volume},dst=/qdrant/storage,volume-nocopy"
    common = ["docker", "create", "--network", "none", "--log-driver", "none", "--user", "0:0"]
    probe = (
        run(
            [
                *common,
                "--read-only",
                "--mount",
                mount + ",readonly",
                "--entrypoint",
                "sh",
                image,
                "-ec",
                'test -z "$(find /qdrant/storage -mindepth 1 -print -quit)"',
            ]
        )
        .decode()
        .strip()
    )
    checked = False
    try:
        run(["docker", "start", "-a", probe])
        checked = True
    finally:
        try:
            run(["docker", "rm", "-f", "-v", probe])
        except Exception:
            if checked:
                raise
    identity = (
        run(
            [
                *common,
                "--mount",
                mount,
                "--env",
                "QDRANT__CLUSTER__ENABLED=false",
                "--entrypoint",
                "/qdrant/qdrant",
                image,
                "--disable-telemetry",
                "--storage-snapshot",
                "/recover.snapshot",
            ]
        )
        .decode()
        .strip()
    )
    succeeded = False
    try:
        # 仅复制到本次临时容器，退出时连容器可写层一起删，不写宿主机明文副本。
        run(["docker", "cp", str(snapshot), identity + ":/recover.snapshot"])
        run(["docker", "start", identity])
        for _ in range(120):
            state = json.loads(run(["docker", "inspect", identity]))[0]["State"]
            if not state["Running"]:
                raise RuntimeError("Qdrant 恢复进程退出")
            try:
                # 固定版本在原生快照恢复及服务启动后才写此就绪标记。
                run(["docker", "exec", identity, "test", "-f", "/qdrant/.qdrant-initialized"])
                break
            except Exception:
                sleep(5)
        else:
            raise TimeoutError("Qdrant 恢复超时")
        run(["docker", "stop", "--time", "60", identity])
        state = json.loads(run(["docker", "inspect", identity]))[0]["State"]
        if state["Running"] or state["ExitCode"] != 0:
            raise RuntimeError("Qdrant 未正常退出")
        succeeded = True
    finally:
        try:
            run(["docker", "rm", "-f", "-v", identity])
        except Exception:
            if succeeded:
                raise


def restore(project, manifest, postgres, container, snapshot, run=pg.command, sleep=time.sleep):
    """在 restore_guard.prepared 锁内调用；只接受停止的新项目 Qdrant。"""
    try:
        pg.target(project, manifest, postgres, run)
        if not re.fullmatch(r"[a-f0-9]{64}", container):
            raise ValueError("需要完整 Qdrant 容器 ID")
        item = json.loads(run(["docker", "inspect", container]))[0]
        labels, state = item["Config"]["Labels"], item["State"]
        image = manifest["images"]["-dify/qdrant"]["image_id"]
        if (
            item["Id"] != container
            or labels.get(LABEL + "project") != project + "-dify"
            or labels.get(LABEL + "service") != "qdrant"
            or labels.get(LABEL + "oneoff", "false").lower() == "true"
            or state["Running"]
            or state.get("Paused")
            or state.get("Restarting")
            or item["Image"] != image
            or item["Config"].get("User", "") not in ("", "0", "0:0")
        ):
            raise ValueError("目标 Qdrant 身份、版本或停止状态无效")
        mounts = [m for m in item["Mounts"] if m["Destination"] == "/qdrant/storage"]
        if len(mounts) != 1 or mounts[0]["Type"] != "volume" or not mounts[0]["RW"]:
            raise ValueError("Qdrant 必须使用新命名卷")
        volume = mounts[0]["Name"]
        info = json.loads(run(["docker", "volume", "inspect", volume]))[0]
        if (
            info["Name"] != volume
            or (info.get("Labels") or {}).get(LABEL + "project") != project + "-dify"
        ):
            raise ValueError("Qdrant 卷不属于新项目")
        recover(image, volume, snapshot, run, sleep)
    except Exception as error:
        raise CollectionError(f"Qdrant:{type(error).__name__}") from None
