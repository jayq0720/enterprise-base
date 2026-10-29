"""已有项目PostgreSQL覆盖恢复；只能由已确认、持锁和停写的维护入口调用。"""

import json
from pathlib import Path

import restore_postgres as pg
from backup import private
from restore_guard import names


def apply(container, payload, databases, run):
    """官方dropdb/createdb/pg_restore；目标账号和密码不从源globals导入。"""
    for index, database in enumerate(databases):
        for utility, options in (
            ("dropdb", "--if-exists --force"),
            ("createdb", '--owner "$POSTGRES_USER"'),
        ):
            run(
                [
                    "docker",
                    "exec",
                    container,
                    "sh",
                    "-ec",
                    f'exec {utility} -U "$POSTGRES_USER" -h /var/run/postgresql '
                    f'--maintenance-db=template1 {options} -- "$1"',
                    "sh",
                    database,
                ]
            )
        run(
            [
                "docker",
                "exec",
                "-i",
                container,
                "sh",
                "-ec",
                'exec pg_restore -U "$POSTGRES_USER" -h /var/run/postgresql '
                "--no-owner --no-privileges --no-tablespaces --single-transaction "
                '--exit-on-error --dbname "$1"',
                "sh",
                database,
            ],
            payload / f"postgres-{index}.dump",
        )


def restore(project, manifest, container, payload, run=pg.command):
    payload = Path(payload)
    private(payload, True)
    private(payload / "databases.json")
    databases = names(
        json.loads((payload / "databases.json").read_text()), r"[A-Za-z0-9_][A-Za-z0-9_-]*"
    )
    if {"template0", "template1"} & set(databases):
        raise ValueError("不能覆盖模板数据库")
    for index in range(len(databases)):
        path = payload / f"postgres-{index}.dump"
        private(path)
        if not path.stat().st_size:
            raise ValueError("数据库备份为空")
    pg.target(project, manifest, container, run, existing=True)
    apply(container, payload, databases, run)
