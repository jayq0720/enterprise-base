"""仅临时 CI：官方 Qdrant 全快照恢复后验证两个集合、别名和向量查询。"""

import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

from restore_postgres import command
from restore_qdrant import recover

HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(base, path, data=None, method=None):
    content = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        base + path, data=content, method=method, headers={"Content-Type": "application/json"}
    )
    return HTTP.open(req, timeout=60)


def endpoint(container):
    ports = json.loads(
        command(["docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", container])
    )
    base = "http://127.0.0.1:" + ports["6333/tcp"][0]["HostPort"]
    for _ in range(60):
        try:
            with request(base, "/readyz"):
                return base
        except OSError:
            time.sleep(1)
    raise TimeoutError("Qdrant CI 未就绪")


def main():
    tag = json.loads(Path("deploy/upstream.lock.json").read_text())["dify_images"]["qdrant"]
    command(["docker", "pull", tag])
    image = command(["docker", "image", "inspect", "--format", "{{.Id}}", tag]).decode().strip()
    name = "eb-ci-qdrant-" + uuid4().hex
    volume, containers = name + "-data", []
    command(["docker", "volume", "create", volume])
    started = time.monotonic()
    try:
        for target in (False, True):
            options = (
                ["--mount", f"type=volume,src={volume},dst=/qdrant/storage,volume-nocopy"]
                if target
                else []
            )
            container = (
                command(
                    [
                        "docker",
                        "create",
                        "--log-driver",
                        "none",
                        "-p",
                        "127.0.0.1::6333",
                        *options,
                        "--entrypoint",
                        "/qdrant/qdrant",
                        image,
                        "--disable-telemetry",
                    ]
                )
                .decode()
                .strip()
            )
            containers.append(container)
        command(["docker", "start", containers[0]])
        base = endpoint(containers[0])
        for index, collection in enumerate(("alpha", "beta"), 1):
            with request(
                base,
                "/collections/" + collection,
                dict(vectors=dict(size=2, distance="Cosine")),
                "PUT",
            ):
                pass
            with request(
                base,
                "/collections/" + collection + "/points?wait=true",
                dict(points=[dict(id=index, vector=[1.0, 0.0], payload=dict(probe="synthetic"))]),
                "PUT",
            ):
                pass
        with request(
            base,
            "/collections/aliases",
            dict(
                actions=[dict(create_alias=dict(collection_name="alpha", alias_name="probe_alias"))]
            ),
            "POST",
        ):
            pass
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "qdrant.snapshot"
            with request(base, "/snapshots?wait=true", method="POST") as response:
                filename = json.load(response)["result"]["name"]
            with request(base, "/snapshots/" + filename) as response:
                snapshot.write_bytes(response.read())
            snapshot.chmod(0o600)
            recover(image, volume, snapshot)
            try:
                recover(image, volume, snapshot)
            except subprocess.CalledProcessError:
                pass
            else:
                raise AssertionError("再次写入非空卷必须拒绝")
        command(["docker", "start", containers[1]])
        base = endpoint(containers[1])
        with request(base, "/collections") as response:
            assert {c["name"] for c in json.load(response)["result"]["collections"]} == {
                "alpha",
                "beta",
            }
        for name, expected in (("alpha", 1), ("beta", 2), ("probe_alias", 1)):
            with request(
                base,
                "/collections/" + name + "/points/search",
                dict(vector=[1.0, 0.0], limit=1, with_payload=True),
                "POST",
            ) as response:
                point = json.load(response)["result"][0]
                assert point["id"] == expected and point["payload"] == {"probe": "synthetic"}
        print(
            "Qdrant 原生演练：双集合、别名、向量检索与合成 payload 一致，"
            f"通过，耗时 {time.monotonic() - started:.1f} 秒。"
        )
    finally:
        if containers:
            command(["docker", "rm", "-f", "-v", *containers])
        command(["docker", "volume", "rm", volume])


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Qdrant 原生演练:失败:" + type(error).__name__)
        sys.exit(1)
