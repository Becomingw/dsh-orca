# 上游冻结点

| 项 | 值 |
|---|---|
| 仓库 | https://git.scosine.org/becomingw/remote-fleet.git |
| 版本 | v0.7.0 |
| commit | 75203016050d272e2521a84d1ef330e1350f6f34 |
| 冻结日期 | 2026-10-21 |
| 冻结范围 | `scripts/remote_fleet_mcp.py` → `engine/remote_fleet_mcp.py`；`scripts/test_remote_fleet_mcp.py` → `tests/test_upstream_engine.py` |
| 未纳入 | MCP 传输之外的打包：`.mcp.json`、opencode/claude/codex 安装描述、skill；上游测试中两条 opencode 打包用例已删除 |
| 本地改动 | 见 [docs/engine-changes.md](docs/engine-changes.md)（E1–E6） |

同步流程见 [docs/upstream.md](docs/upstream.md)。
