# dsh-orca — 远端机群

DeepSeek Harness 原生插件：把 [remote-fleet](../remote-fleet) 的远端执行能力做成 DSH 自己的工具与面板。

> **一群虎鲸，一个大脑。** 本机决策，机群执行。

> **状态：M6 / 0.8.0。** 10 个工具已有原生对话卡片；会话顶栏「机群」打开右侧栏，可查看主机与任务、读取日志尾、确认停止和撤销授权，打开只读宿主缓存。设置 → Orca 配置展示配置与授权范围。审批分级、结构化日志、原子 patch 与回滚已在真实主机（13）验证。使用说明见 [docs/panel.md](docs/panel.md)，路线见 [docs/roadmap.md](docs/roadmap.md)。

## 定位

对话和上下文留在本地，文件读写、命令执行、补丁、tmux 长任务落到受信任的 SSH 主机上。
不做 remote development（不搬工作区），只做**本地决策 + 远端执行**。

## 与上游的关系

| | 上游 `~/plugins/remote-fleet` | 本仓库 |
|---|---|---|
| 使用者 | Codex / Claude Code / opencode | 仅 DSH |
| 引擎 | `scripts/remote_fleet_mcp.py` | `engine/`（v0.7.0 冻结副本） |
| 接口 | MCP over stdio | DSH 原生 tool |
| 改动权限 | **本仓库不修改上游任何文件** | 自由演进 |

两条原则：

1. **代码分叉，协议不分裂。** 见 [docs/compatibility.md](docs/compatibility.md)。
2. **只 cherry-pick，不 merge。** 见 [docs/upstream.md](docs/upstream.md)。

## 命名

工具统一 `orca_` 前缀，远端协议名保持不变：

| 工具 | 作用 | 默认审批 |
|---|---|---|
| `orca_status` | 负载、内存、磁盘、GPU、tmux、登记任务（尾部读 `tail.ring`） | 免询问 |
| `orca_read` / `orca_grep` / `orca_ls` | 读文件分页 / 服务端搜索（含 .gz）/ 列目录 | 免询问 |
| `orca_run` | 有界命令 | 同 host+workdir 批准一次记 7 天 |
| `orca_launch` | tmux 长任务 + 结构化日志 | 同上 |
| `orca_patch` | 事务型 patch：全有或全无、崩溃可恢复、幂等重试、文件被改过即拒绝；`dry_run` 只返回 diff | 每次询问（dry_run 免） |
| `orca_rollback` | 按 `patch_id` 回滚 | 每次询问 |
| `orca_stop` | 停任务，宽限后 SIGKILL 残留 | 每次询问，永不记忆 |
| `orca_grants` | 列出/撤销记住的审批 | 免询问 |
完整对照见 [docs/naming.md](docs/naming.md)。

## 非目标

- 不替代 `research-hub`（决定跑什么、跑在哪台）
- 不替代 `rmount-ctx`（挂载生命周期）
- 不接管其他 agent 的安装或配置
- 不引入远端第三方依赖（远端只允许 Python 标准库 + OpenSSH + tmux）

## 安装

在 DSH 会话里让 agent 执行（走 `plugin_manager` 的 `install_bundle`，不要手改 profile）：

```
安装本地插件 ~/plugins/dsh-orca
```

新 bundle 经 HMR 生效；之后改了代码需重启 DSH。配置覆盖写在 profile 的 `cordis.patch.yml`：

```yaml
- id: orca
  name: dsh-orca
  config:          # 覆盖会整体替换 config，需要保留的键都要写上
    hosts: ["13", "16", "17", "21"]
    policy: { allowedCommands: [...], deniedCommands: [...] }
```

## 测试

```bash
npm test     # node --test test/*.test.mjs && python3 -m pytest -q tests/
```

- `test/`：Node 侧（policy、schema 与引擎一致性、经假 ssh 的端到端）
- `tests/test_upstream_engine.py`：上游原有测试，针对冻结引擎
- `tests/test_orca_engine.py`：E1–E6 回归（每条在上游上都会失败）
- `tests/test_contract.py`：本机真实 tmux 上 orca ⇄ 上游双向契约，含结构化日志
- `tests/test_orca_ext.py`：日志跟随器、grep/ls、扩展处理器
- `tests/test_orca_patch_apply.py`：patch 应用器，含与本机 Codex 二进制的逐字节对照
- `tests/test_codex_scenarios.py`：Codex 官方 25 个 apply-patch 场景（`tests/fixtures/codex_scenarios/`）
- `tests/test_orca_patch_robustness.py`：SIGKILL/SIGTERM 崩溃恢复、并发写入、硬链接/属组、冲突反馈、备份保留

可选：`ORCA_DSH_TOOLS=<dsh-tools/lib/index.js>` 时，Node 测试会用 DSH 自己的 schema 校验器检查全部工具。

## 目录

```
package.json          bundle 清单（dsh.bundle.patch、exports、locale、icon）
cordis.patch.yml      `- insert:` 插件行 orca 与默认 config
index.js              插件入口：注册工具、pre-execute 审批、deniedCommands guard
lib/engine.js         引擎桥：每次调用一个 cli-json 子进程，进程组级取消
lib/tools.js          orca_* 工具 schema 与渲染
lib/policy.js         审批判定（纯函数）
lib/grants.js         记住的审批（askOnce）
lib/observations.js   上次读到的远端文件哈希（先读后改校验）
engine/remote_fleet_mcp.py   冻结的上游引擎 + 小钩子（UPSTREAM_COMMIT）
engine/orca_ext.py           orca 专有工具处理器
engine/orca_fs.py            远端 grep / ls
engine/orca_patch_apply.py   远端 patch / rollback（Codex 语义）
engine/orca_logger.py        远端结构化日志跟随器
locale/ icon.svg      插件管理器展示资源
docs/                 设计文档
UPSTREAM.md           冻结点与同步记录
```

## 文档

| 文件 | 内容 |
|---|---|
| [docs/naming.md](docs/naming.md) | 命名总表、工具对照、冻结区 |
| [docs/architecture.md](docs/architecture.md) | 组件、数据流、关键决策 |
| [docs/compatibility.md](docs/compatibility.md) | 必须与上游保持一致的协议 |
| [docs/engine-changes.md](docs/engine-changes.md) | 冻结引擎相对上游的改动与已知问题 |
| [docs/ui-design.md](docs/ui-design.md) | 前端设计（M6）：对话卡片、审批文案、右侧栏机群面板、接口与验收 |
| [docs/policy.md](docs/policy.md) | 免确认策略与审批矩阵 |
| [docs/logging.md](docs/logging.md) | 结构化日志与 tail 设计 |
| [docs/panel.md](docs/panel.md) | 面板：缓存模型、接口、成本、已知限制 |
| [docs/roadmap.md](docs/roadmap.md) | 里程碑与验收标准 |
| [docs/upstream.md](docs/upstream.md) | 上游同步流程 |

## 许可证

MIT，见 [LICENSE](LICENSE)。第三方来源见 [NOTICE](NOTICE)。

- `engine/remote_fleet_mcp.py` 派生自 remote-fleet v0.7.0（同一作者），偏离点逐条记录在 [docs/engine-changes.md](docs/engine-changes.md)
- `tests/fixtures/codex_scenarios/` 复刻自 OpenAI Codex，Apache-2.0，见该目录的 `NOTICE`

## 从 GitHub 安装

```bash
git clone https://github.com/Becomingw/dsh-orca.git ~/plugins/dsh-orca
```

然后在 DSH 会话里让 agent 安装本地插件 `~/plugins/dsh-orca`（走 `plugin_manager` 的 `install_bundle`）。
升级即 `git pull` 后重启 DSH。
