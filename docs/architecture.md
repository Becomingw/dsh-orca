# 架构

## 场景约束

1. **多人共用的 GPU 训练机群**（编号别名 12/13/16/17/19/21/24/25/30/33 等）。误伤别人的进程比自己的失败更严重。
2. **多个 agent 并存**（DSH / Codex / Claude / opencode）。同一台机器可能被两个前端同时操作。
3. **长任务离手可查**。训练按小时到天计，关掉对话、隔天回来仍要能答出「跑到哪、exit code、GPU 还占着吗」。
4. **上下文是稀缺资源**。日志动辄 GB 级，任何工具输出都必须有界。
5. **网络不可靠**。远端命令有副作用，因此不自动重试、不静默降级。

## 组件

```
DSH (Web/Host)
  └── index.js  ── defineTool × N
        └── engine/  (Python 标准库)
              └── OpenSSH (ControlMaster 复用)
                    └── 远端 python / tmux / nvidia-smi / apply_patch
```

DSH 只依赖本地 Node；远端只有 Python 标准库、OpenSSH、tmux。

## 为什么冻结引擎

三个候选：

| 方案 | 结论 |
|---|---|
| 调用上游 `cli` 子命令 | 上游一改就跟着变；且本仓库要改日志格式时无处落手。**否决** |
| TS 全量重写 | 2,600 行 SSH 复用 / tmux 登记 / patch guard 细节，重写风险高、周期长。**暂缓** |
| 冻结一份 `engine/` | 可自由加 `tail.ring`、policy、结构化日志，上游零感知。**采用** |

冻结不等于永不更新：见 [upstream.md](upstream.md)。

## 本地与远端布局

本地：

```
~/plugins/dsh-orca/
  engine/           冻结引擎（含 UPSTREAM_COMMIT）
  node_modules/     DSH 依赖
```

远端（每台机器）：

```
~/.local/state/remote-fleet/          ← 与上游共用，不可改名
  jobs/                               任务登记
  locks/                              workdir 级 flock
<log_path>.remote-fleet-status.json   ← 与上游共用，只加字段
<log_path> 同级的 events.ndjson / tail.ring / meta.json   ← 本插件新增，上游忽略
```

## 数据流

**status**：`index.js` → `engine cli status` → SSH → 远端读数 → 压缩投影 → 模型。
**run**：参数校验 → policy 判定 → `engine cli run` → 进程组超时 → 有界输出。
**patch**：read 取 sha256 → guard 校验 → 后端选择（git / 影子目录 / codex）→ 回滚点。
**launch**：登记 job → tmux + `remote_fleet_job_id` → 写 status 旁挂 → 返回 job id → 投影到 DSH Jobs。

## 关键决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | 协议兼容优先于内部优雅 | 两个前端必须看见同一份任务 |
| D2 | 真值在远端，DSH 只做投影 | 多 agent 并存，内存态无法共享 |
| D3 | 只读工具免确认，写操作分级 | 共用机器上「每次都问」会被绕过，不如分好类 |
| D4 | 日志控制面与数据面分离 | 模型看结构化事件，人查原始日志 |
| D5 | 远端零依赖 | 训练机常常不能装包，且升级不受控 |
