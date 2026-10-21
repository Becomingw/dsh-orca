# 日志：控制面与数据面分离

训练的原始输出改不了（框架自己打印），能改的是**包装层**。目标不是「让日志变好看」，是**让读日志变便宜**。

实现：[engine/orca_logger.py](../engine/orca_logger.py)（远端跟随进程），launch 程序与 status 程序中的 `orca:` 段。

## 布局

```
runs/exp001/logs/raw/
  exp001.tee.log                          # 原始输出（上游 tee，保真）
  exp001.tee.log.remote-fleet-status.json # 协议：与上游共用，字段只增不改
  exp001.tee.log.orca/                    # 本插件自有，上游不认识会忽略
    meta.json                             # 命令、host、user、git sha、起止、exit code、跟随器计数
    events.ndjson                         # {"ts","level","msg"[, "repeat"]}
    events-0001.ndjson.gz                 # 轮转后压缩，编号越大越新
    tail.ring                             # 最近 N 行归一化文本，原子重写 ← 专供 tail
    logger.py / logger.err                # 跟随器本体与它自己的错误输出
```

一个目录管住所有自有产物：**协议文件留在原位不改名，扩展数据全部收进 `<log>.orca/`**。

## 跟随文件，而不是插进管道

跟随器是 launch 包装脚本里的一个后台进程，**读日志文件**，不在 `cmd | tee` 管道里：

- 训练进程的 stdout 永远不会因为日志处理变慢而被阻塞（没有背压可言）；
- `PIPESTATUS[0]`（命令）/ `PIPESTATUS[1]`（tee）下标不变，退出码语义与上游逐字节一致；
- 跟随器落后超过 64 MB 时直接跳到末尾前 1 MB，跳过字节数记入 `meta.json` 的 `logger.skipped_bytes`。

生命周期：

| 事件 | 行为 |
|---|---|
| launch | 写 `logger.py` 与初始 `meta.json`，清掉旧 `.stop`，后台启动跟随器（从当前日志末尾开始，兼容 `append_log`） |
| 任务正常结束 | 包装脚本先写 status 旁挂（协议），再创建 `.stop`，最多等 30 s 后 SIGKILL 兜底 |
| `orca_stop` | 进程组收到 SIGTERM，跟随器排空并收尾；stop 在 SIGKILL 清扫前给进程组最多 5 s 退出 |
| 收尾 | 读 status 旁挂补 `exit_code` / `finished_at`，写计数，删 `.stop` |

用 `.stop` 文件而不是信号来结束：极短任务可能在 Python 装好信号处理器之前就结束，信号会直接杀死跟随器（测试中实际遇到过）。

## 为什么 tail.ring 是重点，而不是压缩比

gzip 无法从尾部随机读。想「看最后 50 行」就得解压整个文件。所以额外维护一个定长尾环：

- 保留最近 `tailLines`（默认 2000）行归一化文本，最多每秒原子重写一次
- `orca_status` 的 `log_tail` 优先读它（返回 `log_tail_source: "ring"`），没有时回落到原始日志（`"log"`）
- 上游 status 不认识它，仍读原始日志，互不影响

## 归一化规则

1. 剥离 ANSI 转义序列
2. `\r` 进度条折叠：一行只保留最后一个非空状态
3. 连续重复行：第一次立即写出；后续重复在环里原地改成 `msg ×N`，在事件流里于重复结束时补一条 `{"msg", "repeat": N-1}`（保证最新一行马上可见）
4. 超长单行截断到 4 KB，附原长度标记

级别：异常类名（`RuntimeError`、`CUDAException`…）、`Traceback`、`error`/`fatal`/`killed`/`out of memory`/`nan` → `error`；`warn`/`warning`/`*Warning` → `warn`；其余 `info`。

## 压缩与轮转

只用 Python 标准库 `gzip`，**不引入 zstd**（远端零依赖）。`events.ndjson` 超过 `rotateBytes`（默认 32 MB）即压成下一个编号的段，保留 `keepSegments`（默认 5）段。压缩发生在跟随器里，不在训练进程的写路径上。

## 读取路径

| 场景 | 读什么 | 量级 |
|---|---|---|
| 任务在跑吗 | `orca_status` → status 旁挂 + 登记 | ~1 KB |
| 最后几行 | `orca_status` → `tail.ring` | ~KB |
| 找报错 | `orca_grep` 搜 `events.ndjson` / `events-*.gz` / 原始日志（.gz 透明搜索） | 命中行数 |
| 复现问题 | `orca_read` 原始日志（`next_offset_bytes` 翻页） | 大，需分页 |

## 兼容性红线

`status.json`（上游的 `<log>.remote-fleet-status.json`）字段**只增不改**，见 [compatibility.md](compatibility.md)。关闭结构化日志：`logging.structured: false`，此时 launch 与上游完全相同。
