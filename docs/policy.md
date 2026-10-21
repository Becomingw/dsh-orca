# 免确认策略（policy）

目标：**日常零打断，红线仍然拦得住。**

共用训练机上「每个操作都问」不可行——人会被问烦，然后一路点「允许」，等于没有审批。所以把「问不问」从运行时决策变成**安装时声明的策略**。

## 审计矩阵

| 工具 | 风险 | 默认策略 | 记忆粒度 |
|---|---|---|---|
| `remote_status` | 只读 | auto | — |
| `remote_read` | 只读 | auto | — |
| `remote_grep` / `remote_ls` | 只读 | auto | — |
| `remote_run` | 有副作用 | askOnce | host + workdir |
| `remote_launch` | 有副作用（长任务） | askOnce | host + workdir |
| `remote_patch` | 改远端代码 | alwaysAsk | — |
| `remote_rollback` | 覆盖文件 | alwaysAsk | — |
| `remote_stop` | 杀进程 | alwaysAsk | — |

`remote_run` 命中 `allowedCommands` 时跳过询问。

## 配置

```yaml
- id: remote-fleet
  name: dsh-remote-fleet
  config:
    hosts: ["13", "16", "17", "21"]      # 缺省读 ~/.ssh/config 的交集
    enginePath: /Users/mengfei/plugins/dsh-remote-fleet/engine
    policy:
      autoAllow:   [status, read, grep, ls]
      askOnce:     [run, launch]
      alwaysAsk:   [patch, rollback, stop]
      allowedCommands:
        - "python train.py *"
        - "python3 -m pytest *"
        - "git status"
        - "nvidia-smi"
        - "tail *"
      deniedCommands: ["rm -rf *", "sudo *", "kill -9 *", "chmod 777 *"]
      trustedWorkdirs: ["/data/local_14T/mengfei/*"]
    logging:
      structured: true
      rotateBytes: 33554432
      keepSegments: 5
      tailLines: 2000
```

## 判定流程

```
tool call
  → 参数校验（host 白名单、路径、长度）
  → deniedCommands 命中？ → 拒绝（不看其他规则）
  → alwaysAsk / askOnce 未授权？ → 询问
  → allowedCommands / trustedWorkdirs 命中？ → 放行
  → 执行
```

判定放在 `tools/pre-execute`，工具本身不判断——策略升级不用改工具。

## 授权记忆与撤销

- 记忆键：`policy/host/workdir/action`，会话间持久（写入插件自己的 storage）。
- 面板里可见「已记住：host 13 · /data/project · run」，可单条撤销。
- 默认 7 天过期，避免「三个月前点过一次，现在还在放行」。

## 共用机器的额外规则

- 跨出 `trustedWorkdirs` 的写操作一律 alwaysAsk。
- `remote_stop` 永不记忆：只停本次显式确认过的 job。
- `remote_run` 默认用收敛环境（`env -i` + 显式 PATH），避免继承别人的变量造成误判。
