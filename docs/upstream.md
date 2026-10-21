# 上游同步

上游：`https://git.scosine.org/becomingw/remote-fleet.git`
冻结点：`75203016050d272e2521a84d1ef330e1350f6f34`（v0.7.0）

## 原则

**只 cherry-pick，不 merge。** 上游的 MCP/CLI/skill 结构与本仓库目标不同，整包合并会把 DSH 不需要的东西带进来。

## 一次性配置

```bash
cd ~/plugins/dsh-orca
git remote add upstream https://git.scosine.org/becomingw/remote-fleet.git
git fetch upstream
```

## 同步流程

1. `git fetch upstream`
2. `git log --oneline UPSTREAM_COMMIT..upstream/main`，逐个判断是否与 DSH 相关
3. 相关改动**手工移植**到 `engine/`（不是 cherry-pick 整条提交）
4. 更新 `UPSTREAM.md` 与 `engine/UPSTREAM_COMMIT`
5. **跑契约测试**（compatibility.md 的三条）
6. 通过则提交；失败则回退 `engine/` 到上一个冻结点

## 必须同步的情况

- 协议四项（状态目录 / 旁挂文件字段 / tmux 标记 / session 命名）发生任何变化
- SSL/SSH 复用或 tmux 登记逻辑的安全修复
- patch guard 的逃逸检查修复

## 可以不跟的情况

- 只影响 MCP 传输层、Codex/Claude/opencode 安装方式的改动
- 只改 README/skill 文案的改动
