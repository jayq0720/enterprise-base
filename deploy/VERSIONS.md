# P0-G1 固定版本与许可证

核对日期：2026-10-15。以下为待实测的组合，不代表兼容性验收通过；版本变化必须先在测试环境验证。官方源提交锁在 `upstream.lock.json`，Frappe App 锁在 `apps.lock.json`，不跟随分支。来源分别是各项目官方仓库、上游 Dockerfile 和官方镜像仓库。

| 组件/镜像 | 固定版本或摘要 | 项目许可证 / 官方来源 |
| --- | --- | --- |
| Dify API、Web、Agent backend、Agent local sandbox | 1.17.1；源 `8387590ace4a094de812b7847fc6a4c3a27cd52b` | [Dify Open Source License，基于 Apache-2.0 有附加条件](https://github.com/langgenius/dify/blob/1.17.1/LICENSE) |
| Dify Sandbox | 0.2.15 | [Apache-2.0](https://github.com/langgenius/dify-sandbox/blob/main/LICENSE) |
| Dify Plugin Daemon | 0.6.10-local | [Apache-2.0](https://github.com/langgenius/dify-plugin-daemon/blob/main/LICENSE) |
| Qdrant 官方镜像 `qdrant/qdrant` | v1.15.5 | [Apache-2.0](https://github.com/qdrant/qdrant/blob/v1.15.5/LICENSE) |
| frappe_docker 官方构建定义 | `3556154478dffca8f3bcdee47d43b65d04a2c962` | [MIT](https://github.com/frappe/frappe_docker/blob/3556154478dffca8f3bcdee47d43b65d04a2c962/LICENSE) |
| Frappe / ERPNext / HRMS / Wiki | v15.123.1 / v15.123.0 / v15.64.3 / v3.4.0 | [MIT](https://github.com/frappe/frappe/blob/v15.123.1/LICENSE) / [GPL-3.0](https://github.com/frappe/erpnext/blob/v15.123.0/license.txt) / [GPL-3.0](https://github.com/frappe/hrms/blob/v15.64.3/license.txt) / [MIT](https://github.com/frappe/wiki/blob/v3.4.0/LICENSE) |
| HR 构建工具 Python / Node / Dockerfile frontend | 3.12.12-slim-bookworm / 22.20.0 / 1.19.0 | PSF / MIT / Apache-2.0；使用官方 frappe_docker，不改源码 |
| PostgreSQL / MariaDB / Redis | 15.18-alpine / 10.11.14 / 6.2.20-alpine | PostgreSQL / GPL-2.0 / BSD-3-Clause |
| Nginx（Debian，含 bash） | `nginx:1.28.0@sha256:552e7481ca93ffccd046aa658dbbed22caefbc09c66fa7cd247cbb90b8a5c609` | BSD-2-Clause；[官方镜像](https://hub.docker.com/_/nginx)，摘要为多架构 OCI index，2026-10-15 从官方 Registry 读取并核验 |
| BusyBox / Caddy | 1.37.0 / 2.10.2-alpine | GPL-2.0 / Apache-2.0 |
| Ubuntu Squid（两个 SSRF 代理） | `sha256:6a097f68bae708cedbabd6188d68c7e2e7a38cedd05a176e1cc0ba29e3bbe029` | GPL-2.0；Ubuntu 官方镜像，摘要锁定 |
| dify-on-wechat | `hanfangyuan/dify-on-wechat@sha256:2ef2ecf9f081301fd34f9ceb4397f1d6cd6dac2eb42789285970a6f31752ad76` | [MIT](https://github.com/hanfangyuan4396/dify-on-wechat/blob/4685a7d89aa6881c14459037bc1d1564f84a9224/LICENSE)；官方发布镜像按摘要锁定，不声称与该源码提交完全一致 |
| 部署脚本 PyYAML | 6.0.2 | MIT；通过 uv 独立运行，无服务端 Python 依赖变更 |
| enterprise_base（X1） | 0.1.0；安装于原固定组合，Frappe 镜像后缀 `-eb0.1.0` | 本仓库 Frappe App，GPL-3.0-only；不修改上游源码 |
| policy-sync（X5） | `ghcr.io/jayq0720/enterprise-base-policy-sync@sha256:a58c658531701ad4d38deb7b5c6d46e3e36bcf059a0af73159ee7b47b8ef5dae`；[main 22a6538 发布记录](https://github.com/jayq0720/enterprise-base/actions/runs/41010380499) | 本仓库独立服务；运行依赖许可证由 CI 检查 |
| kb-gateway（X3） | `ghcr.io/jayq0720/enterprise-base-kb-gateway@sha256:cb8885d44b7bbf4c88a5fb539cd64b521fd07b74495650b165f6eeae8dc2cb78`；[main 22a6538 发布记录](https://github.com/jayq0720/enterprise-base/actions/runs/41010380499) | 本仓库独立服务；运行依赖许可证由 CI 检查 |
| frappe-mcp（X4） | `ghcr.io/jayq0720/enterprise-base-frappe-mcp@sha256:ac76771a51048e854864efcef2dbad6d80edf8e03eb6d07300796b797020d7d1`；[main 22a6538 发布记录](https://github.com/jayq0720/enterprise-base/actions/runs/41010380499) | 本仓库独立服务；含岗位开关和员工前台接口 |
| portal（X6 员工前台） | `ghcr.io/jayq0720/enterprise-base-portal@sha256:dca5e6fd3d9fc5634a467c93e9718a138375348bbdaac6fda2a474db09ad0207`；[main 22a6538 发布记录](https://github.com/jayq0720/enterprise-base/actions/runs/41010380499) | 本仓库独立服务；依赖 starlette、httpx、pydantic、uvicorn（BSD-3/MIT） |
| X3/X4/X5 构建 Python / uv 镜像 | 3.12.12-slim-bookworm / 0.11.32 | PSF / MIT 或 Apache-2.0；仅官方 Python、Astral 镜像 |
| age（备份加密工具） | 1.2.1；官方发布包及程序 SHA256 见 `age.lock.json` | BSD-3-Clause；[官方发布](https://github.com/FiloSottile/age/releases/tag/v1.2.1)，缓存保留原 LICENSE；支持 Linux amd64、macOS arm64 |

实际启动的 Dify 服务全部保留上游命令、依赖条件、健康检查及隔离网络；仅裁掉未选数据库/向量库与 certbot，覆盖镜像版本、命名卷、环境变量和入口网络。只暴露 Caddy，插件调试、数据库、Qdrant 端口不发布。Dify Marketplace 仍需联网手动安装国产插件；关闭 Next.js 遥测、更新检查、Sentry/Amplitude，不将这些设置描述为完整的出口封锁。
