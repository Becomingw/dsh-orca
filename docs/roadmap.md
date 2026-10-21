# 路线图

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **M0** | 仓库 + 设计文档 | 本文档集齐，决策可追溯。**当前** |
| **M1** | 冻结引擎 + 契约测试 | 三条双向测试通过（见 compatibility.md） |
| **M2** | `remote_status` / `remote_read` / `remote_run` | DSH 里能查状态、读文件、跑命令；输出有界 |
| **M3** | policy + 审批分级 | 只读零打断；写操作按矩阵拦截；授权可撤销 |
| **M4** | 结构化日志 | `tail.ring` 生效；`status` 只读 KB 级；轮转与压缩正常 |
| **M5** | `remote_grep` / `remote_ls` / patch 改造 | patch 有 dry-run + 回滚；grep 服务端过滤 |
| **M6** | Jobs 投影 + Fleet 面板 | 远端任务在 DSH Jobs 可见可停；面板常开零 SSH |

## 依赖关系

```
M0 → M1 → M2 → M3 → M4
                └→ M5
                     └→ M6
```

M4 与 M5 可并行；M6 依赖 M3（策略）与 M4（缓存数据源）。

## 已知风险

| 风险 | 缓解 |
|---|---|
| 双引擎漂移 | 契约测试 + 只改上层不改协议 |
| 上游协议变更 | 同步时先跑契约测试，失败即回滚冻结点 |
| 共用机器误伤 | policy 矩阵 + stop 永不记忆 + stop 校验 job identity |
| 日志写入拖慢训练 | 有界队列 + 丢弃策略 + 不阻塞 stdout |
| patch 半成品 | 后端优先 git apply（原子），非 git 走影子目录提交 |
