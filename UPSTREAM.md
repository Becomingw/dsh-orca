# 上游冻结点

> 2026-10-22：上游 remote-fleet 已升级并改名为 **orca 0.8.0**（`https://git.scosine.org/becomingw/orca.git`，本机 `~/plugins/orca`；MCP 版，供 Codex / Claude / OpenCode），后端能力与本仓库一致；两者代码独立维护，互不依赖。远端协议名两边相同。下表记录的是本仓库冻结时的旧地址与版本。

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
