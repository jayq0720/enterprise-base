# 任务卡 P0-C5：CI 骨架

- 负责：Claude　复看：GPT
- 时间：10-15 – 10-21
- 分支：`claude/P0-C5-ci`

## 要做的事

`.github/workflows/ci.yml`，PR 和推 main 时运行：

1. 找出改动的 `services/*/`，对每个服务在其目录里跑 `uv sync --frozen`、`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy .`、`uv run pytest`。
2. 依赖许可证检查：只允许 MIT、BSD、Apache-2.0、ISC、MPL-2.0、PSF、LGPL；出现 GPL/AGPL/SSPL/未知许可证时失败（`frappe_apps/` 除外，它本身按 GPL）。工具可选 pip-licenses。
3. gitleaks 密钥扫描（全仓库）。
4. `deploy/` 有改动时跑 `docker compose config`。
5. 用 `actions/*` 官方 Action，版本锁到具体 tag 或 commit。

## 交付

PR 包含 workflow 和一段说明（写在 `docs/CI.md`）。附一次 CI 运行链接。

## 验收

- 故意提交一个假密钥的测试分支，CI 失败。
- X3 骨架（PR #1）在 CI 里全绿。
