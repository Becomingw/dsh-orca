# 前端设计（M6）

> 状态：**已实现于 0.8**（2026-10-22）。实际界面、接口和验证记录见 [panel.md](panel.md)，后端路线见 [roadmap.md](roadmap.md)。
> 下文保留原始设计调研与验收计划；与实现修订冲突的旧线框、交互和字段示例不作为现行规范。机群入口已移至会话顶栏，设置页用于展示配置。

## 实现前的设计修订

- 宿主原生主题、13px 正文；主机与操作为第一层，命令与目录可换行。异常展开，普通成功保持安静。
- 运行任务优先于主机清单；统计基于最近登记时必须显示覆盖范围与 `+`，不把零任务称为“空闲”。不额外截断跨主机列表。
- 缓存保留最后成功数据，区分未探测、过期、连接失败；旧的运行状态显示未知并禁用停止。
- 停止采用独立确认/取消区，无 3 秒时限；核对任务身份、拒绝并发重复停止、展示错误与完成反馈。
- 审批首行说明记忆授权范围、实际期限及“允许一次”的实际效果。回滚按钮改为“复制回滚指令”。
- meta 使用 `version: 1`；grep 传每行路径、行号、命中标记和文本范围。Python 正则只在服务端解释，前端高亮命中行；固定字符串可精确高亮。
- 历史记录缺少 meta 或展示数据损坏时保留原始输出。
- UI 与工具共用 `createTools` 的执行函数；无需再维护独立 actions 层。
- 实际界面、接口与限制以 [panel.md](panel.md) 为准。以下保留原方案的宿主 API 调研与设计依据。

## 目标

1. **对话里一眼看懂远端操作**：每个 `orca_*` 调用有自己的卡片，远端主机醒目，结果（退出码、diff、冲突、GPU）结构化呈现，不再是一行「Tool call · orca_run」。
2. **审批时信息够做决定**：批 patch 之前知道改哪些文件、多少行；打不上的补丁不再让人白批。
3. **机群状态常驻可见**：和对话并排的右侧栏，而不是藏在设置里；运行中任务可查看、可停止；记住的授权可撤销。
4. **安全与一致**：所有界面数据和操作走 DSH 鉴权接口；主题跟随明暗；中英双语。

## 设计原则

- **宿主塑形，客户端渲染（host shapes, client renders）**。所有「把工具结果变成要显示的东西」的逻辑放在宿主侧 Node 代码里（可单测），通过 `presentationMeta` 交给客户端；`client.js` 只做无状态渲染和交互，不解析业务数据。
- **远端一眼可辨**。每张 orca 卡片最前面是主机徽章（`13`、`16`…），与本地 bash/edit 卡片区分开。
- **安静优先**。默认折叠输出；只在异常（非零退出、冲突、半成品、磁盘告急）时用颜色。不做装饰。
- **零额外 SSH**。打开面板、渲染卡片都不触发远端调用；界面只有用户明确点击（刷新、展开日志尾、停止）才会新增远端调用，既有后台探测独立运行。
- **一个操作只实现一次**。界面按钮和对应工具共用宿主侧同一段代码（见 DSH `user-actions.md`）。

## 核实过的 DSH 事实（0.2.1-rc.2）

源码在 `/Applications/DeepSeek Harness.app/Contents/Resources/app.asar`，可用 `npx --yes @electron/asar extract <asar> <dir>` 解出，包在 `node_modules/@deepseek-ai/` 下（只有打包后的 `lib/client.js`，无类型声明）。官方插件开发指南在 `dsh-agent-preset/skills/cordis-plugin-development/`（`SKILL.md`、`references/{ui-plugin,practices,user-actions,verification}.md`）——**实现前必读**。

| 能力 | 结论 | 依据 |
|---|---|---|
| 按工具名定制对话卡片 | **可以**：keyed 插槽 `tool.call.toolview`，`key` = 工具名，注册后完全替换通用卡片 | `dsh-client-ui-tool/lib/client.js` 中 `renderSlot("tool.call.toolview", owner, {entryKey: toolName, fallback: GenericToolCard})` |
| 卡片拿到的数据 | `props.block`：运行中 `{phase, callId, name, args(惰性读取器), argsRaw}`；结束后 `{kind:"tool-result", args, content(渲染文本), isError, error?, meta}`。**规范值 `value` 不会到客户端**，只有渲染文本 `content` 和 `meta` | `dsh-client-ui-chat` `rootResult` |
| 结构化数据通道 | 宿主工具定义 `output.presentationMeta(args, value) → JSON`，存入会话日志 `tool/result.data.meta`，模型不可见；**只在成功时计算** | `dsh-tools/lib/index.js` `register` 校验、`createSuccessResult` 计算 |
| 状态推导 | 无状态字段，按下式推导（与内置卡片一致） | 见下方代码 |
| 复用内置终端/diff 卡片 | **不行**：`BashRow`/`FileMutationRow` 未导出，其模型只认 `bash`/`edit`/`write`；引用 `dsh-client-ui-primitives` 被开发指南禁止。只能参照其外观自己写 | `dsh-client-ui-tool` 导出仅 `apply`/`inject` |
| 运行中流式输出 | **不行**：工具没有进度接口，运行中卡片只看得到参数 | `dsh-tools` 管线无 progress |
| 审批框定制 | 只有 `displayReason {en, zh}` 文本 + 单例插槽 `conversation.approval.detail`（替换会影响所有工具）；按钮固定「允许一次 / 拒绝」，结果集固定 `allowed-once/rejected/cancelled/unavailable` | `dsh-client-ui-approval`、`dsh-user-approval` |
| 右侧栏标签页 | **可以**：`sidebarRightTabs.register({id, kind, title, guide, keepMounted})` + 插槽 `sidebar.right.pane.tab`（key = id），可选 `sidebar.right.pane.tab.title`；组件内 `props.useTabInfo().tab.visible` | `dsh-client-ui-sidebar-right/README.md`、`dsh-context` 插件 |
| 会话顶栏入口 | 列表插槽 `conversation.session.header.actions`（`{id, order}`），Jobs 按钮也在这里 | `dsh-client-ui-jobs`、`dsh-cost-meter` |
| 宿主 ↔ 客户端（全局数据） | `connection.fetch.register({path:'/api/...', methods, requestBody:'buffered', fetch})`：浏览器 cookie 鉴权 + Host/Origin 校验，非属主返回 403。无服务端推送，客户端轮询 | `dsh-context` 的 helper |
| 原始 `webServer.register` | **无鉴权、无来源校验**（`dsh-host-webserver` README：路由自行负责），不应承载数据与操作 | 同左 |
| 多语言 | `ctx.locale.register(ns, {zh, en})`，注册时带 `locale: ns`，组件拿到 `props.t` | 各内置包 |
| 主题 | 只用 `--dsw-alias-*`、`--dsw-radius-*`、`--dsw-font-*` 变量；实时清单用 `cordis_inspect_query` 查 `Theme` | `practices.md` |
| 禁止 | iframe、宿主返回 HTML、`require` DSH 客户端包、往 `document.body` 挂节点、读写其他插件 DOM | `practices.md` |
| 组件崩溃 | 抛错会清空该插槽条目，控制台出现 `slot entry crashed in '<slot>'` | `dsh-client-ui-renderer` |

状态推导：

```js
const state = !('kind' in block)
  ? (block.phase === 'preparing' ? 'preparing' : 'running')
  : block.error?.code === 'interrupted' ? 'stopped'
  : block.isError ? 'error' : 'ok'
```

**未在运行时验证**（实现时请确认并回填本表）：被策略拒绝（deny）的调用在客户端如何呈现、`deny.info` 是否随结果到达客户端；`conversation.session.header.actions` 条目的确切 props；右侧栏标签在无会话页面的行为。

## 总体结构

```
宿主（index.js + lib/）                          客户端（client.js）
├─ lib/tools.js        工具定义 + presentationMeta ──meta──▶ tool.call.toolview × 10（卡片）
├─ lib/view-models.js  [新] 结果 → meta 的纯函数（单测）
├─ lib/tools.js        execute：日志尾/停止/授权由界面与工具共用
├─ lib/cache.js        机群快照（已有，后台轮询）
├─ lib/routes.js       [新] /api/dsh-orca/*（connection.fetch） ◀──fetch── 右侧栏「机群」+ 顶栏标记 + 设置页
└─ index.js            审批预检（patch dry run）、注册
```

客户端只有一个 `client.js`（ModuleLoader 格式，无构建步骤）。若超过约 1500 行，可引入 esbuild 打包 `client/src/*` → `client.js`，产物提交入库；在此之前保持单文件、按区块组织。

## 第 1 部分：对话卡片

### 通用外壳

```
┌──────────────────────────────────────────────────────────────┐
│ [▣ 13] 标题（命令 / 文件 / 主机摘要）           [状态徽章] [操作] │  ← 头部，单行，可点击展开
├──────────────────────────────────────────────────────────────┤
│ 详情区（默认折叠；异常时默认展开）                                │
└──────────────────────────────────────────────────────────────┘
```

- 主机徽章：`ti-server` 图标 + 别名，accent 色调。
- 状态：`preparing/running` 显示转圈与已流入的参数（如命令）；`ok`/`error`/`stopped` 按下表各卡定义。
- `isError`（工具抛错、被拒）时没有 meta：显示红色 `content` 文本（去掉 `Error: ` 前缀），头部仍用 `args` 里的 host/命令。
- 详情区最大高度约 320px，超出可滚动；等宽字体用 `--dsw-font-mono`。
- 所有文案走 `t()`。

### 渲染文本与 meta 的分工

`content` 是给模型看的渲染文本（`lib/tools.js` 的 `RENDERERS`），卡片需要其中的输出/diff 片段时，**不要在客户端猜格式**，而由 meta 给出字符区间：`meta.body = [start, end]`，客户端取 `content[0].text.slice(start, end)`。这样既不在日志里重复存 diff，也不让客户端依赖渲染格式。生成区间的逻辑与渲染器放在一起（同一处构造字符串时记录偏移）。

### 各工具的 meta 与卡片

meta 必须紧凑（会持久化到每条调用），字符串字段统一截断（命令 500 字、冲突消息 1500 字）。下表 meta 字段均为建议，实现时以 `lib/view-models.js` 为准并写测试。

| 工具 | meta | 卡片 |
|---|---|---|
| `orca_run` | `{host, workdir, command, exit_code, timed_out, truncated, body:[s,e]}` | 头部：`[13] /data/proj $ cmd`，徽章 `exit 0`（成功低调灰）/ `exit 1`（红）/ `超时`（黄）。详情：输出（body），stderr 段落已在渲染文本中以 `[stderr]` 分隔，用较弱色显示。非零退出默认展开 |
| `orca_patch` | `{host, workdir, mode:'dry_run'\|'apply', state, patch_id?, files:[{path, action, move_to?, added, removed, fuzzy?:[{hunk,line,level,also?}]}], conflicts:[{path, message}], warnings:[], body:[s,e]}`（body 指向 diff） | 头部：`[13] 修改 N 个文件 +A −R`，状态徽章：`预览`、`已应用`（绿）、`冲突`（红）、`已恢复`（黄）、`半成品`（红，粗）、`已应用过`（灰）。已应用时右侧「复制回滚指令」按钮（见下）。详情：文件列表（新增/删除/移动图标，模糊匹配用黄色小字注明「hunk 1 按去空白匹配」、歧义注明「也匹配第 N 行」），冲突块（路径 + 消息原文，含「最接近片段」），彩色 diff（`+` 绿底、`-` 红底、`@@` 弱色） |
| `orca_rollback` | `{host, patch_id, state, restored:[], conflicts:[], saved:[]}` | 一行：`[13] 回滚 orca-… · 恢复 N 个文件`；冲突/强制保存时展开列出 |
| `orca_status` | 复用 `lib/cache.js` 的 `summarizeHost` 并补每卡利用率：`{host, hostname, load, gpu:{count, model, util:[…], memUsedMib, memTotalMib}, disk, memory, jobs:[{session, state, exit_code, active}]}` | 头部一行：`[16] 8×A100 · 平均 63%` + 每卡一根 5px 小柱（高度 = 利用率）+ `2 个任务在跑`。磁盘可用 < 5% 或 < 20 GB 时头部加红色「磁盘 98%」。详情：负载、内存、磁盘、任务表（会话 / 状态 / 退出码）、任务 `log_tail`（若有） |
| `orca_read` | `{host, path, first_line, last_line, total_lines, eof, next_start_line, size_bytes, body:[s,e]}` | 头部：`[13] 📄 /path/train.py  1–200 / 5000 行`。详情：带行号的内容 |
| `orca_grep` | `{host, path, pattern, files_matched, files_scanned, count, truncated, timed_out, body:[s,e]}` | 头部：`[13] 🔍 "pattern" · 12 处 / 3 个文件`。详情：按文件分组，行号 + 命中行（命中子串高亮），上下文弱色 |
| `orca_ls` | `{host, path, count, truncated, body:[s,e]}` | 头部：`[13] 📁 /path · 42 项`。详情：等宽列表 |
| `orca_launch` | `{host, workdir, session, job_id, log_path, structured:boolean}` | 头部：`[16] ▶ 启动任务 exp7`，按钮「在机群面板查看」（打开右侧栏并定位该任务）。详情：命令、日志路径 |
| `orca_stop` | `{host, session, was_running, session_stopped, hard_killed, sigkilled:number}` | 一行：`[16] ■ 已停止 exp7`（若 SIGKILL 了残留进程组，加「强制结束 N 个进程组」） |
| `orca_grants` | `{action, count}` | 一行说明 |

### 卡片上的操作

- **回滚**（patch 卡片）：复制包含主机与 patch_id 的指令，供用户粘贴发送；执行仍经过原有审批流程。
- **在机群面板查看**（launch 卡片）：`ctx.get('sidebarRight').openTab('dsh-orca')` 并传入要定位的 `{host, session}`（经客户端内存状态传递）。

### 被策略拒绝的调用

宿主侧所有 deny 决定统一带上 `info: {name: 'OrcaPolicyDenied', code: 'ORCA_DENIED', reason}`（`index.js` 的 pre-execute 与 guard）。客户端若能拿到 `block.error.info.code === 'ORCA_DENIED'`，显示为灰色「已拒绝：原因」而非红色错误。（未验证能否到达客户端，见上。）

## 第 2 部分：审批

审批框只能改文字，所以把文字做到足够决策：

1. **`orca_patch` 预检**：在 pre-execute 中、返回 `ask` 之前，用同样参数加 `dry_run:true` 调一次 `orca_patch`（受 `exec.signal` 约束，超时 20 s）：
   - 预检结果为冲突 → 直接 `deny`，`reason` 带上冲突消息（模型据此重新读文件、重写补丁），**不打扰用户**；
   - 预检成功 → `displayReason` 写成「在 13:/data/proj 修改 2 个文件（train.py +4 −1，configs/exp7.yaml +2）」；
   - 预检本身失败（SSH 不通、超时）→ 照常询问，文案注明「未能预检」。
   - 代价：每次需要审批的 patch 多一次约 130 ms 的往返；dry run 不写任何东西。
2. **`orca_run` / `orca_launch`**：DSH 自带的 `ApprovalCommand` 已会显示 `args.command`；`displayReason` 首行明确记忆授权的主机、目录、操作类别及配置期限，并解释宿主“允许一次”按钮的效果。
3. **`orca_stop`**：写明主机、会话、任务已运行时长（来自缓存快照，没有则省略）。
4. 不做：接管整个审批框（`conversation.composer` 优先级抢占，依赖内部类，脆弱）；额外按钮（协议不支持）。

## 第 3 部分：机群面板

### 位置

| 位置 | 内容 |
|---|---|
| 右侧栏标签页「机群」（`id: 'dsh-orca'`） | 主力视图：主机、运行中任务、记住的授权 |
| 会话顶栏标记（`conversation.session.header.actions`） | 小徽章：`▣ 2`（运行中任务数）；有失败或主机异常时红点；点击打开右侧栏标签页 |
| 设置 → Orca 机群（已有） | 只留「当前配置」只读视图 + 指向 `cordis.patch.yml` 的说明；主机与任务移走 |

### 右侧栏布局

```
4/4 在线 · 缓存于 40 秒前                         [⟳]
── 主机 ───────────────────────────────────────────
● 16   GPU 63% ▂▅▁▇▁█▄▁   2 任务                     ▸
● 13   磁盘 98%（红）                                 ▸
● 17   空闲                                          ▸
── 运行中的任务 ─────────────────────────────────────
exp7-lr1e4   @16 · 2 小时                         [停止]
  epoch 12/40 · loss 0.412 ×3          （展开：日志尾）
── 最近结束 ─────────────────────────────────────────
exp6   @16  失败 exit 1   10-22 01:13
── 已记住的授权 ──────────────────────────────────────
run · 16:/data/proj   剩 6 天                     [撤销]
```

- 主机行点击展开：负载、内存、磁盘、GPU 每卡利用率与显存、同步时间、错误原文。
- 状态点：连接与运行使用中性色和文字；连接失败为红色；磁盘可用 < 5% 或 < 20 GiB 为黄色资源告警。
- 运行中任务的一行副标题来自 `tail.ring` 最后一行（快照里带最后 1 行即可，`log_tail_lines: 1`，成本可忽略）；「展开日志尾」才按需拉 50 行。
- 「停止」展开独立确认区，显示主机与任务名；确认与取消为不同按钮，无倒计时；提交后禁用重复点击。
- 授权「撤销」一次点击即可（收窄权限，无风险）。
- 空态：没有主机时说明主机来源（`hosts` 配置或 `~/.ssh/config` 自动发现，最多 12 台）。

### 客户端数据流

- 一个模块级 store（`subscribe/getSnapshot`），顶栏标记与标签页共用，**引用计数**：有订阅者才轮询。
- 轮询周期 15 s，读的是宿主缓存（零 SSH）；`document.visibilityState !== 'visible'` 或标签页不可见且无顶栏订阅时暂停。
- 刷新按钮 → `POST /api/dsh-orca/summary {refresh:true}`（宿主侧并发刷新共享同一轮，已实现）。

### 接口（替换 `/dsh-orca/summary`）

全部用 `connection.fetch.register` 注册在 `/api/dsh-orca/` 下，`POST` + JSON 体，`requestBody: 'buffered'`；删除现有原始 `webServer` 路由。

| 路径 | 请求 | 响应 | 远端动作 |
|---|---|---|---|
| `summary` | `{refresh?: boolean}` | 现有快照结构 + `config` | 仅 `refresh` 时一轮 `remote_status` |
| `job/tail` | `{host, session, lines≤200}` | `{log_tail, state, exit_code}` | 一次 `remote_status`（session 过滤） |
| `job/stop` | `{host, session, grace_seconds?}` | `orca_stop` 的结果 | `remote_stop_job`（与工具共用 execute） |
| `grants/list` | `{}` | `{grants}` | 无 |
| `grants/revoke` | `{host?, workdir?, action?}` | `{revoked}` | 无 |

- 参数校验与工具一致（主机必须在允许列表、session 正则等），失败返回 400 + `{error}`。
- `job/stop` 是用户在界面上的显式操作，本身即确认，不再走审批；但必须只能停 fleet 登记过的任务（引擎已保证）。
- 不提供「新增授权」「修改配置」接口：授权只能来自审批，配置只在 `cordis.patch.yml`。

## 视觉规范

- 类名统一 `orca-` 前缀；样式以一个 `<style data-plugin-css="dsh-orca">` 注入（组件内渲染或首次挂载时插入 `document.head`，二选一，保证只有一份）。
- 颜色只用主题变量，示例映射（以 `cordis_inspect_query Theme` 实际清单为准）：

| 用途 | 变量 |
|---|---|
| 正文 / 次要 / 说明 | `--dsw-alias-label-primary` / `-secondary` / `-tertiary` |
| 边框 | `--dsw-alias-border-l1`…`l4` |
| 卡片底色 / 代码区底色 | `--dsw-alias-bg-layer-1` / `-layer-2` |
| 悬停 | `--dsw-alias-interactive-bg-hover` |
| 成功 / 警告 / 错误 / 强调 | `--dsw-alias-state-success-primary` / `-warn-primary` / `-error-primary` / `-business-primary` |
| 圆角 / 字号 / 等宽 | `--dsw-radius-sm/md/lg`、`--dsw-font-xs-13`、`--dsw-font-xxs-12`、`--dsw-font-mono` |

- diff 的增删底色用对应状态色的 12–15% 透明混合（`color-mix(in srgb, var(--dsw-alias-state-success-primary) 14%, transparent)`），保证明暗两套都可读。
- 图标：DSH 是否内置图标字体需确认；否则用小号内联 SVG（服务器、文件、搜索、播放、停止、回滚 6 个），单色、继承 `currentColor`。
- 数字等宽（`font-variant-numeric: tabular-nums`），时间相对显示（「2 小时」「40 秒前」），悬停显示绝对时间。

## 分阶段计划与验收

### 阶段 A：卡片 + 审批（建议先做）

1. `lib/view-models.js`：每个工具的 `meta(args, value, renderedText)` 纯函数；`lib/tools.js` 为每个工具挂 `presentationMeta`，渲染器返回偏移区间。
2. `client.js`：10 个 `tool.call.toolview` 注册；通用外壳；各卡详情；`ctx.locale` 中英文案。
3. `index.js`：deny 统一带 `info`；`orca_patch` 预检；新的 `displayReason` 文案。

验收：
- `node --test` 覆盖每个 meta 函数（含截断、偏移区间与渲染文本一致、错误/冲突/半成品各状态）；DSH 自带 schema 校验器测试（`ORCA_DSH_TOOLS`）仍通过（`presentationMeta` 是函数，注册校验会检查）。
- 预检测试：冲突 → deny 且不调用 `approval.request`；成功 → `displayReason` 含文件与增删行；预检超时 → 照常询问。
- 在已安装的 DSH 中逐一触发 10 个工具（含 run 非零退出、patch 冲突、patch 半成品可用模拟），明暗主题 + 中英文各截图核对；控制台无 `slot entry crashed`。

### 阶段 B：接口迁移 + 右侧栏 + 顶栏标记

1. `lib/routes.js` 共用工具执行函数；删除原始 `webServer` 路由。
2. 右侧栏标签页、顶栏标记、共享 store；设置页瘦身。
3. `docs/panel.md` 按新结构改写。

验收：
- 未登录请求 `/api/dsh-orca/*` 被拒（401），异源请求被拒（403）；旧 `/dsh-orca/summary` 不再存在。
- 面板不可见且无顶栏等订阅者，或文档隐藏时客户端无轮询请求（DevTools 网络面板核对）；打开面板不产生 SSH（宿主日志或 `ps` 核对）。
- 停止按钮展开独立确认区；只能停 fleet 登记任务；撤销授权后再次 `orca_run` 会重新询问。
- 13 号机磁盘 98% 时主机行出现资源告警，顶栏出现异常提示。

### 阶段 C：可选

- 远端任务完成时在顶栏标记上提示（基于快照前后对比，客户端本地，无推送）。
- 「谁占着 GPU」：需后端先补 `nvidia-smi --query-compute-apps` 与进程 → 用户映射（单独工作）。

## 明确不做

| 不做 | 原因 |
|---|---|
| 把远端任务塞进 DSH 自带 Jobs 列表 | Jobs 归属单个会话、随进程消失，会话关闭会调用 `cancel()`（若接 `orca_stop` 会误杀训练）；结束时还会向模型推送完成通知 |
| 接管审批框 / 增加「7 天内允许」按钮 | 依赖内部类且协议不支持；记忆授权已由插件实现并写在提示文字里 |
| 引用 DSH 内置终端/diff 组件 | 未导出，且开发指南禁止 `require` 客户端包 |
| 运行中流式显示 `orca_run` 输出 | 工具无进度接口；长任务应走 `orca_launch` + 机群面板 |
| 界面上修改配置 / 新增授权 | 配置真值在 profile 补丁层；授权只能来自审批 |

## 对实现者的提示

- 先读 DSH 插件开发指南（路径见上），用 `cordis_inspect_query` 查 `Slots`（确认插槽名、注册选项、props）与 `Theme`（变量清单），**以运行时为准**；本文「核实过的事实」来自 0.2.1-rc.2 的打包代码。
- 改动后用 `plugin_manager install_bundle` 重装；**替换已安装的包需要重启 DSH**，新增导出（如 `./client`）同样需要重启。
- 后端契约不要改：`orca_*` 工具的参数与返回值、远端协议见 [compatibility.md](compatibility.md) 与 [engine-changes.md](engine-changes.md)；`npm test` 必须保持全绿。
- 现有 `client.js` 中的格式化函数（字节、相对时间）可以保留复用，但颜色、文案、接口都要按本文替换。
