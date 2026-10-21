# 与上游的协议兼容

本仓库可以自由改内部实现，但下列四项必须与上游**逐字节兼容**，否则两个前端会互相看不见对方的任务。

## 1. 状态目录

```
~/.local/state/remote-fleet/
  jobs/     任务登记：<job_id>.json，job_id = <session>-<YYYYmmddTHHMMSSZ>-<uuid4 hex 前 8 位>
  locks/    workdir 级 flock（文件名 = sha256(realpath(workdir)).lock）
```

目录名、层级、锁文件命名规则均不可变。登记文件名前缀有语义：status / stop 都靠 `glob("<session>-*.json")` 找任务。

## 2. 状态旁挂文件

路径：`<log_path>.remote-fleet-status.json`

```json
{"exit_code": 0, "finished_at": "2026-10-21T14:00:00Z", "job_id": "…", "session": "exp001"}
```

由 stop 写入时多一个字段 `"stopped_by_remote_fleet": true`（status 据此判定 `stopped`）。

规则：

- 这五个字段**只增不改、不删不改名**；读方必须按 JSON 解析，不能按字节比较（launch 写紧凑格式，stop 写带空格格式）
- 写入必须是原子替换（`os.replace` / `mv -f`）
- 文件已存在时默认拒绝启动（上游行为），除非显式 `append_log`

## 3. tmux 任务标记

**是 tmux 用户选项，不是环境变量：**

```
tmux set-option -t <session> @remote_fleet_job_id <job_id>     # 写
tmux list-sessions -F '#{@remote_fleet_job_id}'                 # 读
tmux show-options -v -t <session> @remote_fleet_job_id          # 读
```

上游的 `remote_status`（本插件为 `orca_status`）靠它把 tmux 会话识别为 fleet 任务。没有该标记、或标记与登记不符的会话，`remote_stop_job` 一律拒绝——这是上游的安全边界，不得放宽。

注意：`set-option` / `show-options` 必须用裸 `<session>`（tmux 3.2a 拒绝 `=` 前缀），其余命令用 `=<session>`。

## 3½. 日志首尾标记

tee 日志首行 `[remote-fleet] started_at=… job_id=… session=… workdir=…`，末行 `[remote-fleet] finished_at=… exit_code=…`。包装脚本靠 `PIPESTATUS[0]`（命令）/ `PIPESTATUS[1]`（tee）取退出码；往管道里加阶段（如 tail.ring 写入器）时下标必须保持正确。

## 4. session 命名

`[A-Za-z0-9][A-Za-z0-9_.-]{0,79}`，且 `tmux has-session -t =<session>` 精确匹配（前缀匹配会误杀同前缀会话）。

orca 的 **launch** 收窄为 `[A-Za-z0-9][A-Za-z0-9_-]{0,79}`：tmux 会把 `.` 静默改成 `_`（3.6a 实测 `a.b` → `a_b`），上游据此会产生 stop 够不着的孤儿任务。status / stop 仍接受旧规则，以便看见上游登记的任务。

工具命名对照见 [naming.md](naming.md)；本文件出现的 `remote_*` 一律指**上游工具名**，不得改动。

## 允许的扩展

- 新增文件：`<log>.orca/` 目录（`events.ndjson`、`tail.ring`、`meta.json`、`*.ndjson.gz`、`logger.py`）
- 新增远端目录：`~/.local/state/orca/`（`backups/` patch 备份，`bin/` 远端程序缓存）
- `orca_patch` 复用上游的 workdir 锁（`locks/<sha256(realpath)>.lock`），两边的 patch 互斥
- status 返回体新增 `log_tail_source`；stop 返回体新增 `sigkilled_pgids`
- `remote_read_file` 返回体新增字段（如 `encoding`、`binary`）
- 新的工具与配置项

不允许：改上述四项、改已有字段语义、把只读工具变成有副作用。

## 契约测试（M1 验收）

`tests/test_contract.py`：在本机真实 tmux（隔离 socket）上直接运行两边的远端程序——远端协议只由这些程序读写，所以本地跑等价于在远端跑。上游路径取 `$ORCA_UPSTREAM_ENGINE`，缺省 `../remote-fleet/scripts/remote_fleet_mcp.py`。

双向验证：

1. **DSH → 上游**：本插件 `launch` 一个任务，上游 CLI `status` 必须能看见并读到 exit code。
2. **上游 → DSH**：上游 CLI `launch` 一个任务，本插件 `orca_status` 必须能看见。
3. **跨前端停止**：本插件能停上游登记的任务，反之亦然（job identity 校验通过后）。

三条全过，才允许合并。任何一次改动涉及上述四项时，必须重跑。

另有真实主机验证（2026-10-21，host 13：Python 3.10、tmux 3.2a）：orca 启动的任务被上游 CLI 看到并读到 exit code；对忽略 TERM/HUP 的任务 `orca_stop` 清扫后无残留进程。
