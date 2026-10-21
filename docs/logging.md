# 日志：控制面与数据面分离

训练的原始输出改不了（框架自己打印），能改的是**包装层**。目标不是「让日志变好看」，是**让读日志变便宜**。

## 布局

```
runs/exp001/
  meta.json                # 命令、host、user、git sha、起止、exit code（一次性）
  status.json              # 原子替换的当前状态（与上游同名旁挂文件兼容）
  events.ndjson            # 结构化事件：{ts, level, stream, msg}
  events-0001.ndjson.gz    # 轮转后压缩
  tail.ring                # 最近 N 行归一化文本，定长重写 ← 专供 tail
  raw/exp001.tee.log       # 原始保真，保留不动
```

## 为什么 tail.ring 是重点，而不是压缩比

gzip 无法从尾部随机读。想「看最后 50 行」就得解压整个文件——日志一大，这个操作比直接读原始文件还慢。

所以额外维护一个定长尾环文件：

- 只保留最近 2000 行归一化文本
- 达到上限后整体重写（原子替换）
- `remote_status(log_tail_lines=50)` 永远只读它：**几十 KB，不是几百 MB**

压缩只在轮转/结束时做，不放在写热路径上。

## 归一化规则（写入侧）

1. 剥离 ANSI 转义序列
2. `\r` 进度条折叠：同一行只保留最后一个状态
3. 连续重复行折叠为 `msg ×N`
4. 超长单行截断到 4 KB，保留前缀 + 长度标记

这三步通常砍掉 80% 体积，且不丢失语义。

## 压缩格式

只能用 Python 标准库：`gzip` 或 `lzma`。**不引入 zstd**（远端零依赖是硬约束）。

轮转策略：单段 32 MB，保留 5 段（可配）。超过则删最旧。

## 读取路径

| 场景 | 读什么 | 量级 |
|---|---|---|
| 任务在跑吗 | `status.json` | ~1 KB |
| 最后几行 | `tail.ring` | ~50 KB |
| 找报错 | `remote_grep` 搜 `events.ndjson` | 命中行数 |
| 复现问题 | `raw/*.tee.log`（显式请求才给） | 大，需分页 |

## 兼容性红线

`status.json`（即上游的 `<log>.remote-fleet-status.json`）字段**只增不改**，见 [compatibility.md](compatibility.md)。新增的 `events.ndjson` / `tail.ring` / `meta.json` 上游不认识会忽略，不冲突。

## 写入不能拖慢训练

事件收集在独立线程，有界队列；队列满时丢弃 `info` 级、保留 `error` 级，并在 `meta.json` 记录丢弃计数。绝不阻塞被监控进程的 stdout。
