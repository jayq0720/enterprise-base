# P0 本地部署与交接

目标：镜像准备好后 30 分钟内可登录；首次下载、Frappe 冷构建耗时另计，尚待 JQ 实测。建议 Linux amd64、4 核、16 GB 内存、60 GB 可用磁盘；安装 Docker Engine、Compose ≥2.24、Git、make、uv。macOS/ARM 需要 amd64 仿真，不能据此承诺耗时。域名和凭据只在服务器配置。

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

浏览器打开 `http://dify.localhost/install` 创建管理员，打开 `http://hr.localhost` 用 `Administrator` 和服务器 `secrets/.env` 中的 `EB_FRAPPE_ADMIN_PASSWORD` 登录。若系统不解析子域名，将 `dify.localhost hr.localhost wecom.localhost` 加入 hosts，指向 `127.0.0.1`。Frappe 的公司向导先用测试公司，真实组织配置在 G3/G4 导出 fixtures。

## 企业微信入口
在 Dify 后台创建 Chatflow，API Key 只填进服务器 `secrets/.env` 的 `DIFY_API_KEY`；填写该文件所有 `WECHAT*` 项，AES Key 为企业微信后台给出的 43 位值，Agent ID 用纯数字。然后：
```sh
make wechat
```
企业微信后台回调路径为 `/wxcomapp`。真实回调需要公网 HTTPS 域名和可信 IP；先在本机完成两个管理员初始化，再把 `EB_DOMAIN` 改成客户域名、`EB_SCHEME=https`、`EB_BIND=0.0.0.0`，配置三个子域名 DNS，并开放 80/443，重新 `make up`。Caddy 自动签发证书，配置客户域名前先确认站点数据是可丢弃的测试环境；域名变化会创建新的 Frappe 站点。公网测试域名、备案要求由 JQ 确认（清单 6.2）。

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

三套 Compose 的项目名分别是 `${EB_PROJECT}`、`${EB_PROJECT}-dify`、`${EB_PROJECT}-hr`，数据库/向量/附件/队列/证书均用命名卷；sandbox conf 是只读配置挂载。`deploy/data/` 是可重新生成的上游配置，`deploy/secrets/` 需随备份加密保存；正常重启不修改项目名，不运行 `down -v`。本任务不包含自动备份或升级，升级时先备份数据库、文件卷和密钥，固定新版本，在独立测试项目完整验证后再迁移。

## 当前验证边界
开发环境没有 Docker Engine 和服务器凭据，仅运行官方 Compose 2.39.4 的配置校验；镜像构建、干净机器 30 分钟目标、登录、持久化、真实企业微信回调均等待上述服务器实测。CI 自动运行 `make verify`，在临时目录检查完整三套配置及部署约束，不读取或覆盖已有运行密钥；`make config` 用于检查当前实际部署配置。G1 通过后按 G2 验证三个官方模型插件，再按 G3/G4 配置组织、审批与 `wecom_userid` 并导出 fixtures；结果不能用配置校验替代。
