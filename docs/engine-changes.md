# 引擎改动（相对上游 v0.7.0）

`engine/remote_fleet_mcp.py` 是上游 `scripts/remote_fleet_mcp.py` 的冻结副本。下表是全部偏离点；每一条都有回归测试（`tests/test_orca_engine.py` / `tests/test_contract.py`），并已确认该测试在上游上会失败。

所有改动都不触碰 [compatibility.md](compatibility.md) 的协议项；只有 stop 的返回体**新增**了 `sigkilled_pgids` 字段。

| # | 级别 | 问题（上游） | 改动 | 测试 |
|---|---|---|---|---|
| E1 | 高·安全 | patch guard 只认行首严格匹配的 `*** Add File:` 等头部；Codex `apply_patch` 会 trim 头部行，`   *** Add File: ../x` 绕过 guard 写到 workdir 之外（本机 codex 实测复现） | 按所有可能的换行符切分、strip 后再匹配，路径同样 strip | `PatchGuardTests` |
| E2 | 高·共用机 | stop 只发 SIGTERM + `kill-session`(SIGHUP)；忽略这两个信号的进程存活，却被记为 `stopped` / exit 143，GPU 继续被占 | 记录 pane 进程组，宽限期后对仍存活的组 SIGKILL，返回 `sigkilled_pgids` | `test_orca_stop_sigkills_processes_ignoring_term_and_hup` |
| E3 | 高 | session 规则允许 `.`，tmux 会静默改成 `_`（3.6a 实测）；之后 `set-option` 失败，回滚 `kill-session -t =a.b` 又杀不到 `a_b`，留下无登记的孤儿任务 | 新 launch 禁止 `.`；status/stop 仍接受 | `LaunchSessionTests` |
| E4 | 中 | `ssh -T` 无 pty，本地取消/断线不会给远端发信号；`remote_run` 的命令一直跑到自身超时（最长 3600s） | 看门狗每秒检查 `getppid()`，被重新挂到别的父进程即视为断线，杀整个进程组 | `RunWatchdogTests` |
| E5 | 中 | status 按 session 过滤时 `glob("exp-*")` 也命中 `exp-2`，且在精确过滤前先截 `job_limit`，目标任务会被挤掉 | 按 job_id 文件名形状精确过滤后再截断 | `StatusFilterTests` |
| E6 | 中·性能 | `ControlPersist=60s`，空闲超过 1 分钟就要重新握手 | 默认 600s（仍可用 `REMOTE_FLEET_SSH_PERSIST` 覆盖） | `MuxTests` |

| E7 | 低 | stop 的 SIGKILL 清扫（E2）若紧跟在会话消失之后，会误杀正在收尾的进程 | 清扫前给进程组最多 `min(grace, 5)` 秒自行退出 | `test_stop_finalizes_structured_log` |

## 扩展（新增能力，不改协议）

为方便日后 cherry-pick，上游文件里只加了钩子，新逻辑放在独立文件：

| 位置 | 内容 |
|---|---|
| `remote_fleet_mcp.py` 顶部 | `import orca_ext`（把 engine 目录加入 `sys.path`） |
| `TOOL_HANDLERS` 之后 | `TOOL_HANDLERS.update(orca_ext.install(globals()))`：只扩展 `cli-json` 可调用的处理器，MCP 的 `TOOLS` 保持上游原样 |
| launch 程序 | 可选 `spec.orca_log`：建 `<log>.orca/`、写 meta、后台启动跟随器、结束时用 `.stop` 文件收尾；回滚时删除新建的目录 |
| launch 处理器 | 接受 `orca_log` 参数（`orca_ext.logger_spec` 校验并附上跟随器源码） |
| status 程序 | `log_tail` 优先读 `<log>.orca/tail.ring`，新增 `log_tail_source` 字段 |
| `orca_ext.py` | `orca_grep` / `orca_ls` / `orca_patch` / `orca_rollback` 处理器；回复丢失时查询 patch 状态 |
| read 程序 / 处理器 | `orca_auto_sha256`：≤4 MB 的文件自动附带 sha256（供先读后改校验） |
| `orca_fs.py` / `orca_patch_apply.py` / `orca_logger.py` | 远端程序，以 `python3 -c` 源码下发，可单独测试 |

### orca_patch：事务型 Codex 补丁应用器

`engine/orca_patch_apply.py`，以 `python3 -c` 下发。文本语义对齐**当前** Codex（codex-rs `parser.rs` / `seek_sequence.rs` / `file_update.rs` / `text_file.rs`，commit 430a167）；事务层是 orca 自己的。

**语义基准**：`tests/fixtures/codex_scenarios/` 原样收录 Codex 官方 25 个场景（Apache-2.0，见其 NOTICE），`tests/test_codex_scenarios.py` 逐字节比对。`tests/test_orca_patch_apply.py` 另与本机 Codex 二进制对照（0.141 这类旧版本还不保留行尾，对应用例自动跳过）。

| 维度 | 行为 |
|---|---|
| 匹配 | Codex 四级：exact → rstrip → strip → Unicode 标点归一；EOF 块不早于游标；末尾空行重试 |
| 行尾 / BOM | 每行保留原行尾；上下文行原样不动；新增行用文件首个行尾；BOM 保留且不妨碍第 1 行匹配（Codex 不处理 BOM） |
| 匹配报告 | 非精确匹配与歧义（同级别后面还有匹配）都写进 `files[].matches`；`strict` 时直接拒绝 |
| 冲突信息 | 补丁行号、全部期望行、最接近的实际片段与相似度、"疑似已应用" 提示 |
| 原子性 | 先在内存算完全部文件；任一写失败或收到 SIGTERM/SIGHUP/SIGINT，恢复所有已写文件 |
| 崩溃恢复 | manifest 状态机 `prepared → committed`；SIGKILL 后留下 `prepared`，回滚是幂等的（已是原状的条目跳过），无需 force；同 `patch_id` 重试会先自动撤销残局再应用；其他补丁碰到残局文件会被拦下 |
| 幂等 | `patch_id` 即幂等键：同 id 同补丁再次应用返回 `already_applied`；不同补丁复用同 id 拒绝 |
| 结果契约 | exit 1 只表示"什么都没动"；一旦开始写入，一律 exit 0 带 `state`（`restored` / `partial` …），`orca_ext` 不再丢弃这些结果 |
| 回复丢失 | SSH 超时或回复损坏时，`orca_ext` 自动用 `status` 模式查询该 `patch_id` 的真实状态再回答模型 |
| 并发 | 与 remote-fleet 共用 workdir flock；每次写入前重新核对文件仍是规划时的字节，不守锁的编辑器（vim、脚本、其他用户）改过的文件不会被覆盖；新建文件与移动目标用独占创建 |
| 先读后改 | 插件记录 `orca_read`（≤4 MB 自动算哈希）、dry run 与上次 apply 看到的哈希，apply 时自动填入 `expected_sha256`；文件在此之后被改就拒绝，提示重新读取 |
| 文件身份 | 普通文件原子替换（保留权限位、尽力保留属组）；硬链接、属主不是自己、带 xattr/ACL、目录不可写时改为原地写，保留 inode、属主、ACL、链接 |
| 权限 | 只读文件、不可写目录在 dry run 阶段就报冲突（与 Codex 一致：Codex 也拒绝只读文件） |
| 备份 | `~/.local/state/orca/backups/`，目录 0700、文件 0600；已提交备份保留 14 天且最多 100 份，已退役的保留 7 天，总量超过 1 GB 时从最旧的删起，`prepared` 永不清理；force 回滚前先把将被覆盖的内容存到 `forced-*` |
| 磁盘空间 | 写入前检查备份与新内容所需空间，剩余不足 256 MB 时拒绝（什么都不动） |
| 读文件 | `O_NOFOLLOW`（写前换成符号链接会被拒）+ `O_NONBLOCK`（FIFO 不会卡死） |

**性能**（主机 13，Linux，Python 3.10；不含 SSH）：

| 场景 | 优化前 | 现在 |
|---|---|---|
| 1 万行 / 30 个 hunk，dry run | 68 ms | 4 ms |
| 10 万行（4.5 MB）/ 30 个 hunk，dry run | 872 ms | 61 ms |
| 30 万行（14 MB）/ 10 个 hunk，apply | 1.6 s | 365 ms |
| 10 万行匹配失败 + 找最接近片段 | 2.0 s | 322 ms |
| 10 万行相同内容、50 行补丁匹配失败（最坏情况） | 4.1 s | 386 ms |
| 每次调用的进程启动 | 43 ms（下发 67 KB 源码） | 32 ms（远端缓存，下发约 300 字节） |

做法：`LineIndex` 按级别惰性建立归一化行与"行 → 位置"索引，只在首行能匹配的位置做切片比较（语义不变，3000 组随机用例与逐位置的参照实现逐一核对）；diff 直接由替换区段生成，不再对整文件跑 difflib（随机用例验证 diff 能还原新文件）；源文件按行尾 `split` 解析；远端程序按内容哈希缓存在 `~/.local/state/orca/bin/` 并复用 `.pyc`，缓存缺失或被改动时自动重装。端到端一次小 patch 仍约 130 ms，主要是 SSH 往返。

**与 Codex 的差异**（`DIVERGENCES`，均为拒绝得更多，且拒绝时工作目录不动）：Add File 目标已存在、Move to 目标已存在（Codex 覆盖）；任一文件失败则整体不生效（Codex 保留已成功的部分）；绝对路径与逃逸路径。另外 Move 保留源文件权限位（Codex 给 0644）。同一补丁两次触及同一路径：当前 Codex 工具路径同样拒绝。

**横向对比结论**（2026-10-22 调研 Codex、Claude Code、OpenCode、Aider、Cline、Roo、Gemini CLI、SWE-agent、OpenHands、Zed、Cursor、Continue、Goose、pi、DSH）：多文件全有或全无、跨进程锁、崩溃日志、幂等重试这几项在上述实现里都没有完整对应；模糊匹配不再放宽（Levenshtein、大小写不敏感、LLM 自动纠错都不采用），Codex 四级是上限——这也是 Aider 与 OpenCode 新内核的方向。

## 已知但尚未处理

| 问题 | 计划 |
|---|---|
| 登记目录从不清理，status 代价 O(N) | M4 前加归档/清理 |
| 会话消失但无 sidecar 的任务永远 `unknown`（重启、tmux server 被杀） | status 侧加 `orphaned` 推断 |
| 非零退出 / patch 失败不标记为工具错误 | DSH 层（`index.js`）按 `exit_code`/`applied` 处理 |
| status 不报告「谁占着 GPU」（无 `--query-compute-apps`） | M4 |
| 大文件按行翻页每次 O(文件) 重扫 | 优先用 `offset_bytes` 游标；M4 的 tail.ring |
| 远端 `.bashrc` 有输出会污染 JSON；非 POSIX 登录 shell（fish）会破坏引号 | 文档提示；必要时改为 `exec python3` 前缀 |
| Python 任务在 `| tee` 后块缓冲，日志尾滞后 | 由用户命令自行设置 `PYTHONUNBUFFERED`，不在包装层强加 |
