# CI 说明

`.github/workflows/ci.yml` 在每个 PR 和推 `main` 时运行，四项都绿才能合并。

| 检查 | 做什么 | 失败时怎么办 |
| --- | --- | --- |
| python | 对 `services/*/` 下每个有 `pyproject.toml` 的服务，跑 `ruff check`、`ruff format --check`、`mypy`、`pytest` | 在该服务目录里本地跑同样的命令修好 |
| python / 许可证 | 运行时依赖只允许 MIT、BSD、Apache、ISC、MPL-2.0、PSF、LGPL；出现 GPL、AGPL、SSPL 或未知许可证时失败 | 换依赖，或在 PR 里说明并请 JQ 决定 |
| secrets | gitleaks 扫全部提交历史 | 删掉密钥、作废并更换该密钥；已推上去的密钥一律视为泄露 |
| compose | `deploy/` 下的 compose 文件用 `.env.example` 做 `docker compose config` 校验 | 补齐 `.env.example` 里的变量或修语法 |

`frappe_apps/` 是 Frappe App，本身按 GPL 处理，不在许可证检查范围内；它的测试在 P1 有代码后加到 CI。

本地跑一个服务的全部检查：

```bash
cd services/kb_gateway
uv sync && uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest
```

`main` 的分支保护（必须 PR、必须 CI 通过）由 JQ 在 GitHub 仓库 Settings → Branches 里开。
