# 与上游的协议兼容

本仓库可以自由改内部实现，但下列四项必须与上游**逐字节兼容**，否则两个前端会互相看不见对方的任务。

## 1. 状态目录

```
~/.local/state/remote-fleet/
  jobs/     任务登记
  locks/    workdir 级 flock（文件名 = sha256(workdir)）
```

目录名、层级、锁文件命名规则均不可变。

## 2. 状态旁挂文件

路径：`<log_path>.remote-fleet-status.json`

```json
{"exit_code": 0, "finished_at": "2026-10-21T14:00:00Z", "job_id": "…", "session": "exp001"}
```

规则：

- 这四个字段**只增不改、不删不改名**
- 写入必须是原子替换（`os.replace` / `mv -f`）
- 文件已存在时默认拒绝启动（上游行为），除非显式 `append_log`

## 3. tmux 环境标记

```
remote_fleet_job_id=<job_id>
```

上游的 `remote_status`（本插件为 `orca_status`）靠它把 tmux 会话识别为 fleet 任务。缺少该变量的会话对 `remote_stop_job` 不可见——这是上游的安全边界，不得放宽。

## 4. session 命名

`[A-Za-z0-9][A-Za-z0-9_.-]{0,79}`，且 `tmux has-session -t =<session>` 精确匹配（前缀匹配会误杀同前缀会话）。

工具命名对照见 [naming.md](naming.md)；本文件出现的 `remote_*` 一律指**上游工具名**，不得改动。

## 允许的扩展

- 新增文件：`events.ndjson`、`tail.ring`、`meta.json`、`*.ndjson.gz`
- `remote_read_file` 返回体新增字段（如 `encoding`、`binary`）
- 新的工具与配置项

不允许：改上述四项、改已有字段语义、把只读工具变成有副作用。

## 契约测试（M1 验收）

同一台测试机上双向验证：

1. **DSH → 上游**：本插件 `launch` 一个任务，上游 CLI `status` 必须能看见并读到 exit code。
2. **上游 → DSH**：上游 CLI `launch` 一个任务，本插件 `orca_status` 必须能看见。
3. **跨前端停止**：本插件能停上游登记的任务，反之亦然（job identity 校验通过后）。

三条全过，才允许合并。任何一次改动涉及上述四项时，必须重跑。
