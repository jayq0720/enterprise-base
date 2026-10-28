# P0 本地部署与交接

目标：镜像准备好后 30 分钟内可登录；首次下载、Frappe 冷构建耗时另计，尚待 JQ 实测。建议 Linux amd64、4 核、16 GB 内存、60 GB 可用磁盘；安装 Docker Engine ≥28、Compose ≥2.24、Git、make、uv。macOS/ARM 需要 amd64 仿真，不能据此承诺耗时。域名和凭据只在服务器配置。

## 启动
在已合并部署代码的仓库根目录执行。
```sh
cd deploy
make init
# 在服务器编辑 secrets/.env；本地默认 localhost + HTTP + 仅监听 127.0.0.1。
make up
make status
```
`make init` 自动生成随机密钥，重复执行保留原值；所有密钥文件权限 600。环境文件仅支持 `KEY=单行值`，不写引号或变量展开。`make up` 首次用官方 frappe_docker 构建 HR 镜像；`make build` 可提前构建。构建失败可重试，勿删除数据卷。只有 HRMS、Wiki 作为附加功能安装；ERPNext 是 HRMS 官方必需依赖。

HR 助手在建站、启用专用技术账号并录入凭据后，以 `make hr-assistant` 启动 X3/X4；X5 仍用 `make policy-sync`。旧环境先执行 `make init`，补齐新配置及三项独立随机密钥，不覆盖已有值。Dify 工具、应用导入及完整服务器验收见 [HR 助手部署与验收](../docs/manual/P1-HR助手部署与验收.md)。

浏览器打开 `http://dify.localhost/install` 创建管理员，打开 `http://hr.localhost` 用 `Administrator` 和服务器 `secrets/.env` 中的 `EB_FRAPPE_ADMIN_PASSWORD` 登录。若系统不解析子域名，将 `dify.localhost hr.localhost wecom.localhost` 加入 hosts，指向 `127.0.0.1`。Frappe 的公司向导先用测试公司，真实组织配置在 G3/G4 导出 fixtures。

`make prepare` 从仓库 `Caddyfile` 模板生成 `data/Caddyfile`，Compose 只读挂载生成文件。仅当实际端口绑定为 `127.0.0.1` 时移除 Dify/HR 的 `import admin_only`，依靠宿主机回环端口隔离；此时 Docker 将来源改写成网桥网关也能完成本机初始化。其他绑定一律启用白名单，包括内网地址和 IPv6 回环。不要把网桥网关加入白名单。每次改 `EB_BIND` 都通过 `make up` 重新生成、重建入口，不直接复用旧生成文件；`data/` 不提交。

## 旧测试环境修复（PR #9 发现的启动问题）

新环境直接 `make init`。已初始化的环境不会自动覆盖密钥；若检查提示 Agent 密钥格式错误，先在 `deploy/` 执行 `make down`，再运行以下命令，仅替换旧版生成的 64 位十六进制 Agent 密钥，其他配置原样保留。旧文件以 600 权限保留在 `secrets/.env.before-agent-key`；已有该备份时命令拒绝覆盖。密钥变化会使原 Agent Stub 令牌失效，需重新发起测试会话。

```sh
uv run --python 3.12 python - <<'PY'
import os, re, secrets
from pathlib import Path
os.umask(0o077)
path = Path("secrets/.env")
text = path.read_text()
pattern = r"(?m)^DIFY_AGENT_SERVER_SECRET_KEY=[0-9a-f]{64}$"
if len(re.findall(pattern, text)) != 1:
    raise SystemExit("仅适用于旧版生成的 Agent 密钥；未修改文件。")
backup = path.with_name(".env.before-agent-key")
with backup.open("x") as output:
    output.write(text)
path.chmod(0o600)
path.write_text(re.sub(pattern, lambda _: "DIFY_AGENT_SERVER_SECRET_KEY=" + secrets.token_urlsafe(32), text))
PY
make up
make status
```

不删除 `data/`、命名卷或其它密钥。`make up` 重新生成配置、拉取固定摘要的 Debian Nginx；全局日志保持 `WARNING`，仅插件守护进程用 `WARN`，不要在环境文件把全局改成 `WARN`。站点已经创建时仍会重试应用安装、遥测设置和调度器开启；遥测使用数据库 Single 接口，避免未完成公司向导时触发必填校验。步骤依据 [Claude 实测记录](../docs/p0-results/P0-本地实测-Claude.md)。JQ 转交的 2026-10-15 晚复核确认：四项启动修复在 main e686aa1 从零初始化成功，用时 5 分钟（镜像已缓存）；本次回环入口修复仍须单独进行 Docker 回归。

## 企业微信入口
审批卡片上线时按 [P2 审批卡片部署与验收](../docs/manual/P2-审批卡片部署与验收.md)启用统一入口；默认 `EB_WECOM_CALLBACK_MODE=bridge` 保留现有聊天，完整前置合并和测试前不要切换。
在 Dify 后台创建并发布 Chatflow 后，先按[关闭网页版步骤](../docs/manual/P1-HR助手部署与验收.md#关闭-hr-助手和制度问答的网页版)分别关闭“HR 助手”和“制度问答”的 Web App，保持 API 访问开启。API Key 只填进服务器 `secrets/.env` 的 `DIFY_API_KEY`；填写该文件所有 `WECHAT*` 项，AES Key 为企业微信后台给出的 43 位值，Agent ID 用纯数字。然后：
```sh
make wechat
```
企业微信后台回调路径为 `/wxcomapp`。真实回调需要公网 HTTPS 域名和可信 IP；先在本机完成两个管理员初始化，再把 `EB_DOMAIN` 改成客户域名、`EB_SCHEME=https`、`EB_BIND=0.0.0.0`，并在服务器 `.env` 的 `EB_ADMIN_ALLOW_CIDRS` 填管理员固定出口 IP/CIDR，配置三个子域名 DNS，开放 80/443，重新 `make up`。Caddy 自动签发证书，配置客户域名前先确认站点数据是可丢弃的测试环境；域名变化会创建新的 Frappe 站点。公网测试域名、备案要求由 JQ 确认（清单 6.2）。

`EB_ADMIN_ALLOW_CIDRS` 默认 `127.0.0.1/32`，多个条目用空格分隔，支持 IPv4/IPv6，不加引号；空值、无效 CIDR 和全网 `/0` 拒绝启动。本机模式不使用该值判断来源。旧 `.env` 没有此键时仍用默认值，`make init` 不覆盖已有文件，转为对外绑定前需填管理员真实出口。对外绑定时，Dify 所有路径（包括 `/v1/*`）及 HR 的 `/login`、REST、后台均受限制，未授权来源返回 403；企业微信 `/wxcomapp` 不受此白名单限制，仍由应用校验签名。X3/X4/dify-on-wechat 走内网 `dify` 别名，不受公网入口限制；外部 Service API 和员工扫码登录须在后续独立评审后按需开放路径，不先开放整站。

来源判断使用连接对端 `remote_ip`，不信任客户端的 `X-Forwarded-For`/`Forwarded`。当前方案按直连入口设计；CDN、负载均衡或 Docker Desktop NAT 可能改变对端地址，应先核实实际来源，不能将所有访客共享的代理地址直接列为可信管理员来源。官方行为见 [Caddy remote_ip](https://caddyserver.com/docs/caddyfile/matchers#remote-ip)。

上线前按 [后台入口实测](ADMIN-ACCESS.md) 从白名单内外分别访问，核对 Caddy 实际来源，IPv4/IPv6 分别记录。原生 Caddy 回环检查不经过 Docker 转发，不能替代此验收。回环隔离需 Docker Engine ≥28、默认 bridge/NAT 且未开启容器直路由；旧版的同网段访问问题见 [Docker 官方说明](https://docs.docker.com/engine/network/port-publishing/)。

上游企业微信渠道会打印凭据和消息，因此部署将 stdout/stderr 与 `/app/run.log` 丢弃，Docker 日志驱动设为 `none`；不要临时开启原始日志。附带聊天插件全部禁用，避免切换模型或读取额外数据。故障只反馈容器状态和 HTTP 状态码。启动入口不等于收发已通过：需以测试员工发消息，检查 Chatflow 的 `sys.user_id` 与该员工企业微信 userid 一致，记录“相等/不相等”，不要粘贴真实 userid、Key 或完整运行记录。

## 校验、重启与证据
```sh
make config                 # 校验入口、完整 Dify、完整 Frappe 三套配置，不输出展开后的密钥
make status
curl -s -o /dev/null -w 'Dify HTTP %{http_code}\n' http://dify.localhost/install
curl -s -o /dev/null -w 'Frappe HTTP %{http_code}\n' http://hr.localhost/login
make down                   # 不带 -v；停止三套服务，保留所有命名卷
make up
make status
```
服务器实际域名不同则替换 curl URL。第一次启动后创建一个测试知识库和测试 Frappe 记录；重启后核对仍存在。记录开始/结束北京时间、版本、两次状态摘要、两个 HTTP 状态码及记录是否保留，交 JQ/Claude 复核。不要贴 `docker compose config` 的完整输出、原始日志或 `docker inspect`。启动成功摘要由 JQ 执行后补充，当前未勾选验证清单 1.1/1.4/6.1；无岗位拒绝、fixtures 空库导入在 G3/G4 验证，当前没有新增员工权限接口或 fixtures。

三套 Compose 的项目名分别是 `${EB_PROJECT}`、`${EB_PROJECT}-dify`、`${EB_PROJECT}-hr`，数据库/向量/附件/队列/证书均用命名卷；sandbox conf 是只读配置挂载。`deploy/data/` 是可重新生成的上游配置，`deploy/secrets/` 需随备份加密保存；正常重启不修改项目名，不运行 `down -v`。每日加密备份及定时安装见[每日备份](../docs/manual/P3-每日备份.md)，`make restore` 隔离恢复见[完整恢复](../docs/manual/P3-完整恢复.md)。正式覆盖仍未提供；升级时先备份数据库、文件卷和密钥，固定新版本，在独立测试项目完整验证后再迁移。

## 当前验证边界
开发环境没有 Docker Engine 和服务器凭据；镜像构建、干净机器 30 分钟目标、登录、持久化、真实企业微信回调均等待上述服务器实测。CI 自动运行 `make verify`，在临时目录检查完整三套配置及部署约束，并用固定版 Caddy 在随机回环端口短暂验证允许/403/伪造请求头行为，测试后端仅返回固定响应，不访问业务服务。校验不读取或覆盖已有运行密钥；`make config` 用于检查当前实际部署配置。G1 通过后按 G2 验证三个官方模型插件，再按 G3/G4 配置组织、审批与 `wecom_userid` 并导出 fixtures；结果不能用这些检查替代。
