# 无服务器时的部署校验

运行 `make -C deploy verify`。需要 Compose、Git、uv；不需要 Docker Engine，也不会构建、拉取运行镜像或启动容器。首次下载固定提交的官方部署源和 Caddy 2.10.2 校验程序需要网络；Caddy 官方发布包按 `caddy.lock.json` 的 SHA256 验证，版本与部署镜像一致。已下载的源只复用固定提交的 Git 对象，不复制运行文件。

校验在临时目录中使用 `.env.example` 生成独立随机测试密钥，结束后删除该目录，不读取或改写当前 `deploy/secrets/`。可安全检查已有部署旁的代码；`make config` 则仍检查当前部署本身，两者用途不同。

自动检查范围：

- 本机默认、本机白名单不含回环、对外拒绝、对外允许四组完整入口/Dify/Frappe Compose，并往返切换绑定模式，检查生成文件不残留上次策略。
- 镜像为具体语义版本或 SHA256 摘要；数据卷有名字，sandbox 配置绑定只读。
- 只有 Caddy 公开端口，默认仅本机监听；Sandbox 保持隔离网络，Frappe 后端不直接接入入口网络。
- 检查 Compose 实际只读挂载 `data/Caddyfile`，用 Caddy 解析生成文件：精确绑定 `127.0.0.1` 时后台无来源限制；其他绑定必须有 remote_ip 白名单及 403 拒绝，`/v1/*` 不例外。本机误留限制、对外删除限制的反例均须失败。
- Caddy 在随机 127.0.0.1 端口短暂运行，后端替换为固定测试响应，验证允许/拒绝、伪造转发头不能绕过、企业微信回调入口不误封；不连接 Dify/Frappe/企业微信。验证结束即停止该测试进程。
- 项目名、域名、Qdrant 选择及 Sandbox 共享密钥正确传递。
- Debian Nginx 的已核验摘要；插件守护进程 `WARN` 与 API/Celery `WARNING`；Agent 密钥解码后为 32 字节无填充 base64url。回退 Alpine、两种错误日志级别或旧 64 位密钥的反例必须失败。
- 初始化重复执行不覆盖密钥，各服务密钥独立，文件 600/目录 700，生成输出不含密钥。
- X5 两项密钥为 32 字节 base64url；旧配置补钥保留其它项。X5 无宿主机端口，Frappe 只获得签名密钥，专用 API 凭据缺项拒绝，镜像必须来自本仓库 CI 的摘要；使用 policy-sync profile 展开检查网络和环境变量。
- 缺少必填密钥、企业微信凭据缺失或缺整个键时拒绝；moving tag、额外端口、匿名卷三种反例必须被检查器拒绝。
- Frappe 建站 shell 使用参数记录器替代 bench 执行，检查 `frappe.db.set_single_value` 收到 Single/字段名/整数 0，成功后才开启调度器；模拟设置失败时必须停止。该检查不运行 Frappe，实际数据库值和外联行为仍需运行验证。

命令输出只包含检查摘要，异常时隐藏原始子命令输出，避免配置展开后的密钥进入 CI 日志。完整服务配置只在内存中检查，不上传为构建产物。

只有独立 Compose 程序而没有 `docker` 命令时，可设置 `EB_COMPOSE_COMMAND=/绝对路径/docker-compose` 后运行；默认使用 `docker compose`。

已有官方 Caddy 2.10.2 可设置 `EB_CADDY_BINARY=/绝对路径/caddy`；其它版本拒绝，未指定则在临时目录下载已锁定摘要的官方程序。验证平台支持 Linux/macOS 的 amd64/arm64。

原生 Caddy 探测不经过 Docker 的 userland-proxy/NAT，也没有验证真实 IPv6 客户端。上线前仍须按 [ADMIN-ACCESS.md](ADMIN-ACCESS.md) 实测本机初始化、远端允许/403 和 Caddy 来源 IP；不能因配置校验通过就将公网来源校验记为通过。

不覆盖的验收：镜像构建、登录、30 分钟目标、重启保留数据、权限接口的运行拒绝、fixtures 空库导入、真实企业微信或模型调用。JQ 已安排这些测试在服务器准备后完成，不能根据本校验勾选它们。
