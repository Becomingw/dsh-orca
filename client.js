/** Native DSH UI: versioned tool cards, shared cached fleet view, and settings. */
window.__ModuleLoader__.load({
  id: 'dsh-orca',
  factory(require) {
    const React = require('react')
    const h = React.createElement
    const { useState, useEffect, useRef, useSyncExternalStore } = React
    const words = {
      fleet: ['机群', 'Fleet'], fleetTitle: ['Orca 机群', 'Orca Fleet'], fleetSubtitle: ['远程主机与任务', 'Remote hosts and jobs'], configSubtitle: ['当前生效的连接与授权范围', 'Active connections and approval scope'], activeJobs: ['运行任务', 'Running jobs'], coverageShort: ['每台最近 {n} 条登记', 'Latest {n} records per host'], config: ['Orca 配置', 'Orca settings'], guide: ['主机状态、已登记任务与授权', 'Hosts, registered jobs and approvals'],
      hosts: ['主机', 'Hosts'], runningJobs: ['已知运行任务', 'Known running jobs'], recentJobs: ['最近结束与未知状态', 'Recent and unknown jobs'], grants: ['已记住的授权', 'Remembered approvals'],
      refresh: ['刷新主机', 'Refresh hosts'], refreshing: ['刷新中…', 'Refreshing…'], loading: ['读取中…', 'Loading…'], retry: ['重试', 'Retry'],
      reachable: ['最近探测成功', 'Last probe succeeded'], unreachable: ['无法连接', 'Unreachable'], unpolled: ['尚未探测', 'Not checked'], stale: ['数据已过期', 'Stale data'],
      lastProbe: ['最近探测', 'Last probe'], cache: ['上次成功同步', 'Last successful sync'], noHosts: ['没有监控主机。请在 cordis.patch.yml 中配置 hosts；留空时从 SSH 配置自动发现，最多 12 台。', 'No monitored hosts. Set hosts in cordis.patch.yml, or leave empty to discover up to 12 SSH aliases.'],
      noJobs: ['当前记录中没有运行任务', 'No running jobs in the available records'], noRecent: ['暂无其他任务记录', 'No other job records'],
      coverage: ['仅显示每台最近 {n} 条登记；任务数是已知数量，不代表整台机器空闲。', 'Showing the latest {n} registrations per host. Counts are known jobs, not proof that a host is idle.'],
      incomplete: ['统计不完整', 'Partial coverage'], allRegistered: ['已登记任务', 'Registered jobs'], noGrants: ['没有有效的记忆授权', 'No active remembered approvals'],
      attention: ['有需要关注的状态', 'Some statuses need attention'],
      load: ['负载', 'Load'], memory: ['内存可用', 'Available memory'], disk: ['磁盘可用', 'Free disk'], diskLow: ['磁盘空间不足', 'Low disk space'],
      gpu: ['GPU', 'GPU'], vram: ['显存', 'VRAM'], noGpu: ['未报告 GPU 数据', 'No GPU data reported'], unknown: ['未知', 'Unknown'],
      running: ['运行中', 'Running'], preparing: ['准备中', 'Preparing'], succeeded: ['已完成', 'Succeeded'], failed: ['失败', 'Failed'], stopped: ['已停止', 'Stopped'],
      stop: ['停止…', 'Stop…'], confirmStop: ['确认停止', 'Confirm stop'], cancel: ['取消', 'Cancel'], stopping: ['正在停止…', 'Stopping…'],
      stopExplain: ['停止 {host} 上的 {session}？将先发送 SIGTERM；必要时强制结束。', 'Stop {session} on {host}? Sends SIGTERM first, then forces termination if necessary.'],
      stopDone: ['停止操作已完成；已重新探测主机', 'Stop completed; host checked again'], staleStop: ['请刷新后再停止此任务', 'Refresh before stopping this job'],
      logs: ['查看日志尾', 'View log tail'], reloadLogs: ['刷新日志', 'Refresh log'], noLog: ['没有可用日志', 'No log available'], logSnapshot: ['日志快照 · 按需读取，不自动追踪', 'Log snapshot · fetched on demand, not streaming'],
      revoke: ['撤销', 'Revoke'], revoking: ['撤销中…', 'Revoking…'], revoked: ['授权已撤销，后续操作按策略重新审批', 'Approval revoked; future operations follow the policy'],
      expires: ['到期', 'Expires'], refreshError: ['同步失败；保留最后可用的数据', 'Sync failed; showing the last available data'],
      inspect: ['原始记录', 'Inspect record'], copy: ['复制', 'Copy'], copied: ['已复制', 'Copied'], copyFailed: ['复制失败，请手动选择文本', 'Copy failed; select the text manually'],
      details: ['详情', 'Details'], expand: ['展开', 'Expand'], collapse: ['收起', 'Collapse'], fullOutput: ['展开全部内容', 'Show full output'], boundedOutput: ['限制内容高度', 'Limit output height'],
      raw: ['原始输出', 'Raw output'], oldRecord: ['此记录没有新版展示数据，以下为原始输出。', 'This record has no compatible presentation data; showing the original output.'],
      truncated: ['结果已截断', 'Result truncated'], timedOut: ['超时', 'Timed out'], noOutput: ['没有输出', 'No output'], denied: ['已拒绝', 'Denied'],
      exit: ['退出码', 'Exit'], files: ['个文件', 'files'], matches: ['处命中', 'matches'], entries: ['项', 'entries'], lines: ['行', 'lines'], nextLine: ['下一页起始行', 'Next page starts at'],
      changed: ['读取期间文件发生变化', 'File changed during reading'], fragment: ['包含不完整行', 'Includes a line fragment'],
      clean: ['预检通过', 'Preview passed'], applied: ['已应用', 'Applied'], already_applied: ['此前已应用', 'Already applied'], unchanged: ['无变化', 'Unchanged'],
      conflict: ['冲突', 'Conflict'], restored: ['写入失败，已恢复', 'Write failed; restored'], partial: ['部分应用，需处理', 'Partially applied; action needed'], not_applied: ['未应用', 'Not applied'],
      rolled_back: ['已回滚', 'Rolled back'], already_rolled_back: ['此前已回滚', 'Already rolled back'], rollbackCopy: ['复制回滚指令', 'Copy rollback request'],
      rollbackHint: ['复制后发送到对话，由审批流程执行。', 'Paste into the conversation to run through approval.'],
      rollbackRequest: ['请回滚主机 {host} 上的补丁 {patch}（orca_rollback）；执行前按策略审批。', 'Roll back patch {patch} on host {host} with orca_rollback, following the approval policy before execution.'],
      fuzzy: ['非精确或多处匹配', 'Non-exact or ambiguous match'], saved: ['覆盖前的内容已另存', 'Overwritten content saved separately'], restoredFiles: ['已恢复文件', 'Restored files'], forceKilled: ['强制结束的进程组', 'Force-killed process groups'], noStop: ['任务已结束，本次未停止会话', 'Job already ended; no session stopped'],
      viewJob: ['在机群中查看', 'View in Fleet'], jobMissing: ['该任务不在当前快照中，可刷新主机或查看历史记录。', 'This job is outside the current snapshot. Refresh hosts or inspect its history.'],
      run: ['执行命令', 'Run command'], read: ['读取文件', 'Read file'], patch: ['修改文件', 'Patch files'], rollback: ['回滚补丁', 'Roll back patch'], status: ['主机状态', 'Host status'],
      grep: ['搜索', 'Search'], ls: ['目录', 'Directory'], launch: ['启动任务', 'Launch job'], grantTool: ['授权记录', 'Approvals'],
      poll: ['后台探测间隔', 'Background probe interval'], seconds: ['秒', 'seconds'], manual: ['关闭，仅手动刷新', 'Off; refresh manually'], discovery: ['自动发现', 'Auto-discovered'],
      whitelist: ['免询问命令', 'Pre-approved commands'], directories: ['信任目录', 'Trusted directories'], none: ['未配置', 'Not configured'],
      configHint: ['配置在当前 profile 的 cordis.patch.yml 中修改。授权只能由审批建立，机群面板可撤销。', 'Edit configuration in the current profile’s cordis.patch.yml. Approvals are created through consent and can be revoked in Fleet.'],
      approvalHint: ['“允许一次”会为以下操作记住授权 {n} 天；审批提示会明确主机、目录与类别。', '“Allow once” remembers the following actions for {n} days; the prompt names the host, directory and action.'],
      openFleet: ['打开机群面板', 'Open Fleet'], sessionRequired: ['请先打开一个会话，再使用顶栏的机群入口。', 'Open a conversation, then use Fleet in its header.'],
      transportHint: ['无法读取 Orca 接口。升级后请重启 DSH；网页登录请检查登录状态。', 'Cannot reach Orca. Restart DSH after an upgrade; on the web, check your sign-in.'],
      progressHint: ['执行期间不提供流式输出；结束后显示结果。', 'Output is available when the command finishes; it is not streamed.'],
      started: ['开始', 'Started'], finished: ['结束', 'Finished'], scope: ['范围', 'Scope'], dismiss: ['关闭提示', 'Dismiss'],
    }
    Object.assign(words, {
      authorization: ['执行授权', 'Execution permissions'], controlled: ['受控模式', 'Controlled mode'], open: ['开放模式', 'Open mode'],
      controlledHelp: ['按命令规则、信任目录与操作类别审批', 'Use command rules, trusted directories and per-action approvals'],
      openHelp: ['所有 Orca 操作免询问，命令不再拦截', 'No Orca prompts or command filtering for any Orca action'],
      openScope: ['开放模式下，Orca 不检查命令黑白名单、信任目录或授权记录，也不为执行、修改、回滚和停止操作请求审批。Harness 自身权限、主机范围与工具参数校验仍然生效。', 'In open mode, Orca skips command lists, trusted directories, remembered grants and approval prompts, including patch, rollback and stop. Harness permissions, configured hosts and tool validation still apply.'],
      activeMode: ['当前生效：{mode}', 'Active: {mode}'], pendingPolicy: ['有未保存的更改', 'Unsaved changes'], policySaved: ['已保存，后续工具调用立即使用新策略', 'Saved. Subsequent tool calls use the new policy.'],
      savePolicy: ['保存授权设置', 'Save permissions'], savingPolicy: ['保存中…', 'Saving…'], reloadPolicy: ['重新读取已保存设置', 'Reload saved settings'],
      resetPolicy: ['恢复配置文件', 'Use config file'], resetDraft: ['已载入配置文件中的策略，点击保存后生效。', 'Config-file policy loaded into the draft. Save to apply.'],
      policySource: ['来源：{source} · 保存后立即生效，重启后保留', 'Source: {source} · Applies on save and persists across restarts'], uiSource: ['前端设置', 'UI settings'], fileSource: ['配置文件', 'Config file'],
      policyFooter: ['前端保存的授权策略优先于配置文件；“恢复配置文件”并保存后重新使用文件配置。主机与探测间隔仍在 cordis.patch.yml 中设置。', 'Saved UI permissions override the config-file policy. Use “Use config file” and save to remove the override. Hosts and probe intervals remain in cordis.patch.yml.'],
      policyRules: ['受控模式规则', 'Controlled-mode rules'], rulesInactive: ['规则已保留，在切回受控模式后生效。', 'Rules are retained and apply when you return to controlled mode.'],
      denylist: ['拦截命令', 'Blocked commands'], commandHelp: ['每行一条 glob 规则，支持 * 和 ?。免询问仅用于执行命令；拦截同时用于执行与启动任务。', 'One glob per line; * and ? supported. Pre-approved rules apply to run; blocked rules apply to run and launch.'],
      directoryHelp: ['每行一个绝对路径或 glob。留空表示不限制；目录外的写操作仍需审批。', 'One absolute path or glob per line. Empty means unrestricted; writes outside these paths require approval.'],
      actionTiers: ['各操作的审批方式', 'Approval by action'], autoAllow: ['免询问', 'No prompt'], askOnce: ['首次询问并记住', 'Ask once and remember'], alwaysAsk: ['每次询问', 'Ask every time'],
      stopPolicyHelp: ['受控模式下，停止任务固定为每次询问。', 'In controlled mode, stop always requires approval.'], ttl: ['记忆授权期限（天）', 'Remember approvals (days)'], ttlHelp: ['1–90 天，仅用于新建立的授权。已有授权保留原到期时间。', '1–90 days, for new grants only. Existing grants keep their expiry.'],
      policyInvalid: ['授权期限必须为 1–90 的整数。', 'Approval duration must be an integer from 1 to 90.'],
    })
    const zh = Object.fromEntries(Object.entries(words).map(([key, v]) => [key, v[0]]))
    const en = Object.fromEntries(Object.entries(words).map(([key, v]) => [key, v[1]]))
    const tr = (t, key, params = {}) => String(t(key)).replace(/\{(\w+)\}/g, (_, k) => params[k] ?? `{${k}}`)
    const CSS = `
.orca-root{--orca-text:var(--dsw-alias-label-primary-bluish,var(--dsw-alias-label-primary));--orca-muted:var(--dsw-alias-label-secondary);--orca-surface:var(--dsw-alias-bg-base);--orca-soft:var(--dsw-alias-bg-layer-2);--orca-line:var(--dsw-alias-border-l3);--orca-accent:color-mix(in srgb,var(--dsw-alias-state-business-primary) 38%,var(--dsw-alias-state-success-primary));--orca-tint:color-mix(in srgb,var(--orca-accent) 8%,var(--orca-surface));color:var(--orca-text);font-family:var(--dsw-font-family,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif);font-size:13px;line-height:1.55;min-width:0;box-sizing:border-box;font-variant-numeric:tabular-nums}
.orca-root *{box-sizing:border-box}.orca-root.orca-root h3,.orca-root.orca-root h4{font-family:inherit;font-weight:650;line-height:1.4}.orca-panel{height:100%;overflow:auto;padding:22px 20px 28px;container-type:inline-size;background:color-mix(in srgb,var(--orca-surface) 97%,var(--orca-accent))}.orca-settings{max-width:780px;padding:8px 0 24px;container-type:inline-size}
.orca-root button{font:inherit;color:inherit;cursor:pointer}.orca-root button:disabled{cursor:default;opacity:.5}.orca-root button:focus-visible,.orca-root summary:focus-visible,.orca-root pre:focus-visible{outline:2px solid var(--orca-accent);outline-offset:3px}.orca-icon{width:16px;height:16px;flex:none;vertical-align:middle}.orca-mark{width:38px;height:38px;flex:none;display:block}.orca-brand{display:flex;gap:11px;align-items:center;min-width:0}.orca-brand h3{font-size:20px;font-weight:650;letter-spacing:-.5px;line-height:1.25;margin:0}.orca-brand p{margin:3px 0 0}.orca-muted{color:var(--orca-muted);font-size:12px}.orca-caption{color:var(--orca-muted);font-size:11px}.orca-mono{font-family:var(--dsw-font-mono,ui-monospace,monospace)}
.orca-toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:16px}.orca-toolbar h3{margin:0}.orca-toolbar p{margin:3px 0 0}.orca-btn{display:inline-flex;gap:6px;align-items:center;justify-content:center;min-height:30px;padding:5px 10px;border:1px solid var(--dsw-alias-border-l2);border-radius:7px;background:var(--orca-surface);white-space:nowrap;font-size:12px!important;font-weight:500!important;transition:background 140ms,border-color 140ms,color 140ms}.orca-btn:hover:not(:disabled){background:var(--orca-tint);border-color:color-mix(in srgb,var(--orca-accent) 45%,var(--orca-line))}.orca-btn.quiet{background:transparent;border-color:transparent}.orca-btn.primary{color:var(--orca-accent);border-color:color-mix(in srgb,var(--orca-accent) 45%,var(--orca-line))}.orca-btn.danger{color:var(--dsw-alias-state-error-primary);border-color:color-mix(in srgb,var(--dsw-alias-state-error-primary) 35%,var(--orca-line))}
.orca-overview{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));padding:16px 0;background:var(--orca-surface);border:1px solid var(--orca-line);border-radius:12px;margin:0}.orca-overview>div{display:flex;flex-direction:column-reverse;min-width:0;padding:0 12px;text-align:center;border-right:1px solid var(--orca-line)}.orca-overview>div:last-child{border:0}.orca-overview dt{font-size:11px;color:var(--orca-muted);margin-top:3px}.orca-overview dd{font-size:26px;line-height:1.2;letter-spacing:-.8px;font-weight:650;margin:0;color:var(--orca-accent)}.orca-overview .warn dd{color:var(--dsw-alias-state-warn-label)}.orca-sync{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-top:10px;font-size:11px;color:var(--orca-muted)}.orca-sync>span{display:flex;gap:5px;align-items:center}.orca-sync .orca-icon{width:13px;height:13px}
.orca-section{margin-top:22px}.orca-section>h4{font-size:13px;font-weight:650;margin:0 0 10px;display:flex;align-items:center;gap:7px}.orca-section-count{font-size:11px;line-height:20px;font-weight:550;padding:0 6px;border-radius:5px;background:var(--orca-soft);color:var(--orca-muted)}.orca-hosts{border:1px solid var(--orca-line);border-radius:12px;overflow:hidden;background:var(--orca-surface)}
.orca-row{padding:12px 0;border-bottom:1px solid var(--orca-line);min-width:0}.orca-row:last-child{border-bottom:0}.orca-row summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:10px;min-height:28px}.orca-row summary::-webkit-details-marker{display:none}.orca-chevron{margin-left:auto;color:var(--orca-muted);transition:transform 160ms;width:16px;height:16px;flex:none}.orca-row[open]>summary>.orca-chevron{transform:rotate(90deg)}.orca-host{padding:0}.orca-host>summary{padding:12px 14px;transition:background 140ms}.orca-host>summary:hover{background:var(--orca-tint)}.orca-host[open]>summary{background:color-mix(in srgb,var(--orca-tint) 50%,var(--orca-surface))}.orca-host-detail{padding:0 14px 14px;border-top:1px solid var(--orca-line)}.orca-host-detail>.orca-caption{margin-top:8px}.orca-icon-box{display:inline-flex;width:32px;height:32px;align-items:center;justify-content:center;border-radius:8px;background:var(--orca-soft);color:var(--orca-muted);flex:none}.orca-icon-box>.orca-icon{width:18px;height:18px}.orca-icon-box.terminal{background:var(--orca-tint);color:var(--orca-accent)}
.orca-identity{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;flex-shrink:0}.orca-host-info{min-width:0;flex:1}.orca-host-info>div{overflow-wrap:anywhere}.orca-host-info>.orca-identity{font-size:13px}.orca-host-state{display:flex;align-items:center;gap:5px}.orca-dot{display:inline-block;width:5px;height:5px;border-radius:50%;background:var(--dsw-alias-label-tertiary);flex:none}.orca-dot.error{background:var(--dsw-alias-state-error-primary)}.orca-dot.good{background:var(--dsw-alias-state-success-primary)}
.orca-badge{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:500;line-height:1.5;padding:3px 7px;border:0;border-radius:6px;white-space:nowrap;color:var(--orca-muted);background:var(--orca-soft);max-width:100%}.orca-badge>.orca-icon{width:13px;height:13px}.orca-badge.good{color:var(--dsw-alias-state-success-primary);background:color-mix(in srgb,var(--dsw-alias-state-success-primary) 9%,var(--orca-surface))}.orca-badge.error,.orca-error{color:var(--dsw-alias-state-error-primary)}.orca-badge.error{background:color-mix(in srgb,var(--dsw-alias-state-error-primary) 8%,var(--orca-surface))}.orca-badge.warn,.orca-warn{color:var(--dsw-alias-state-warn-label)}.orca-badge.warn{background:color-mix(in srgb,var(--dsw-alias-state-warn-primary) 10%,var(--orca-surface))}.orca-notice{font-size:12px;padding:10px 12px;border:1px solid var(--orca-line);background:var(--orca-tint);border-radius:8px;margin:10px 0;overflow-wrap:anywhere}.orca-notice.error{color:var(--dsw-alias-state-error-primary);background:color-mix(in srgb,var(--dsw-alias-state-error-primary) 6%,var(--orca-surface));border-color:color-mix(in srgb,var(--dsw-alias-state-error-primary) 20%,var(--orca-line))}.orca-notice.warn{background:color-mix(in srgb,var(--dsw-alias-state-warn-primary) 7%,var(--orca-surface))}.orca-empty{padding:18px 14px;color:var(--orca-muted);font-size:12px;overflow-wrap:anywhere;border:1px dashed var(--dsw-alias-border-l2);border-radius:10px;text-align:center}.orca-empty>.orca-icon{display:block;margin:0 auto 6px;width:22px;height:22px;color:var(--dsw-alias-label-tertiary)}
.orca-kv{display:grid;grid-template-columns:minmax(70px,auto) minmax(0,1fr);gap:7px 14px;font-size:12px;margin:12px 0}.orca-kv dt{color:var(--orca-muted)}.orca-kv dd{margin:0;text-align:right;overflow-wrap:anywhere}.orca-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));margin:14px 0;gap:10px}.orca-metrics>div{min-width:0;border-right:1px solid var(--orca-line);padding-right:8px}.orca-metrics>div:last-child{border:0;padding:0}.orca-metrics dt{font-size:11px;color:var(--orca-muted);margin-bottom:4px}.orca-metrics dd{font-size:12px;font-weight:550;line-height:1.5;margin:0;overflow-wrap:anywhere}.orca-gpu-heading{border-top:1px solid var(--orca-line);padding-top:12px;font-size:12px;font-weight:550;margin-top:10px}.orca-gpus{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px 10px;margin:12px 0 2px}.orca-gpu{font-size:10px;color:var(--orca-muted);min-width:0}.orca-gpu-label{display:flex;justify-content:space-between;gap:3px;font-size:11px;color:var(--orca-text)}.orca-meter{height:5px;background:var(--orca-soft);border-radius:3px;overflow:hidden;margin:5px 0}.orca-meter>i{height:100%;display:block;background:var(--orca-accent);border-radius:3px;transition:width 180ms}.orca-gpu-memory{font-size:10px;white-space:nowrap}
.orca-job{padding:13px 14px;border:1px solid var(--orca-line)!important;border-radius:12px;background:var(--orca-surface);margin:8px 0}.orca-job.active{border-left:3px solid var(--orca-accent)!important}.orca-job-head{display:flex;align-items:center;justify-content:space-between;gap:8px}.orca-job-title{display:flex;align-items:center;gap:9px;min-width:0;flex:1}.orca-job-name{font-size:13px;font-weight:600;overflow-wrap:anywhere;min-width:0}.orca-job-meta{display:flex;flex-wrap:wrap;gap:4px 7px;font-size:11px;color:var(--orca-muted);margin-top:3px}.orca-job-actions{display:flex;gap:7px;margin-top:10px;flex-wrap:wrap}.orca-confirm{padding:12px;background:var(--orca-soft);border:1px solid var(--orca-line);border-radius:8px;margin-top:12px}.orca-confirm p{margin:0 0 8px;overflow-wrap:anywhere}.orca-highlight{outline:2px solid var(--orca-accent);outline-offset:2px}
.orca-pre{font:12px/1.65 var(--dsw-font-mono,ui-monospace,monospace);white-space:pre;overflow:auto;max-height:320px;padding:12px;margin:10px 0;background:var(--orca-soft);border:1px solid var(--orca-line);border-radius:8px;tab-size:4}.orca-pre.full{max-height:none}.orca-pre.wrap{white-space:pre-wrap;overflow-wrap:anywhere}.orca-log-preview{font:11px/1.65 var(--dsw-font-mono,ui-monospace,monospace);color:#f4f9fc;background:#102d46;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:12px;padding:8px 10px;border-radius:7px}.orca-history{padding:12px 14px;background:var(--orca-surface);border:1px solid var(--orca-line)!important;border-radius:10px}.orca-history>summary{min-height:20px;font-size:12px}
.orca-tool{border:1px solid var(--orca-line);border-radius:12px;background:var(--orca-surface);margin:8px 0;overflow:hidden;container-type:inline-size}.orca-tool-head{padding:12px}.orca-toggle{border:0;background:transparent;text-align:left;padding:0;width:100%;display:flex;align-items:center;gap:10px;min-width:0}.orca-toggle>.orca-chevron{margin-left:0}.orca-toggle[aria-expanded=true]>.orca-chevron{transform:rotate(90deg)}.orca-tool-title{min-width:0;flex:1}.orca-tool-title>.orca-identity{font-size:13px;font-weight:600;line-height:1.5}.orca-tool-title strong{font-size:12px;font-weight:400;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:3px;color:var(--orca-muted)}.orca-tool-path{font-size:11px;color:var(--orca-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}.orca-tool-body{padding:0 12px 12px;border-top:1px solid var(--orca-line);overflow-wrap:anywhere}.orca-tool-body>.orca-toolbar{margin-top:10px;margin-bottom:4px}.orca-tool-actions{display:flex;flex-wrap:wrap;gap:6px}.orca-tool-actions .orca-btn{font-size:11px!important;min-height:28px;padding:4px 7px}.orca-line{display:block;min-height:1.65em}.orca-line.add{background:var(--dsw-alias-file-diff-added-bg)}.orca-line.del{background:var(--dsw-alias-file-diff-deleted-bg)}.orca-line.hunk{color:var(--orca-muted)}.orca-number{display:inline-block;min-width:4.5em;padding-right:12px;text-align:right;user-select:none;color:var(--dsw-alias-label-tertiary)}.orca-match{background:var(--orca-tint)}.orca-root mark{background:color-mix(in srgb,var(--dsw-alias-state-warn-primary) 25%,transparent);color:inherit}.orca-file{margin:10px 0 4px;font:12px var(--dsw-font-mono,monospace);overflow-wrap:anywhere}.orca-chip-list{display:flex;flex-wrap:wrap;gap:8px;padding:12px;background:var(--orca-surface);border:1px solid var(--orca-line);border-radius:10px}.orca-chip{padding:5px 9px;background:var(--orca-soft);border-radius:6px;font:12px/1.5 var(--dsw-font-mono,monospace);overflow-wrap:anywhere;max-width:100%}.orca-settings-summary{display:grid;grid-template-columns:1fr 1fr;border:1px solid var(--orca-line);border-radius:12px;padding:16px;margin-top:16px;gap:16px;background:var(--orca-surface)}.orca-settings-summary>div{display:flex;gap:10px;align-items:center}.orca-settings-summary .orca-icon{width:20px;height:20px;color:var(--orca-muted)}.orca-settings-summary strong{display:block;font-size:17px;color:var(--orca-accent);font-weight:600;margin-top:3px}.orca-settings-footer{display:flex;gap:8px;align-items:flex-start;margin-top:24px;border-top:1px solid var(--orca-line);padding-top:14px}
.orca-header{display:inline-flex;gap:6px;align-items:center;min-height:28px}.orca-header .orca-mark{width:22px;height:22px}.orca-spinner{width:11px;height:11px;border:1.5px solid var(--dsw-alias-border-l2);border-top-color:currentColor;border-radius:50%;animation:orca-spin 1s linear infinite;display:inline-block;flex-shrink:0}@keyframes orca-spin{to{transform:rotate(360deg)}}
@container(max-width:420px){.orca-overview dd{font-size:23px}.orca-overview>div{padding:0 8px}.orca-overview dt{font-size:10px}.orca-metrics{grid-template-columns:1fr}.orca-metrics>div{border:0;padding:0;display:flex;justify-content:space-between;gap:12px}.orca-metrics dt{margin:0}.orca-gpus{grid-template-columns:repeat(2,minmax(0,1fr))}.orca-host>summary{gap:8px;padding:11px}.orca-host>summary>.orca-badge{font-size:10px;padding:3px 5px}.orca-brand h3{font-size:18px}.orca-tool-title>.orca-identity{font-size:12px}.orca-tool .orca-icon-box{width:28px;height:28px}.orca-tool-head{padding:10px}.orca-toggle{gap:8px}.orca-tool-title strong{white-space:normal;overflow-wrap:anywhere}}
.orca-policy-modes{display:grid;grid-template-columns:1fr 1fr;gap:10px}.orca-mode{display:flex;align-items:flex-start;gap:10px;padding:15px;border:1px solid var(--orca-line);border-radius:10px;background:var(--orca-surface);cursor:pointer}.orca-mode.selected{border-color:var(--orca-accent);background:var(--orca-tint)}.orca-mode input{margin:4px 0 0;accent-color:var(--orca-accent);flex:none}.orca-mode strong{display:block;font-size:14px;margin-bottom:3px}.orca-mode small{display:block;color:var(--orca-muted);font-size:12px}.orca-policy-status{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin:12px 0}.orca-policy-actions{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.orca-policy-rules{margin-top:20px;border:1px solid var(--orca-line);border-radius:10px;padding:14px;background:var(--orca-surface)}.orca-policy-rules>summary{cursor:pointer;font-weight:600}.orca-policy-fields{border:0;padding:0;margin:12px 0 0;min-width:0}.orca-policy-fields:disabled{opacity:.55}.orca-field{display:flex;flex-direction:column;gap:6px;margin:16px 0;font-size:12px;font-weight:550}.orca-field small{font-weight:400;color:var(--orca-muted)}.orca-input{width:100%;min-width:0;border:1px solid var(--orca-line);border-radius:7px;padding:9px 10px;background:var(--orca-surface);color:var(--orca-text);font:inherit}.orca-input:focus-visible,.orca-mode input:focus-visible{outline:2px solid var(--orca-accent);outline-offset:2px}textarea.orca-input{resize:vertical;line-height:1.6;min-height:86px;font-family:var(--dsw-font-mono,monospace)}.orca-tier-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px solid var(--orca-line)}.orca-tier-row .orca-input{width:190px;max-width:55%}.orca-policy-fields input[type=number]{max-width:150px}
/* The host settings shell keeps a fixed nav rail; give this section usable room on phones. */
@media(max-width:600px){[data-shortcut-modal="settings"]:has(.orca-settings){flex-direction:column!important;width:calc(100vw - 24px)!important;height:calc(100dvh - 24px)!important;max-height:calc(100dvh - 24px)!important}[data-shortcut-modal="settings"]:has(.orca-settings)>nav{width:100%!important;min-width:0!important;flex:0 0 auto!important;padding:12px!important;max-height:110px}[data-shortcut-modal="settings"]:has(.orca-settings)>nav>div:last-child{display:flex!important;flex-direction:row!important;overflow:auto;gap:4px}[data-shortcut-modal="settings"]:has(.orca-settings)>nav button{flex-shrink:0!important;width:auto!important}[data-shortcut-modal="settings"]:has(.orca-settings)>div{min-height:0;min-width:0;width:100%}.orca-settings{padding:8px 0 20px}}
@container(max-width:440px){.orca-policy-modes{grid-template-columns:1fr}.orca-policy-status{align-items:flex-start}.orca-tier-row .orca-input{max-width:60%;width:165px}}
@container(max-width:320px){.orca-brand .orca-mark{width:30px;height:30px}.orca-toolbar{align-items:flex-start}.orca-overview dd{font-size:20px}.orca-host>summary{flex-wrap:wrap}.orca-host>summary>.orca-host-info{flex-basis:calc(100% - 64px)}.orca-host>summary>.orca-badge{margin-left:40px}.orca-job-head{flex-wrap:wrap}.orca-tool .orca-toggle{flex-wrap:wrap}.orca-tool .orca-tool-title{flex-basis:calc(100% - 40px)}.orca-tool .orca-toggle>.orca-badge{margin-left:36px}.orca-settings-summary{grid-template-columns:1fr}.orca-kv{grid-template-columns:1fr}.orca-kv dd{text-align:left}}
@media(prefers-reduced-motion:reduce){.orca-root *{transition:none!important}.orca-spinner{animation:none}}
`
    const bytes = n => {
      if (typeof n !== 'number' || !Number.isFinite(n)) return '—'
      const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']; let i = 0
      while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
      return `${n >= 100 ? Math.round(n) : n.toFixed(1)} ${units[i]}`
    }
    const absolute = ts => ts ? new Date(ts).toLocaleString() : '—'
    function Ago({ at, t }) {
      if (!at) return h('span', null, '—')
      const seconds = Math.max(0, Math.round((Date.now() - new Date(at).getTime()) / 1000))
      if (!Number.isFinite(seconds)) return h('span', null, '—')
      const locale = t('fleet') === '机群' ? 'zh' : 'en'
      const [amount, unit] = seconds < 60 ? [seconds, 'second'] : seconds < 3600 ? [Math.floor(seconds / 60), 'minute'] : seconds < 86400 ? [Math.floor(seconds / 3600), 'hour'] : [Math.floor(seconds / 86400), 'day']
      return h('time', { dateTime: new Date(at).toISOString(), title: absolute(at) }, new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(-amount, unit))
    }
    const diskLow = disk => disk?.total_bytes > 0 && (disk.free_bytes / disk.total_bytes < .05 || disk.free_bytes < 20 * 1024 ** 3)
    const iconPaths = {'server': 'M4 3.5h12v5H4zM4 11.5h12v5H4zM6.5 6h.01M6.5 14h.01M10 6h3.5M10 14h3.5', 'chevron': 'm7.5 5 5 5-5 5', 'refresh': 'M16.5 7A7 7 0 1 0 17 12M16.5 2.5V7H12', 'clock': 'M10 6v4l3 2M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0', 'info': 'M10 9v5M10 6h.01M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0', 'shield': 'm10 2 6.5 3v5c0 4-4 6.5-6.5 8-2.5-1.5-6.5-4-6.5-8V5zM7 9.5l2 2 4-4', 'folder': 'M2.5 5h6l2 2h7v10h-15zM2.5 5V3h6l2 2h7v2', 'file': 'M5 2.5h6l4 4v11H5zM11 2.5V7h4M8 10h4M8 13h4', 'copy': 'M7 6h10v11H7zM4 14H3V3h10v1', 'expand': 'M7 3H3v4M13 3h4v4M17 13v4h-4M7 17H3v-4', 'eye': 'M1.5 10s3-5 8.5-5 8.5 5 8.5 5-3 5-8.5 5-8.5-5-8.5-5M12 10a2 2 0 1 1-4 0 2 2 0 0 1 4 0', 'stop': 'M5 5h10v10H5z', 'terminal': 'm5 6 4 4-4 4M11 14h4', 'check': 'm5 10 3.5 3.5L15 6.5', 'alert': 'M9 3.5 2 16h16L11 3.5zM10 7v4M10 13.5h.01', 'search': 'M12.5 12.5 17 17M14 8.5a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0', 'rollback': 'M3 4v5h5M3 9a7 7 0 1 1 2 6', 'gpu': 'M4 4h12v12H4zM7 7h6v6H7zM1 7h3M1 13h3M16 7h3M16 13h3M7 1v3M13 1v3M7 16v3M13 16v3'}
    function Icon({ name = 'server', className = '' }) {
      return h('svg', { className: `orca-icon ${className}`, viewBox: '0 0 20 20', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true }, h('path', { d: iconPaths[name] ?? iconPaths.server }))
    }
    function OrcaMark() {
      return h('svg', { className: 'orca-mark', viewBox: '0 0 128 128', fill: 'none', 'aria-hidden': true },
        h('rect', { width: 128, height: 128, rx: 28, fill: '#102d46' }),
        h('path', { d: 'M6.5 60C12 65 23 63 25 72C26 77 39 69 49 60C56 54 60 53 62 50C67 46 62 35 59 31C56 27 65 28 71 33C78 38 78 45 84 45C98 43 114 45 121 52C132 63 105 68 97 74C105 62 95 60 87 63C70 66 60 73 48 78C34 84 24 78 19 75C12 73 7 70 6.5 60Z', fill: '#ffffff' }),
        h('path', { d: 'M95.5 53.5C98 49.5 110 51.5 112 55.5C114 59 102 57.5 99 57C96 56.5 94 55.5 95.5 53.5Z', fill: '#102d46' }),
        h('path', { d: 'M95 66.5C98 64.5 94 82 81 87C76 89 78 71 95 66.5ZM46 84C53 82 56 75 63 75.5C72 75 66 82 74 81C76 81 78 80.5 78 80.5C66 85 53 85.5 46 84Z', fill: '#ffffff' }),
        h('path', { d: 'M8 68.5C13 75 22 73 24 82C25.5 87 24 91 23.5 92C22 86 14 85 10 77C9 74 8.5 71 8 68.5Z', fill: '#2fb0b8' }))
    }
    const ServerIcon = () => h(Icon, { name: 'server' })
    const IconBox = ({ name = 'server' }) => h('span', { className: `orca-icon-box ${name === 'terminal' ? 'terminal' : ''}` }, h(Icon, { name }))
    const Brand = ({ title, subtitle }) => h('div', { className: 'orca-brand' }, h(OrcaMark), h('div', null, h('h3', null, title), h('p', { className: 'orca-caption' }, subtitle)))
    const Button = ({ children, className = '', ...props }) => h('button', { type: 'button', className: `orca-btn ${className}`, ...props }, children)
    const Badge = ({ text, tone = '', spin = false, icon }) => h('span', { className: `orca-badge ${tone}` }, spin ? h('i', { className: 'orca-spinner', 'aria-hidden': true }) : icon ? h(Icon, { name: icon }) : null, text)
    const Notice = ({ children, tone = '', role = 'status' }) => h('div', { className: `orca-notice ${tone}`, role }, children)
    const Empty = ({ children, icon }) => h('div', { className: 'orca-empty' }, icon ? h(Icon, { name: icon }) : null, children)
    function Copy({ text, label, t }) {
      const [state, setState] = useState('')
      return h('span', null, h(Button, { onClick: async () => {
        try { await navigator.clipboard.writeText(text); setState('copied') } catch { setState('copyFailed') }
      } }, h(Icon, { name: state === 'copied' ? 'check' : 'copy' }), label ?? t('copy')), state ? h('span', { className: 'orca-caption', role: 'status' }, ` ${t(state)}`) : null)
    }
    async function api(path, body = {}, signal) {
      const response = await fetch(`api/dsh-orca/${path}`, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), signal })
      let result
      try { result = await response.json() } catch { throw new Error(`HTTP ${response.status}`) }
      if (!response.ok) throw new Error(typeof result.error === 'string' ? result.error : `HTTP ${response.status}`)
      return result
    }
    function createFleetStore() {
      let state = { data: null, error: null, busy: false, grants: null, grantsError: null, focus: null }
      const listeners = new Set(); const controller = new AbortController()
      let timer, pending, pendingRefresh = false, grantPending
      const publish = patch => { if (!controller.signal.aborted) { state = { ...state, ...patch }; listeners.forEach(fn => fn()) } }
      const load = async (refresh = false) => {
        if (pending) {
          if (!refresh || pendingRefresh) return pending
          await pending
          return load(true)
        }
        if (controller.signal.aborted) return
        pendingRefresh = refresh
        publish({ busy: refresh || !state.data })
        pending = api('summary', refresh ? { refresh: true } : {}, controller.signal)
          .then(data => publish({ data, error: null })).catch(error => publish({ error: error.message }))
          .finally(() => { pending = null; pendingRefresh = false; publish({ busy: false }) })
        return pending
      }
      const grants = () => {
        if (grantPending) return grantPending
        grantPending = api('grants/list', {}, controller.signal).then(data => publish({ grants: data.grants, grantsError: null }))
          .catch(error => publish({ grantsError: error.message })).finally(() => { grantPending = null })
        return grantPending
      }
      const tick = () => { if (document.visibilityState === 'visible' && listeners.size) void load() }
      const start = () => { tick(); timer = setInterval(tick, 15000); document.addEventListener('visibilitychange', tick) }
      const stop = () => { clearInterval(timer); timer = undefined; document.removeEventListener('visibilitychange', tick) }
      return { getSnapshot: () => state, subscribe(fn) { listeners.add(fn); if (listeners.size === 1) start(); return () => { listeners.delete(fn); if (!listeners.size) stop() } },
        load, grants, focus: value => publish({ focus: value }), signal: controller.signal,
        dispose() { stop(); controller.abort(); listeners.clear() } }
    }
    function useFleet(store, active = true) {
      const subscribe = React.useCallback(fn => active ? store.subscribe(fn) : () => {}, [store, active])
      return useSyncExternalStore(subscribe, store.getSnapshot, store.getSnapshot)
    }
    function Metrics({ host, t }) {
      const gpu = host.gpu
      const rows = [[t('load'), host.load?.map(n => Number(n).toFixed(1)).join(' / ') ?? '—'],
        [t('memory'), `${bytes(host.memory?.available_bytes)} / ${bytes(host.memory?.total_bytes)}`],
        [t('disk'), `${bytes(host.disk?.free_bytes)} / ${bytes(host.disk?.total_bytes)}`]]
      const mib = n => n == null ? '—' : (n / 1024).toFixed(n >= 102400 ? 0 : 1)
      return h(React.Fragment, null,
        h('dl', { className: 'orca-metrics' }, rows.map(([k, v]) => h('div', { key: k }, h('dt', null, k), h('dd', null, v)))),
        host.disk?.path ? h('div', { className: 'orca-caption orca-mono' }, host.disk.path) : null,
        gpu ? h('div', null, h('div', { className: 'orca-gpu-heading' }, `${gpu.count} × ${gpu.model ?? 'GPU'}`),
          h('div', { className: 'orca-gpus' }, (gpu.cards ?? []).map(card => h('div', { className: 'orca-gpu', key: card.index },
            h('div', { className: 'orca-gpu-label' }, h('span', null, `#${card.index}`), h('span', null, card.util == null ? '—' : `${card.util}%`)),
            h('div', { className: 'orca-meter', 'aria-hidden': true }, h('i', { style: { width: `${Math.min(100, Math.max(0, card.util ?? 0))}%` } })),
            h('div', { className: 'orca-gpu-memory' }, `${mib(card.used)} / ${mib(card.total)} GiB`)))),
          !gpu.cards?.length ? h('div', { className: 'orca-caption' }, `${t('vram')} ${bytes(gpu.memUsedMib * 1024 ** 2)}`) : null) : h('div', { className: 'orca-caption' }, host.gpuError || t('noGpu')))
    }
    function HostRow({ host, t }) {
      const issue = host.error && host.attemptedAt ? 'unreachable' : !host.at ? 'unpolled' : host.stale ? 'stale' : 'reachable'
      return h('details', { className: 'orca-row orca-host' }, h('summary', null,
        h(IconBox, { name: 'server' }),
        h('span', { className: 'orca-host-info' }, h('span', { className: 'orca-identity orca-mono' }, host.host),
          h('div', { className: 'orca-caption orca-host-state' }, h('i', { className: `orca-dot ${issue === 'unreachable' ? 'error' : issue === 'reachable' ? 'good' : ''}`, 'aria-hidden': true }), t(issue), host.running ? ` · ${host.running} ${t('running')}` : '')),
        diskLow(host.disk) ? h(Badge, { text: t('diskLow'), tone: 'warn', icon: 'alert' }) : host.gpu?.utilAvg != null ? h('span', { className: 'orca-muted' }, `GPU ${host.gpu.utilAvg}%`) : null,
        h(Icon, { name: 'chevron', className: 'orca-chevron' })),
        h('div', { className: 'orca-host-detail' },
          h('div', { className: 'orca-caption' }, host.hostname, host.at ? ` · ${t('cache')} ` : '', host.at ? h(Ago, { at: host.at, t }) : null),
          host.error && host.attemptedAt ? h(Notice, { tone: 'error' }, host.error) : null,
          host.at ? h(Metrics, { host, t }) : null))
    }
    function JobRow({ job, store, t, focus }) {
      const [confirm, setConfirm] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState(null), [done, setDone] = useState(false)
      const [log, setLog] = useState(null), [logBusy, setLogBusy] = useState(false), [showLog, setShowLog] = useState(false)
      const ref = useRef(null), locked = useRef(false)
      useEffect(() => { if (focus) ref.current?.scrollIntoView({ block: 'nearest' }) }, [focus])
      const loadLog = async () => {
        setShowLog(true); setLogBusy(true); setError(null)
        try { setLog(await api('job/tail', { host: job.host, session: job.session, jobId: job.jobId, lines: 50 }, store.signal)) }
        catch (e) { setError(e.message) } finally { setLogBusy(false) }
      }
      const stop = async () => {
        if (locked.current) return
        locked.current = true; setBusy(true); setError(null)
        try { await api('job/stop', { host: job.host, session: job.session, jobId: job.jobId }, store.signal); setDone(true); setConfirm(false); await store.load() }
        catch (e) { setError(e.message) } finally { locked.current = false; setBusy(false) }
      }
      const state = job.stale && job.active ? 'unknown' : job.state
      return h('article', { className: `orca-job ${job.active && !job.stale ? 'active' : ''} ${focus ? 'orca-highlight' : ''}`, ref },
        h('div', { className: 'orca-job-head' },
          h('div', { className: 'orca-job-title' }, h(IconBox, { name: 'terminal' }), h('div', { style: { minWidth: 0 } },
            h('div', { className: 'orca-job-name orca-mono' }, job.session),
            h('div', { className: 'orca-job-meta' }, h('span', { className: 'orca-mono' }, `@${job.host}`), '·', h(Ago, { at: job.startedAt, t }), job.exitCode != null ? `${t('exit')} ${job.exitCode}` : null, job.stale ? t('stale') : null))),
          h(Badge, { text: t(words[state] ? state : 'unknown'), tone: state === 'failed' ? 'error' : state === 'running' || state === 'succeeded' ? 'good' : '', icon: state === 'succeeded' ? 'check' : undefined })),
        job.logTail ? h('div', { className: 'orca-log-preview', title: job.logTail }, job.logTail.trim()) : null,
        h('div', { className: 'orca-job-actions' }, h(Button, { disabled: logBusy, onClick: showLog ? () => setShowLog(false) : loadLog }, h(Icon, { name: 'file' }), logBusy ? t('loading') : showLog ? t('collapse') : t('logs')),
          job.active && !done ? h(Button, { disabled: busy || job.stale || !job.jobId, title: job.stale ? t('staleStop') : undefined, onClick: () => setConfirm(true) }, h(Icon, { name: 'stop' }), t('stop')) : null),
        confirm ? h('div', { className: 'orca-confirm', onKeyDown: e => { if (e.key === 'Escape' && !busy) setConfirm(false) } },
          h('p', null, tr(t, 'stopExplain', { host: job.host, session: job.session })),
          h('div', { className: 'orca-job-actions' }, h(Button, { disabled: busy, onClick: () => setConfirm(false) }, t('cancel')),
            h(Button, { className: 'danger', disabled: busy || job.stale, onClick: stop }, t(busy ? 'stopping' : 'confirmStop')))) : null,
        done ? h(Notice, null, t('stopDone')) : null, error ? h(Notice, { tone: 'error', role: 'alert' }, error) : null,
        showLog ? h('div', null, h('div', { className: 'orca-caption' }, t('logSnapshot')),
          h('pre', { className: 'orca-pre wrap', tabIndex: 0 }, logBusy && !log ? t('loading') : log?.log_tail || t('noLog')),
          h(Button, { disabled: logBusy, onClick: loadLog }, t('reloadLogs'))) : null)
    }
    function GrantRow({ grant, store, t }) {
      const [busy, setBusy] = useState(false), [error, setError] = useState(null), [done, setDone] = useState(false)
      const revoke = async () => {
        setBusy(true); setError(null)
        try { await api('grants/revoke', { host: grant.host, workdir: grant.workdir, action: grant.action }, store.signal); setDone(true); await store.grants() }
        catch (e) { setError(e.message) } finally { setBusy(false) }
      }
      return h('div', { className: 'orca-row' }, h('div', { className: 'orca-job-head' },
        h('div', { className: 'orca-job-name' }, h('span', { className: 'orca-mono' }, `${grant.action} · ${grant.host}`),
          h('div', { className: 'orca-caption orca-mono', style: { overflowWrap: 'anywhere' } }, grant.workdir)),
        h(Button, { disabled: busy || done, onClick: revoke }, t(busy ? 'revoking' : 'revoke'))),
        h('div', { className: 'orca-caption' }, `${t('expires')} · ${absolute(grant.expiresAt)}`),
        error ? h(Notice, { tone: 'error' }, error) : null, done ? h(Notice, null, t('revoked')) : null)
    }
    function Section({ title, count, children, icon }) {
      return h('section', { className: 'orca-section' }, h('h4', null, icon ? h(Icon, { name: icon }) : null, title, count != null ? h('span', { className: 'orca-section-count' }, count) : null), children)
    }
    function History({ jobs, store, t, focus }) {
      const [open, setOpen] = useState(false)
      useEffect(() => { if (focus && jobs.some(j => j.host === focus.host && j.session === focus.session)) setOpen(true) }, [focus, jobs])
      return h('details', { className: 'orca-row orca-history orca-section', open, onToggle: e => setOpen(e.currentTarget.open) },
        h('summary', { className: 'orca-muted' }, h(Icon, { name: 'clock' }), t('recentJobs'), ` · ${jobs.length}`, h(Icon, { name: 'chevron', className: 'orca-chevron' })),
        open ? jobs.length ? jobs.map(job => h(JobRow, { key: `${job.host}:${job.jobId ?? job.session}`, job, store, t, focus: focus?.host === job.host && focus?.session === job.session })) : h(Empty, null, t('noRecent')) : null)
    }
    function FleetPanel({ store, t, useTabInfo }) {
      const info = useTabInfo()
      const active = info.tab.visible
      const state = useFleet(store, active), { data, error, busy, grants, grantsError, focus } = state
      useEffect(() => {
        if (!active) return
        void store.grants()
        const timer = setInterval(() => { if (!document.hidden) void store.grants() }, 15000)
        return () => clearInterval(timer)
      }, [active, store])
      const hosts = data?.hosts ?? [], jobs = data?.jobs ?? []
      const running = jobs.filter(j => j.active && !j.stale), recent = jobs.filter(j => !j.active || j.stale)
      const found = !focus || jobs.some(j => j.host === focus.host && j.session === focus.session && (!focus.jobId || j.jobId === focus.jobId))
      return h('div', { className: 'orca-root orca-panel', 'aria-label': t('fleet') },
        h('div', { className: 'orca-toolbar' }, h(Brand, { title: t('fleetTitle'), subtitle: t('fleetSubtitle') }),
          h(Button, { className: 'primary', disabled: busy, onClick: () => { void store.load(true); void store.grants() } }, busy ? h('i', { className: 'orca-spinner', 'aria-hidden': true }) : h(Icon, { name: 'refresh' }), t(busy ? 'refreshing' : 'refresh'))),
        data ? h(React.Fragment, null,
          h('dl', { className: 'orca-overview' },
            [[`${hosts.filter(host => host.ok && !host.stale).length}/${hosts.length}`, 'reachable'], [`${running.length}${data.jobsComplete ? '' : '+'}`, 'runningJobs'], [hosts.filter(host => !host.stale && diskLow(host.disk)).length, 'diskLow']].map(([value, label]) => h('div', { key: label, className: label === 'diskLow' && value > 0 ? 'warn' : '' }, h('dt', null, t(label)), h('dd', null, value)))),
          h('div', { className: 'orca-sync' }, h('span', null, h(Icon, { name: 'clock' }), t('lastProbe'), ' · ', h(Ago, { at: data.lastRoundAt, t })),
            h('span', { title: tr(t, 'coverage', { n: data.config.jobLimit }) }, h(Icon, { name: 'info' }), tr(t, 'coverageShort', { n: data.config.jobLimit }), data.jobsComplete ? '' : ` · ${t('incomplete')}`))) : null,
        error ? h(Notice, { tone: 'error', role: 'alert' }, t(data ? 'refreshError' : 'transportHint'), h('div', { className: 'orca-caption' }, error), h(Button, { disabled: busy, onClick: () => store.load() }, t('retry'))) : null,
        !data ? (!error ? h(Empty, null, t('loading')) : null) : h(React.Fragment, null,
          !found ? h(Notice, null, t('jobMissing'), h(Button, { className: 'quiet', onClick: () => store.focus(null) }, t('dismiss'))) : null,
          h(Section, { title: t('activeJobs'), count: `${running.length}${data.jobsComplete ? '' : '+'}` }, running.length ? running.map(job => h(JobRow, { key: `${job.host}:${job.jobId ?? job.session}`, job, store, t, focus: focus?.host === job.host && focus?.session === job.session })) : h(Empty, { icon: 'terminal' }, t('noJobs'))),
          h(Section, { title: t('hosts'), count: hosts.length }, hosts.length ? h('div', { className: 'orca-hosts' }, hosts.map(host => h(HostRow, { host, t, key: host.host }))) : h(Empty, null, t('noHosts'))),
          h(History, { jobs: recent, store, t, focus })),
        h(Section, { title: t('grants'), icon: 'shield', count: grants?.length }, grantsError ? h(Notice, { tone: 'error' }, grantsError, h(Button, { onClick: store.grants }, t('retry'))) : null,
          grants ? grants.length ? grants.map(grant => h(GrantRow, { key: JSON.stringify([grant.host, grant.workdir, grant.action]), grant, store, t })) : h(Empty, { icon: 'shield' }, t('noGrants')) : !grantsError ? h(Empty, null, t('loading')) : null))
    }
    function Header({ store, t, openFleet }) {
      const { data, error } = useFleet(store)
      const jobs = data?.jobs ?? [], hosts = data?.hosts ?? []
      const count = jobs.filter(j => j.active && !j.stale).length
      const alert = error || hosts.some(host => host.error && host.attemptedAt || diskLow(host.disk)) || jobs.some(job => job.state === 'failed' && Date.now() - new Date(job.finishedAt).getTime() < 86400000)
      return h('span', { className: 'orca-root' }, h(Button, { className: 'quiet orca-header', title: `${t('allRegistered')}${data?.jobsComplete ? '' : ` · ${t('incomplete')}`}`, onClick: () => openFleet() },
        h(OrcaMark), t('fleet'), data ? `${count}${data.jobsComplete ? '' : '+'}` : '—', alert ? h('span', { className: 'orca-dot error', 'aria-label': t('attention'), title: t('attention') }) : null))
    }
    const policyLists = ['allowedCommands', 'deniedCommands', 'trustedWorkdirs']
    const policyDraft = policy => ({ ...policy, ...Object.fromEntries(policyLists.map(key => [key, policy[key].join('\n')])) })
    const policyValue = draft => ({ ...draft, grantTtlDays: Number(draft.grantTtlDays), ...Object.fromEntries(policyLists.map(key => [key, draft[key].split('\n').map(line => line.trim()).filter(Boolean)])) })
    function PolicyEditor({ store, t }) {
      const [savedPolicy, setSavedPolicy] = useState(null), [draft, setDraft] = useState(null)
      const [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState(''), [reset, setReset] = useState(false)
      const accept = value => { setSavedPolicy(value); setDraft(policyDraft(value.policy)); setReset(false) }
      useEffect(() => {
        const controller = new AbortController()
        api('settings/get', {}, controller.signal).then(accept).catch(err => { if (!controller.signal.aborted) setError(err.message) })
        return () => controller.abort()
      }, [])
      const reload = async () => {
        setBusy(true); setError(''); setMessage('')
        try { accept(await api('settings/get', {}, store.signal)) } catch (err) { setError(err.message) } finally { setBusy(false) }
      }
      const edit = (key, value) => { setDraft(old => ({ ...old, [key]: value })); setReset(false); setMessage('') }
      const dirty = !!draft && (reset || JSON.stringify(policyValue(draft)) !== JSON.stringify(savedPolicy.policy))
      const save = async event => {
        event.preventDefault()
        if (!dirty || busy) return
        const policy = policyValue(draft)
        if (!Number.isInteger(policy.grantTtlDays) || policy.grantTtlDays < 1 || policy.grantTtlDays > 90) { setError(t('policyInvalid')); return }
        setBusy(true); setError(''); setMessage('')
        try {
          accept(await api('settings/save', reset ? { revision: savedPolicy.revision, reset: true } : { revision: savedPolicy.revision, policy }, store.signal))
          setMessage('policySaved'); void store.load()
        } catch (err) { setError(err.message) } finally { setBusy(false) }
      }
      const tier = action => action === 'stop' || draft.alwaysAsk.includes(action) ? 'alwaysAsk' : draft.askOnce.includes(action) ? 'askOnce' : draft.autoAllow.includes(action) || ['status', 'read', 'grep', 'ls', 'grants'].includes(action) ? 'autoAllow' : 'alwaysAsk'
      const setTier = (action, target) => {
        setDraft(old => ({ ...old, ...Object.fromEntries(['autoAllow', 'askOnce', 'alwaysAsk'].map(key => [key, [...old[key].filter(item => item !== action), ...(key === target ? [action] : [])]])) }))
        setReset(false); setMessage('')
      }
      return h('form', { onSubmit: save, 'aria-label': t('authorization'), 'aria-busy': busy },
        error ? h(Notice, { tone: 'error', role: 'alert' }, error, h('div', { className: 'orca-policy-actions' }, h(Button, { onClick: reload, disabled: busy }, t('reloadPolicy')))) : null,
        !draft ? (!error ? h(Empty, null, t('loading')) : null) : h(React.Fragment, null,
          h('div', { className: 'orca-policy-status' }, h(Badge, { icon: 'shield', tone: savedPolicy.policy.mode === 'open' ? 'warn' : 'good', text: tr(t, 'activeMode', { mode: t(savedPolicy.policy.mode) }) }), dirty ? h('span', { className: 'orca-caption', role: 'status' }, t('pendingPolicy')) : null),
          h('div', { className: 'orca-policy-modes', role: 'radiogroup', 'aria-label': t('authorization') }, ['controlled', 'open'].map(mode => h('label', { className: `orca-mode ${draft.mode === mode ? 'selected' : ''}`, key: mode },
            h('input', { type: 'radio', name: 'orca-policy-mode', value: mode, checked: draft.mode === mode, disabled: busy, onChange: () => edit('mode', mode) }), h('span', null, h('strong', null, t(mode)), h('small', null, t(`${mode}Help`)))))),
          draft.mode === 'open' ? h(Notice, { tone: 'warn' }, t('openScope')) : null,
          h('p', { className: 'orca-caption' }, tr(t, 'policySource', { source: t(savedPolicy.source === 'ui' ? 'uiSource' : 'fileSource') })),
          message ? h(Notice, null, t(message)) : null,
          h('div', { className: 'orca-policy-actions' }, h(Button, { type: 'submit', className: 'primary', disabled: busy || !dirty }, h(Icon, { name: 'check' }), t(busy ? 'savingPolicy' : 'savePolicy')),
            h(Button, { disabled: busy || !dirty, onClick: () => { accept(savedPolicy); setMessage(''); setError('') } }, t('cancel')),
            h(Button, { className: 'quiet', disabled: busy, onClick: () => { setDraft(policyDraft(savedPolicy.basePolicy)); setReset(true); setMessage('resetDraft'); setError('') } }, t('resetPolicy'))),
          h('details', { className: 'orca-policy-rules' }, h('summary', null, t('policyRules')),
            draft.mode === 'open' ? h('p', { className: 'orca-muted' }, t('rulesInactive')) : null,
            h('fieldset', { className: 'orca-policy-fields', disabled: busy || draft.mode === 'open' },
              ['allowedCommands', 'deniedCommands', 'trustedWorkdirs'].map((key, i) => h('label', { className: 'orca-field', key }, t(['whitelist', 'denylist', 'directories'][i]),
                h('textarea', { className: 'orca-input', rows: key === 'deniedCommands' ? 6 : 3, value: draft[key], spellCheck: false, onChange: e => edit(key, e.target.value) }),
                h('small', null, t(key === 'trustedWorkdirs' ? 'directoryHelp' : 'commandHelp')))),
              h('h4', null, t('actionTiers')),
              ['status', 'read', 'grep', 'ls', 'grants', 'run', 'launch', 'patch', 'rollback', 'stop'].map(action => h('label', { className: 'orca-tier-row', key: action }, h('span', null, t(action === 'grants' ? 'grantTool' : action)),
                h('select', { className: 'orca-input', value: tier(action), disabled: action === 'stop', onChange: e => setTier(action, e.target.value) }, ['autoAllow', 'askOnce', 'alwaysAsk'].map(key => h('option', { key, value: key }, t(key)))))),
              h('p', { className: 'orca-caption' }, t('stopPolicyHelp')),
              h('label', { className: 'orca-field' }, t('ttl'), h('input', { className: 'orca-input', type: 'number', min: 1, max: 90, required: true, value: draft.grantTtlDays, onChange: e => edit('grantTtlDays', e.target.value) }), h('small', null, t('ttlHelp'))),
            h('div', { className: 'orca-policy-actions' }, h(Button, { type: 'submit', className: 'primary', disabled: busy || !dirty }, t(busy ? 'savingPolicy' : 'savePolicy')))))))
    }
    function Settings({ store, t }) {
      const { data, error } = useFleet(store)
      const config = data?.config
      return h('div', { className: 'orca-root orca-settings' }, h('div', { className: 'orca-toolbar' }, h(Brand, { title: t('config'), subtitle: t('configSubtitle') })),
        h(Section, { title: t('authorization'), icon: 'shield' }, h(PolicyEditor, { store, t })),
        error ? h(Notice, { tone: 'error' }, t('transportHint'), h('div', null, error)) : null,
        config ? h('div', { className: 'orca-settings-summary' },
          h('div', null, h(Icon, { name: 'clock' }), h('div', null, h('span', { className: 'orca-caption' }, t('poll')), h('strong', null, config.pollSeconds ? `${config.pollSeconds} ${t('seconds')}` : t('manual')))),
          h('div', null, h(ServerIcon), h('div', null, h('span', { className: 'orca-caption' }, t('hosts')), h('strong', null, `${config.panelHosts.length}${config.discovered ? ` · ${t('discovery')}` : ''}`)))) : null,
        h('p', { className: 'orca-muted orca-settings-footer' }, h(Icon, { name: 'file' }), t('policyFooter')))
    }
    function readArg(block, key) {
      try { return typeof block.args?.text === 'function' ? block.args.text(key) : block.args?.[key] } catch { return undefined }
    }
    const cut = (text, range) => Array.isArray(range) && range.length === 2 && range.every(Number.isInteger) && range[0] >= 0 && range[1] >= range[0] && range[1] <= text.length ? text.slice(...range) : ''
    const toolKey = name => name === 'orca_grants' ? 'grantTool' : name.replace(/^orca_/, '')
    function toolState(block, m) {
      if (!('kind' in block)) return block.phase === 'preparing' ? 'preparing' : 'running'
      if (block.error?.code === 'interrupted') return 'stopped'
      if (block.error?.info?.code === 'ORCA_DENIED' || block.error?.code === 'ORCA_DENIED') return 'denied'
      if (block.isError) return 'failed'
      if (m?.timed_out) return 'timedOut'
      if (m?.tool === 'orca_stop') return m.session_stopped ? 'stopped' : m.was_running === false ? 'unchanged' : 'failed'
      if (m?.state) return words[m.state] ? m.state : 'failed'
      if (m?.exit_code != null && m.exit_code !== 0) return 'failed'
      return 'succeeded'
    }
    const errorStates = new Set(['failed', 'conflict', 'partial', 'not_applied'])
    const warningStates = new Set(['timedOut', 'restored'])
    function ToolBody({ name, m, text, t, full }) {
      const pre = (value, cls = '') => h('pre', { className: `orca-pre ${full ? 'full' : ''} ${cls}`, tabIndex: 0 }, value || t('noOutput'))
      if (!m) return h(React.Fragment, null, h('p', { className: 'orca-caption' }, t('oldRecord')), pre(text, 'wrap'))
      if (name === 'orca_run') return h(React.Fragment, null, m.stdout ? pre(cut(text, m.stdout)) : null,
        m.stderr ? h('div', null, h('div', { className: 'orca-caption' }, 'stderr'), pre(cut(text, m.stderr))) : null,
        !m.stdout && !m.stderr ? h(Empty, null, t('noOutput')) : null)
      if (name === 'orca_status') return h(React.Fragment, null, h(Metrics, { host: m, t }),
        m.observedAt ? h('div', { className: 'orca-caption' }, absolute(m.observedAt)) : null,
        h('p', { className: 'orca-caption' }, t('allRegistered'), m.jobsComplete ? '' : ` · ${t('incomplete')}`),
        (m.jobs ?? []).map((job, i) => h('div', { className: 'orca-row', key: job.jobId ?? i }, h('span', { className: 'orca-mono' }, job.session), ' · ', t(words[job.state] ? job.state : 'unknown'), job.exitCode != null ? ` · ${t('exit')} ${job.exitCode}` : '', job.logTail ? pre(job.logTail) : null)))
      if (name === 'orca_read') return h(React.Fragment, null,
        m.changed ? h(Notice, { tone: 'warn' }, t('changed')) : null, m.fragment ? h(Notice, { tone: 'warn' }, t('fragment')) : null,
        pre(cut(text, m.body) ? cut(text, m.body).replace(/\n$/, '').split('\n').map((line, i) => h('span', { className: 'orca-line', key: i }, h('span', { className: 'orca-number' }, (m.first_line ?? 1) + i), line)) : ''),
        m.next_start_line != null ? h('div', { className: 'orca-caption' }, `${t('nextLine')}: ${m.next_start_line}`) : null)
      if (name === 'orca_patch') return h(React.Fragment, null,
        m.error ? h(Notice, { tone: 'error' }, m.error) : null,
        m.uncertain ? h(Notice, { tone: 'warn' }, m.uncertain) : null,
        (m.files ?? []).map((file, i) => h('div', { key: i, className: 'orca-row' },
          h('div', { className: 'orca-file' }, `${file.action} ${file.path}${file.move_to ? ` → ${file.move_to}` : ''}  +${file.added} −${file.removed}`),
          file.fuzzy?.length ? h('div', { className: 'orca-warn orca-caption' }, t('fuzzy'), ': ', file.fuzzy.map(f => `hunk ${f.hunk} · ${f.level} · ${f.line}${f.also_matches_lines ? ` / ${f.also_matches_lines.join(', ')}` : ''}`).join('; ')) : null)),
        [...(m.conflicts ?? []), ...(m.restore_errors ?? [])].map((c, i) => h(Notice, { tone: 'error', key: `c${i}` }, h('strong', null, c.path), h('div', null, c.message))),
        (m.warnings ?? []).map((w, i) => h(Notice, { tone: 'warn', key: `w${i}` }, w)),
        m.body ? pre(cut(text, m.body).split('\n').map((line, i) => h('span', { className: `orca-line ${line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : line.startsWith('@@') ? 'hunk' : ''}`, key: i }, line))) : null)
      if (name === 'orca_grep') {
        let previous
        return h('div', { className: `orca-pre ${full ? 'full' : ''}`, tabIndex: 0 }, (m.rows ?? []).map((row, i) => {
          const file = row.path !== previous; previous = row.path
          const line = cut(text, row.body)
          // Python regex semantics stay on the host; regex matches highlight the row.
          let content = line
          if (row.match && m.fixed && m.pattern) {
            const hay = m.ignoreCase ? line.toLowerCase() : line, needle = m.ignoreCase ? m.pattern.toLowerCase() : m.pattern
            const parts = []; let start = 0, index
            while ((index = hay.indexOf(needle, start)) >= 0) { parts.push(line.slice(start, index), h('mark', { key: index }, line.slice(index, index + needle.length))); start = index + needle.length }
            parts.push(line.slice(start)); content = parts
          }
          return h(React.Fragment, { key: i }, file ? h('div', { className: 'orca-file' }, row.path) : null,
            h('div', { className: `orca-line ${row.match ? 'orca-match' : 'orca-caption'}` }, h('span', { className: 'orca-number' }, row.line), content))
        }))
      }
      if (name === 'orca_stop') return h(React.Fragment, null,
        h('p', null, t(m.session_stopped ? 'stopped' : m.was_running === false ? 'noStop' : 'unknown')),
        m.sigkilled ? h(Notice, { tone: 'warn' }, `${t('forceKilled')}: ${m.sigkilled}`) : null)
      if (name === 'orca_rollback') return h(React.Fragment, null,
        h('div', { className: 'orca-file' }, m.patch_id),
        (m.restored ?? []).map((path, i) => h('div', { className: 'orca-file', key: i }, path)),
        (m.conflicts ?? []).map((c, i) => h(Notice, { tone: 'error', key: i }, `${c.path}: ${c.message}`)),
        (m.saved ?? []).map((saved, i) => h(Notice, { tone: 'warn', key: `s${i}` }, `${t('saved')}: ${saved.path} → ${saved.saved_as}`)),
        pre(text, 'wrap'))
      if (name === 'orca_launch') return h('dl', { className: 'orca-kv' }, h('dt', null, t('run')), h('dd', { className: 'orca-mono' }, m.command), h('dt', null, t('logs')), h('dd', { className: 'orca-mono' }, m.log_path))
      return pre(cut(text, m.body) || text, name === 'orca_rollback' || name === 'orca_grants' ? 'wrap' : '')
    }
    function ToolCard({ block, toolName, t, inspect, openFleet }) {
      const done = 'kind' in block
      const text = done ? (Array.isArray(block.content) ? block.content.map(c => c.type === 'text' ? c.text : JSON.stringify(c)).join('\n') : String(block.content ?? '')) : ''
      const m = done && block.meta?.version === 1 && block.meta.tool === toolName ? block.meta : null
      const state = toolState(block, m), exceptional = errorStates.has(state) || warningStates.has(state)
      const [chosen, setChosen] = useState(null), [full, setFull] = useState(false)
      const open = chosen ?? exceptional
      const host = m?.host ?? readArg(block, 'host')
      const path = m?.workdir ?? m?.path ?? readArg(block, 'workdir') ?? readArg(block, 'path')
      let title = m?.command ?? m?.session ?? readArg(block, 'command') ?? readArg(block, 'session') ?? path ?? t(toolKey(toolName))
      if (m && toolName === 'orca_patch') title = `${m.files?.length ?? 0} ${t('files')} · +${(m.files ?? []).reduce((n, f) => n + f.added, 0)} −${(m.files ?? []).reduce((n, f) => n + f.removed, 0)}`
      if (m && toolName === 'orca_grep') title = `${m.pattern} · ${m.count} ${t('matches')} / ${m.files_matched} ${t('files')}`
      if (m && toolName === 'orca_status') title = `${m.gpu ? `${m.gpu.count} × ${m.gpu.model ?? 'GPU'}` : t('status')} · ${m.running}${m.jobsComplete ? '' : '+'} ${t('running')}`
      if (m && toolName === 'orca_read') title = `${path} · ${m.first_line ?? '?'}–${m.last_line ?? '?'}${m.total_lines != null ? ` / ${m.total_lines}` : ''} ${t('lines')}`
      if (m && toolName === 'orca_ls') title = `${path} · ${m.count} ${t('entries')}`
      if (m && toolName === 'orca_rollback') title = `${m.patch_id} · ${t('restoredFiles')} ${m.restored?.length ?? 0}`
      if (m && toolName === 'orca_grants') title = `${m.action === 'revoke' ? t('revoke') : t('grantTool')} · ${m.count}`
      const status = m?.exit_code != null && !m.timed_out ? `${t('exit')} ${m.exit_code}` : t(state)
      const rollback = m?.patch_id && ['applied', 'partial', 'already_applied'].includes(m.state) && toolName === 'orca_patch'
      return h('article', { className: 'orca-root orca-tool' },
        h('div', { className: 'orca-tool-head' },
          h('button', { type: 'button', className: 'orca-toggle', 'aria-expanded': !!open, onClick: () => setChosen(!open) },
            h(IconBox, { name: ({ run: 'terminal', launch: 'terminal', status: 'server', read: 'file', patch: 'file', ls: 'folder', grep: 'search', rollback: 'rollback', stop: 'stop', grantTool: 'shield' })[toolKey(toolName)] }),
            h('span', { className: 'orca-tool-title' }, h('span', { className: 'orca-identity' }, host ?? 'Orca', ` · ${t(toolKey(toolName))}`),
              h('strong', { className: 'orca-mono', title }, title), path && path !== title ? h('div', { className: 'orca-tool-path orca-mono', title: path }, path) : null),
            h(Badge, { text: status, tone: errorStates.has(state) ? 'error' : warningStates.has(state) ? 'warn' : state === 'succeeded' ? 'good' : '', spin: !done, icon: state === 'succeeded' ? 'check' : exceptional ? 'alert' : undefined }),
            h(Icon, { name: 'chevron', className: 'orca-chevron' }))),
        open ? h('div', { className: 'orca-tool-body' },
          h('div', { className: 'orca-toolbar' }, h('div', { className: 'orca-tool-actions' }, done ? h(Copy, { text, t }) : null,
            done ? h(Button, { className: 'quiet', onClick: () => setFull(!full) }, h(Icon, { name: 'expand' }), t(full ? 'boundedOutput' : 'fullOutput')) : null,
            inspect ? h(Button, { className: 'quiet', onClick: inspect }, h(Icon, { name: 'eye' }), t('inspect')) : null)),
          m?.truncated ? h(Notice, { tone: 'warn' }, t('truncated')) : null,
          !done ? h(React.Fragment, null, h('pre', { className: 'orca-pre wrap' }, readArg(block, 'command') ?? readArg(block, 'patch') ?? path ?? t('preparing')), h('p', { className: 'orca-caption' }, t('progressHint'))) :
            block.isError ? h(Notice, { tone: state === 'denied' || state === 'stopped' ? '' : 'error' }, text || block.error?.code || t('failed')) : h(ToolBody, { name: toolName, m, text, t, full }),
          rollback ? h('div', { className: 'orca-job-actions' }, h(Copy, { label: t('rollbackCopy'), text: tr(t, 'rollbackRequest', { host, patch: m.patch_id }), t }), h('span', { className: 'orca-caption' }, t('rollbackHint'))) : null,
          toolName === 'orca_launch' && done && !block.isError ? h(Button, { onClick: () => openFleet({ host, session: m?.session ?? readArg(block, 'session'), jobId: m?.job_id }) }, t('viewJob')) : null) : null)
    }
    class CardBoundary extends React.Component {
      constructor(props) { super(props); this.state = { failed: false } }
      static getDerivedStateFromError() { return { failed: true } }
      render() {
        if (!this.state.failed) return this.props.children
        const { block, t } = this.props
        const output = (Array.isArray(block?.content) ? block.content : []).map(c => c.text ?? '').join('\n')
        return h('div', { className: 'orca-root orca-tool' }, h(Notice, null, t('oldRecord')), h('pre', { className: 'orca-pre wrap' }, output))
      }
    }
    return {
      inject: ['slots', 'locale'],
      apply(ctx) {
        const store = createFleetStore()
        const openFleet = focus => { store.focus(focus ?? null); ctx.get('sidebarRight')?.openTab('dsh-orca') }
        ctx.effect(() => () => store.dispose())
        ctx.effect(() => ctx.locale.register('orca', { zh, en }))
        const t = ctx.locale.bind('orca')
        ctx.effect(() => {
          const style = document.createElement('style'); style.dataset.pluginCss = 'dsh-orca'; style.textContent = CSS
          document.head.appendChild(style)
          return () => style.remove()
        })
        const register = (name, options, Component) => ctx.slots.inject(name, () => ctx.slots.register({ name, locale: 'orca', ...options }, props => Component === ToolCard ? h(CardBoundary, { key: props.callId, block: props.block, t: props.t }, h(Component, { ...props, store, openFleet })) : h(Component, { ...props, store, openFleet })))
        register('settings.section', { id: 'orca', order: 120, label: () => t('config') }, Settings)
        register('conversation.session.header.actions', { id: 'orca-fleet', order: 25 }, Header)
        for (const tool of ['status', 'read', 'run', 'launch', 'stop', 'grep', 'ls', 'patch', 'rollback', 'grants']) register('tool.call.toolview', { key: `orca_${tool}` }, ToolCard)
        ctx.inject(['sidebarRightTabs', 'sidebarRight'], scope => {
          scope.effect(() => scope.get('sidebarRightTabs').register({ id: 'dsh-orca', kind: 'dsh-orca', title: () => t('fleet'), keepMounted: true,
            guide: [{ id: 'orca-fleet', order: 35, title: () => t('fleet'), description: () => t('guide'), icon: OrcaMark }] }))
          scope.slots.inject('sidebar.right.pane.tab', () => scope.slots.register({ name: 'sidebar.right.pane.tab', key: 'dsh-orca', locale: 'orca' }, props => h(FleetPanel, { ...props, store })))
        })
      },
    }
  },
})
