# 任务卡 P0-C1 – P0-C5：Claude 的 P0 工作

- P0-C1 仓库规范改为 Claude + GPT：`AGENTS.md`、`CLAUDE.md`、新增 `GPT.md`、删除 `GEMINI.md` 和 `GROK.md`，任务卡改为 `P0-G<n>` 交给 GPT。
- P0-C2 验证清单：`docs/P0-验证清单.md`，P0 验收逐项打勾。
- P0-C3 接口定稿：X3 由 Chatflow HTTP 节点带 `sys.user_id` 调 `/v1/search`；X4 用服务端签发的短期 HMAC 会话令牌。依据 ADR 0001，验证清单第 3、4 节。
- P0-C4 X3 骨架：PR #1，按审阅意见改完合入。
- P0-C5 CI 骨架：见 `P0-C5-CI骨架.md`。
