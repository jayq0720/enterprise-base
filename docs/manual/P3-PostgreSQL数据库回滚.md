# PostgreSQL 数据库回滚

`restore_postgres_existing.py` 只供已二次确认、持锁且停止应用写入的完整回滚入口调用，不提供独立覆盖命令。原新项目恢复接口仍默认拒绝来源项目；已有库路径必须明确选择，且备份项目必须与当前项目一致。镜像、数据库健康、数据卷归属及无应用写入的检查保留。

先验证数据库索引和每份私有dump，拒绝模板库，再用官方dropdb/createdb重新建立目标库，原生pg_restore恢复对象。维护连接固定template1，因此能恢复postgres库自身；使用目标POSTGRES_USER，不导入源globals、旧账号口令、ACL或表空间。目标数据库被重建，备份之后新增的表和数据都会丢失，不能把此模块用于普通新项目演练。

独立原生CI使用固定postgres:15.18-alpine、无网络且不发布端口的临时容器，在两个库（包含postgres）写入合成数据并备份，修改数据和新增表，再恢复并核对原值、新表消失、目标TCP密码仍有效。只有组件名与通过/失败进入日志；临时容器结束时删除。无服务器时不运行生产覆盖，不把命令协议测试当成原生数据库恢复证据。

官方命令行为见[PostgreSQL 15 dropdb源码](https://github.com/postgres/postgres/blob/REL_15_STABLE/src/bin/scripts/dropdb.c)。无新增依赖，PostgreSQL版本及许可证沿用部署版本表；不改上游或.github/。完整发布/回滚入口和全组件联测仍需接入。
