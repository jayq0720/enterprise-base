"""仅临时无网络容器：验证PostgreSQL覆盖恢复及目标口令保留。"""

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from restore_postgres import command
from restore_postgres_existing import apply


def main():
    os.umask(0o077)
    name = "eb-pg-rollback-" + uuid4().hex[:12]
    env = {**os.environ, "POSTGRES_PASSWORD": secrets.token_urlsafe(32)}
    created = False
    try:
        command(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--network",
                "none",
                "--log-driver",
                "none",
                "--env",
                "POSTGRES_PASSWORD",
                "--env",
                "POSTGRES_USER=postgres",
                "postgres:15.18-alpine",
            ],
            env=env,
        )
        created = True
        for _ in range(60):
            try:
                command(["docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres"])
                break
            except Exception:
                time.sleep(1)
        else:
            raise TimeoutError

        def sql(database, query):
            return command(
                [
                    "docker",
                    "exec",
                    name,
                    "psql",
                    "-X",
                    "-U",
                    "postgres",
                    "-d",
                    database,
                    "-At",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-c",
                    query,
                ]
            )

        sql("postgres", "CREATE DATABASE synthetic_app")
        with tempfile.TemporaryDirectory() as folder:
            payload = Path(folder)
            databases = ["synthetic_app", "postgres"]
            for index, database in enumerate(databases):
                sql(
                    database,
                    "CREATE TABLE release_probe(value integer); "
                    "INSERT INTO release_probe VALUES(1)",
                )
                (payload / f"postgres-{index}.dump").write_bytes(
                    command(["docker", "exec", name, "pg_dump", "-U", "postgres", "-Fc", database])
                )
                sql(
                    database,
                    "UPDATE release_probe SET value=2; CREATE TABLE post_release(value integer)",
                )
            if "--retry-after-failure" in sys.argv[1:]:
                # 只对本脚本新建的无网络合成数据库故意喂坏dump；原备份保持不变。
                damaged = payload / "damaged"
                damaged.mkdir()
                (damaged / "postgres-0.dump").write_bytes(b"invalid synthetic dump")
                try:
                    apply(name, damaged, databases, command)
                except subprocess.CalledProcessError:
                    pass
                else:
                    raise AssertionError("损坏输入未被拒绝")
                if sql("synthetic_app", "SELECT to_regclass('release_probe')").strip():
                    raise AssertionError("没有复现恢复失败后的空库")
                print("PostgreSQL故障注入：原生pg_restore失败后空库已复现，使用原备份重试。")
            apply(name, payload, databases, command)
            for database in databases:
                restored = json.loads(
                    sql(
                        database,
                        "SELECT json_build_object('value',(SELECT value FROM release_probe),"
                        "'extra',to_regclass('post_release'))",
                    )
                )
                if restored != {"value": 1, "extra": None}:
                    raise ValueError
            command(
                [
                    "docker",
                    "exec",
                    "--env",
                    "PGPASSWORD",
                    name,
                    "psql",
                    "-X",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "postgres",
                    "-Atc",
                    "SELECT 1",
                ],
                env={**env, "PGPASSWORD": env["POSTGRES_PASSWORD"]},
            )
        print(
            "PostgreSQL回滚原生演练：含postgres库的双数据库回退、新表移除、数据恢复及目标口令保留通过。"
        )
        return 0
    except Exception as error:
        print("PostgreSQL回滚原生演练:失败:" + type(error).__name__)
        return 1
    finally:
        if created:
            command(["docker", "rm", "-f", "-v", name])


if __name__ == "__main__":
    sys.exit(main())
