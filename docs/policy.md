# 免确认策略（policy）

目标：**日常零打断，红线仍然拦得住。**

共用训练机上「每个操作都问」不可行——人会被问烦，然后一路点「允许」，等于没有审批。所以把「问不问」从运行时决策变成**安装时声明的策略**。

实现：[lib/policy.js](../lib/policy.js)（纯函数判定）、[lib/grants.js](../lib/grants.js)（记忆）、[index.js](../index.js)（接线）。

## 审计矩阵（默认）

| 工具 | action | 风险 | 默认档 | 记忆粒度 |
|---|---|---|---|---|
| `orca_status` / `orca_read` / `orca_grep` / `orca_ls` | status / read / grep / ls | 只读 | autoAllow | — |
| `orca_grants` | grants | 只能列出/撤销授权 | autoAllow | — |
| `orca_run` | run | 有副作用 | askOnce | host + workdir |
| `orca_launch` | launch | 长任务 | askOnce | host + workdir |
| `orca_patch` | patch | 改远端代码（`dry_run:true` 视为只读） | alwaysAsk | — |
| `orca_rollback` | rollback | 覆盖文件 | alwaysAsk | — |
| `orca_stop` | stop | 杀进程 | alwaysAsk，**不可配置为记忆或免询问** | — |

## 配置

仓库自带的 `cordis.patch.yml` 用 `- insert:` 声明插件行；用户在 profile 的 `cordis.patch.yml` 里用 `- id: orca` 行**覆盖**（覆盖整体替换 config，不是合并，没写的键回到代码默认值）：

```yaml
- id: orca
  name: dsh-orca
  config:
    hosts: ["13", "16", "17", "21"]      # 空 = ~/.ssh/config 里全部具体 Host 别名
    policy:
      autoAllow:   [status, read, grep, ls, grants]
      askOnce:     [run, launch]
      alwaysAsk:   [patch, rollback, stop]
      grantTtlDays: 7
      allowedCommands:                    # 仅 run；每一段都命中才免询问
        - "nvidia-smi *"
        - "git status"
        - "tail *"
      deniedCommands: ["rm -rf /*", "sudo *", "shutdown*", "reboot*", "mkfs*", "kill -9 -1*"]
      trustedWorkdirs: ["/data/local_14T/mengfei/*"]   # 空 = 不限制
    logging: { structured: true, tailLines: 2000, rotateBytes: 33554432, keepSegments: 5 }
```

## 判定流程

```
tool call (tools/pre-execute)
  → run/launch 命中 deniedCommands？           → deny（任何模式都拒绝）
  → patch 且 dry_run？                          → 放行
  → 档位 = alwaysAsk > askOnce > autoAllow（未列出：只读→auto，写→alwaysAsk；stop 恒为 alwaysAsk）
  → 只读且 auto？                               → 放行
  → 会话审批策略 never（完全访问）？            → 放行
  → workdir 不在 trustedWorkdirs？              → 改为 alwaysAsk（授权与 allowedCommands 都不生效）
  → auto？                                      → 放行
  → run 且命中 allowedCommands？                → 放行
  → askOnce 且已有有效授权？                    → 放行
  → askOnce：插件自己调用 approval.request；批准则记住并放行，拒绝则 deny
  → alwaysAsk：返回 ask，由 DSH 管线询问
```

另有一道 `ctx.tools.guard()`：对 run/launch 再查一次 `deniedCommands`。guard 是单调的，其他插件的监听器无法把拒绝翻转为放行。

### 与 DSH 审批策略的关系

DSH 的 `never` **不是「完全访问」**，而是严格无头模式：它在分发之前确定性地拒绝每个审批请求，不发出提示。插件必须照做，否则就成了绕过会话策略的后门：

| 会话审批策略 | 只读 / 白名单命令 / 已有授权 | 其余写操作 |
|---|---|---|
| `ask` | 放行 | 按上表询问 |
| `never` | 放行 | **拒绝**，并在原因里提示改用 `ask` 或写进 `allowedCommands` |
| 任意 | — | 命中 `deniedCommands` 一律拒绝 |

要在 `never` 下干活只有两条路：把命令加进 `policy.allowedCommands`，或者先在 `ask` 下批准一次——授权会记住 `grantTtlDays` 天，切回 `never` 后依然有效。

> 2026-10-22 修掉的 P0：早期实现把 `never` 当「完全访问」直接 `pass`，等于把严格无头模式变成无限制模式（实测 `echo`、`mkdir` 未经审批直接执行）。现在只在「本就要询问」的分支上拒绝，只读、白名单、已有授权不受影响。

### `deniedCommands` 是兜底，不是沙箱

它拦的是明确列出的破坏性命令。默认 `rm -rf /*` 用的是锚定 glob，因此会连带拦住 `rm -rf /home/you/scratch` 这类正常清理——这是有意偏保守。需要删目录时用 `find <dir> -depth -delete`，或把该操作交给审批，不要为了图方便放宽这条规则。

### allowedCommands 的切段规则

按 `;` `&&` `||` `|` `&` 切段，**每一段**都命中才免询问（`git status; rm -rf x` 不会搭 `git status` 的便车）。含 `$(`、反引号、重定向 `<` `>` 或换行的命令从不免询问。空白被折叠后再匹配。

### trustedWorkdirs

glob（`*` 可跨 `/`），workdir 先做词法规范化（折叠 `..`、去尾 `/`），所以 `/data/local_14T/mengfei/../x` 不算受信。目录本身（`/data/local_14T/mengfei`）匹配 `/data/local_14T/mengfei/*`。远端符号链接无法在本地解析，不在防护范围内。

## 授权记忆与撤销

DSH 的审批结果只有 `allowed-once | rejected | cancelled | unavailable`，没有内建「记住」。所以 askOnce 由插件自己调用 `approval.request`，拿到 `allowed-once` 后写入授权：

- 存储：`$DSH_HOME/storages/orca/grants.json`（缺省 `~/.dsh/...`，可用 `config.grantsPath` 改），原子写，权限 0600。每次判定都重新读文件，所以别的会话撤销立即生效。
- 记忆键：`[host, 规范化 workdir, action]`；默认 7 天过期（`grantTtlDays`，1–90）。
- 询问文案会写明「批准后 N 天内 host:workdir 下的 action 不再询问」。
- 撤销：`orca_grants {action: "revoke", host?, workdir?, tool?}`；不带过滤条件撤销全部。`orca_grants` 不能创建授权。M6 面板会提供可视化撤销。
- 拒绝不会被记住；`orca_stop` 永不记忆。

## 尚未实现

- `orca_run` 的收敛环境（`env -i` + 显式 PATH）。远端是 `bash -lc`，登录 shell 会重建大部分环境，收益有限，暂缓。
