# CI 说明

`.github/workflows/ci.yml` 在每个 PR 和推 `main` 时运行，四项都绿才能合并。

| 检查 | 做什么 | 失败时怎么办 |
| --- | --- | --- |
| python | 对 `services/*/` 下每个有 `pyproject.toml` 的服务和 `tests/e2e/`（没配测试环境时用例全部跳过，只做静态检查），跑 `ruff check`、`ruff format --check`、`mypy`、`pytest` | 在该服务目录里本地跑同样的命令修好 |
| python / 许可证 | 运行时依赖只允许 MIT、BSD、Apache、ISC、MPL-2.0、PSF、LGPL；出现 GPL、AGPL、SSPL 或未知许可证时失败 | 换依赖，或在 PR 里说明并请 JQ 决定 |
| secrets | gitleaks 扫全部提交历史 | 删掉密钥、作废并更换该密钥；已推上去的密钥一律视为泄露 |
| compose | 临时目录中生成入口、Dify、Frappe 完整配置，运行 Compose 校验及部署约束检查 | 本地执行 `make -C deploy verify`；修复生成器、版本锁或配置 |

`frappe_apps/` 是 Frappe App，本身按 GPL 处理，不在许可证检查范围内；它的测试在 P1 有代码后加到 CI。

本地跑一个服务的全部检查：

```bash
cd services/kb_gateway
uv sync && uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest
```

`main` 的分支保护（必须 PR、必须 CI 通过）由 JQ 在 GitHub 仓库 Settings → Branches 里开。

部署校验不需要 Docker Engine，只需 Compose 命令、Git、uv 及 Python 3.12；首次运行联网获取锁定的官方源和按 SHA256 验证的 Caddy 2.10.2。检查默认本机入口和自定义公网 HTTPS 配置，验证镜像固定版本、命名卷、端口/网络边界、密钥初始化幂等/权限/缺项拒绝；用 Caddy 实际解析路由，并在随机回环端口短暂验证后台来源限制、403 与伪造头拒绝。测试使用固定响应后端，不启动业务容器，不访问业务服务，不读取或覆盖服务器已有密钥和运行数据；详细范围见 `deploy/VALIDATION.md`。检查通过不代表延期的运行验收通过。
