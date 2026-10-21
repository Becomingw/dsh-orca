# 命名

## 原则

**品牌覆盖到「我们自己的东西」，协议名一个都不动。**

能改名的：包、插件行 id、工具、配置键、自有产物。
不能改名的：远端状态目录、上游旁挂文件、tmux 环境标记、session 规则——改了 Codex/Claude 就看不见 DSH 起的任务。

## 总表

| 层 | 名字 | 说明 |
|---|---|---|
| 仓库 / npm 包 | `dsh-orca` | |
| 插件行 id | `orca` | `cordis.patch.yml` 里的 `- id: orca` |
| 工具前缀 | `orca_` | 9 个工具全部统一 |
| 本地自有产物 | `<log>.orca/` | 一个目录管住 meta/events/tail |
| 远端协议目录 | `~/.local/state/remote-fleet/` | **冻结**，与上游共用 |

## 工具对照

| 本插件 | 上游 | 备注 |
|---|---|---|
| `orca_status` | `remote_status` | |
| `orca_read` | `remote_read_file` | |
| `orca_run` | `remote_run` | |
| `orca_patch` | `remote_apply_patch` | 后端改写为 git → 影子目录 → codex |
| `orca_launch` | `remote_launch_tmux` | |
| `orca_stop` | `remote_stop_job` | |
| `orca_grep` | — | 新增，服务端过滤 |
| `orca_ls` | — | 新增 |
| `orca_rollback` | — | 新增，配合 patch 的备份点 |

## 冻结区（不可改名）

| 项 | 值 | 原因 |
|---|---|---|
| 状态目录 | `~/.local/state/remote-fleet/` | 上游 jobs/locks 在这里 |
| 状态旁挂文件 | `<log>.remote-fleet-status.json` | 上游 `status` 读它 |
| 字段 | `exit_code` / `finished_at` / `job_id` / `session` | 只增不改 |
| tmux 标记 | `remote_fleet_job_id` | 识别 fleet 任务的唯一依据 |
| session 规则 | `[A-Za-z0-9][A-Za-z0-9_.-]{0,79}` + `-t =name` | 前缀匹配会误杀 |

## 配置键

```yaml
- id: orca
  name: dsh-orca
  config:
    hosts: ["13", "16", "17", "21"]
    enginePath: /Users/mengfei/plugins/dsh-orca/engine
    sessionPrefix: ""        # 默认空，保证与上游起的任务同名可见
    policy: { ... }          # 见 policy.md
    logging: { ... }         # 见 logging.md
```

`sessionPrefix` 默认留空是有意的：一旦给 DSH 的任务自动加前缀，用 Codex 那边按原 session 名去找就会找不到。想区分来源请靠 `job_id` 里的 `owner` 字段，不要靠 session 名。

## 可执行别名（CLI 调试用）

```
orca status --host 13
orca read --host 13 --path /data/project/app.py
```

`orca` 只是 `engine/` 的薄包装，便于手工排障；模型侧永远走工具，不走 CLI。
