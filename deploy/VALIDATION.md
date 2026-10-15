# 无服务器时的部署校验

运行 `make -C deploy verify`。需要 Compose、Git、uv；不需要 Docker Engine，也不会构建、拉取运行镜像或启动容器。首次下载固定提交的官方部署源需要网络；已下载的源只复用固定提交的 Git 对象，不复制运行文件。

校验在临时目录中使用 `.env.example` 生成独立随机测试密钥，结束后删除该目录，不读取或改写当前 `deploy/secrets/`。可安全检查已有部署旁的代码；`make config` 则仍检查当前部署本身，两者用途不同。

自动检查范围：

- 默认 localhost/HTTP 与自定义测试域名/HTTPS，两组完整入口、Dify、Frappe Compose。
- 镜像为具体语义版本或 SHA256 摘要；数据卷有名字，sandbox 配置绑定只读。
- 只有 Caddy 公开端口，默认仅本机监听；Sandbox 保持隔离网络，Frappe 后端不直接接入入口网络。
- 项目名、域名、Qdrant 选择及 Sandbox 共享密钥正确传递。
- Debian Nginx 的已核验摘要；插件守护进程 `WARN` 与 API/Celery `WARNING`；Agent 密钥解码后为 32 字节无填充 base64url。回退 Alpine、两种错误日志级别或旧 64 位密钥的反例必须失败。
- 初始化重复执行不覆盖密钥，各服务密钥独立，文件 600/目录 700，生成输出不含密钥。
- 缺少必填密钥、企业微信凭据缺失或缺整个键时拒绝；moving tag、额外端口、匿名卷三种反例必须被检查器拒绝。
- Frappe 建站 shell 使用参数记录器替代 bench 执行，检查 `frappe.db.set_single_value` 收到 Single/字段名/整数 0，成功后才开启调度器；模拟设置失败时必须停止。该检查不运行 Frappe，实际数据库值和外联行为仍需运行验证。

命令输出只包含检查摘要，异常时隐藏原始子命令输出，避免配置展开后的密钥进入 CI 日志。完整服务配置只在内存中检查，不上传为构建产物。

只有独立 Compose 程序而没有 `docker` 命令时，可设置 `EB_COMPOSE_COMMAND=/绝对路径/docker-compose` 后运行；默认使用 `docker compose`。

不覆盖的验收：镜像构建、登录、30 分钟目标、重启保留数据、权限接口的运行拒绝、fixtures 空库导入、真实企业微信或模型调用。JQ 已安排这些测试在服务器准备后完成，不能根据本校验勾选它们。
