# 路线图

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **M0** | 仓库 + 设计文档 | 本文档集齐，决策可追溯。✅ |
| **M1** | 冻结引擎 + 契约测试 | 三条双向测试通过（见 compatibility.md）。✅ 本机真实 tmux |
| **M2** | `orca_status` / `orca_read` / `orca_run` | DSH 里能查状态、读文件、跑命令；输出有界。✅ 代码与测试；DSH 内安装待验证 |
| **M2½** | `orca_launch` / `orca_stop` | M3 矩阵与 M4 日志需要；随 M3 交付。✅ |
| **M3** | policy + 审批分级 | 只读零打断；写操作按矩阵拦截；授权可撤销。✅ |
| **M4** | 结构化日志 | `tail.ring` 生效；`status` 只读 KB 级；轮转与压缩正常。✅ |
| **M5** | `orca_grep` / `orca_ls` / patch 改造 | patch 有 dry-run + 回滚；grep 服务端过滤。✅ |
| **M6** | 前端：对话卡片 + 审批文案 + 右侧栏机群面板 | 0.8 已实现；覆盖 10 种工具卡片、鉴权机群接口与侧栏、明确授权提示；验收见 [panel.md](panel.md)；不做 Jobs 投影 |

## 依赖关系

```
M0 → M1 → M2 → M3 → M4
                └→ M5
                     └→ M6
```

M4 与 M5 可并行；M6 依赖 M3（策略）与 M4（缓存数据源）。

## DSH API 约束（2026-10-21 对照 0.2.1-rc.2 核实）

- DSH Jobs 归属单个 agent 会话、只活在 DSH 进程内、重启即消失，且会话关闭/退出时会调 `cancel()`。**不能把 job 的 cancel 接到 `orca_stop`**，否则关会话就杀远端训练。远端任务若投影进 Jobs，cancel 只能解除跟踪。
- 审批结果只有 `allowed-once | rejected | cancelled | unavailable`，没有内建「记住」。askOnce 与 7 天过期需插件在 storage 上自建（M3）。
- `tools/pre-execute` 不能改写参数；硬拒绝用 `ctx.tools.guard()`（单调，其他插件无法翻转）。
- 工具参数 schema 只支持 `type/enum/properties/required/additionalProperties/items/oneOf/const` 与注解，范围与 pattern 写进描述、由引擎校验。

## 已知风险

| 风险 | 缓解 |
|---|---|
| 双引擎漂移 | 契约测试 + 只改上层不改协议 |
| 上游协议变更 | 同步时先跑契约测试，失败即回滚冻结点 |
| 共用机器误伤 | policy 矩阵 + stop 永不记忆 + stop 校验 job identity |
| 日志写入拖慢训练 | 有界队列 + 丢弃策略 + 不阻塞 stdout |
| patch 半成品 | 先在内存算出全部结果再写；写入失败按备份恢复；跨前端共用 workdir 锁 |
