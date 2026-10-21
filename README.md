# dsh-remote-fleet

DeepSeek Harness 原生插件：把 [remote-fleet](../remote-fleet) 的远端执行能力做成 DSH 自己的工具与面板。

> **状态：M0（只有设计文档，尚未实现）。** 路线见 [docs/roadmap.md](docs/roadmap.md)。

## 定位

对话和上下文留在本地，文件读写、命令执行、补丁、tmux 长任务落到受信任的 SSH 主机上。
不做 remote development（不搬工作区），只做**本地决策 + 远端执行**。

## 与上游的关系

| | 上游 `~/plugins/remote-fleet` | 本仓库 |
|---|---|---|
| 使用者 | Codex / Claude Code / opencode | 仅 DSH |
| 引擎 | `scripts/remote_fleet_mcp.py` | `engine/`（v0.7.0 冻结副本） |
| 接口 | MCP over stdio | DSH 原生 tool |
| 改动权限 | **本仓库不修改上游任何文件** | 自由演进 |

两条原则：

1. **代码分叉，协议不分裂。** 见 [docs/compatibility.md](docs/compatibility.md)。
2. **只 cherry-pick，不 merge。** 见 [docs/upstream.md](docs/upstream.md)。

## 非目标

- 不替代 `research-hub`（决定跑什么、跑在哪台）
- 不替代 `rmount-ctx`（挂载生命周期）
- 不接管其他 agent 的安装或配置
- 不引入远端第三方依赖（远端只允许 Python 标准库 + OpenSSH + tmux）

## 目录

```
package.json          bundle 清单（dsh.bundle.patch）
cordis.patch.yml      插件行声明
index.js              defineTool、policy、Jobs 投影
client.js             Fleet 面板（M6）
engine/               冻结的 Python 引擎
docs/                 设计文档
UPSTREAM.md           冻结点与同步记录
```

## 文档

| 文件 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 组件、数据流、关键决策 |
| [docs/compatibility.md](docs/compatibility.md) | 必须与上游保持一致的协议 |
| [docs/policy.md](docs/policy.md) | 免确认策略与审批矩阵 |
| [docs/logging.md](docs/logging.md) | 结构化日志与 tail 设计 |
| [docs/roadmap.md](docs/roadmap.md) | 里程碑与验收标准 |
| [docs/upstream.md](docs/upstream.md) | 上游同步流程 |
