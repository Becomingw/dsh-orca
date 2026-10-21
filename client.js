/** Native DSH UI: versioned tool cards, shared cached fleet view, and settings. */
window.__ModuleLoader__.load({
  id: 'dsh-orca',
  factory(require) {
    const React = require('react')
    const h = React.createElement
    const { useState, useEffect, useRef, useSyncExternalStore } = React
    const words = {
      fleet: ['机群', 'Fleet'], config: ['Orca 配置', 'Orca settings'], guide: ['主机状态、已登记任务与授权', 'Hosts, registered jobs and approvals'],
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
    const zh = Object.fromEntries(Object.entries(words).map(([key, v]) => [key, v[0]]))
    const en = Object.fromEntries(Object.entries(words).map(([key, v]) => [key, v[1]]))
    const tr = (t, key, params = {}) => String(t(key)).replace(/\{(\w+)\}/g, (_, k) => params[k] ?? `{${k}}`)
    const CSS = `
.orca-root{color:var(--dsw-alias-label-primary);font-size:13px;line-height:1.55;min-width:0;box-sizing:border-box;font-variant-numeric:tabular-nums}
.orca-root *{box-sizing:border-box}.orca-panel{height:100%;overflow:auto;padding:18px 18px 28px;container-type:inline-size}.orca-settings{max-width:780px;padding:8px 0 20px}
.orca-root button{font:inherit;color:inherit;cursor:pointer}.orca-root button:disabled{cursor:default;opacity:.5}.orca-root button:focus-visible,.orca-root summary:focus-visible{outline:2px solid var(--dsw-alias-state-business-primary);outline-offset:3px}
.orca-muted{color:var(--dsw-alias-label-secondary);font-size:12px}.orca-caption{color:var(--dsw-alias-label-tertiary);font-size:12px}.orca-mono{font-family:var(--dsw-font-mono,monospace)}
.orca-toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:12px}.orca-toolbar h3{margin:0;font-size:16px;font-weight:600}.orca-toolbar p{margin:4px 0 0}
.orca-btn{display:inline-flex;gap:6px;align-items:center;justify-content:center;min-height:30px;padding:4px 10px;border:1px solid var(--dsw-alias-border-l2);border-radius:var(--dsw-radius-sm,8px);background:transparent;white-space:nowrap}.orca-btn:hover:not(:disabled){background:var(--dsw-alias-interactive-bg-hover)}.orca-btn.quiet{border-color:transparent}.orca-btn.danger{color:var(--dsw-alias-state-error-primary)}
.orca-section{margin-top:24px}.orca-section>h4{font-size:12px;letter-spacing:.02em;font-weight:600;color:var(--dsw-alias-label-secondary);margin:0 0 10px;display:flex;justify-content:space-between;gap:8px}.orca-section-count{font-weight:400}
.orca-row{padding:12px 0;border-bottom:1px solid var(--dsw-alias-border-l3);min-width:0}.orca-row:last-child{border-bottom:0}.orca-row summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:9px;min-height:28px}.orca-row summary::-webkit-details-marker{display:none}.orca-row summary:after{content:'›';margin-left:auto;color:var(--dsw-alias-label-tertiary)}.orca-row[open]>summary:after{transform:rotate(90deg)}
.orca-identity{display:inline-flex;align-items:center;gap:6px;font-family:var(--dsw-font-mono,monospace);font-size:12px;font-weight:600;flex-shrink:0}.orca-identity svg{width:15px;height:15px;color:var(--dsw-alias-label-secondary)}.orca-host-info{min-width:0;flex:1}.orca-host-info>div{overflow-wrap:anywhere}.orca-dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--dsw-alias-label-tertiary);flex:none}.orca-dot.error{background:var(--dsw-alias-state-error-primary)}
.orca-badge{display:inline-flex;align-items:center;gap:5px;font-size:11px;line-height:1.5;padding:2px 7px;border:1px solid var(--dsw-alias-border-l3);border-radius:var(--dsw-radius-sm,8px);white-space:nowrap;color:var(--dsw-alias-label-secondary);max-width:100%}.orca-badge.error,.orca-error{color:var(--dsw-alias-state-error-primary)}.orca-badge.warn,.orca-warn{color:var(--dsw-alias-state-warn-label)}
.orca-notice{font-size:12px;padding:10px 12px;border-left:2px solid var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-layer-2);border-radius:0 var(--dsw-radius-sm,8px) var(--dsw-radius-sm,8px) 0;margin:10px 0;overflow-wrap:anywhere}.orca-notice.error{border-left-color:var(--dsw-alias-state-error-primary)}.orca-notice.warn{border-left-color:var(--dsw-alias-state-warn-primary)}
.orca-empty{padding:14px 0;color:var(--dsw-alias-label-secondary);font-size:12px;overflow-wrap:anywhere}.orca-kv{display:grid;grid-template-columns:minmax(70px,auto) minmax(0,1fr);gap:7px 14px;font-size:12px;margin:12px 0}.orca-kv dt{color:var(--dsw-alias-label-secondary)}.orca-kv dd{margin:0;text-align:right;overflow-wrap:anywhere}
.orca-gpus{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}.orca-gpu{flex:1 1 64px;max-width:110px;font-size:11px;color:var(--dsw-alias-label-secondary)}.orca-meter{height:4px;background:var(--dsw-alias-bg-layer-2);border-radius:2px;overflow:hidden;margin:4px 0}.orca-meter>i{height:100%;display:block;background:var(--dsw-alias-label-secondary)}
.orca-job-head{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}.orca-job-name{font-size:13px;font-weight:500;overflow-wrap:anywhere}.orca-job-meta{display:flex;flex-wrap:wrap;gap:4px 8px;font-size:12px;color:var(--dsw-alias-label-secondary);margin-top:4px}.orca-job-actions{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}.orca-confirm{padding:10px 12px;background:var(--dsw-alias-bg-layer-2);border-radius:var(--dsw-radius-sm,8px);margin-top:10px}.orca-confirm p{margin:0 0 8px;overflow-wrap:anywhere}.orca-highlight{border-left:2px solid var(--dsw-alias-state-business-primary);padding-left:10px}
.orca-pre{font:12px/1.65 var(--dsw-font-mono,monospace);white-space:pre;overflow:auto;max-height:320px;padding:12px;margin:8px 0;background:var(--dsw-alias-bg-layer-2);border-radius:var(--dsw-radius-sm,8px);tab-size:4}.orca-pre.full{max-height:none}.orca-pre.wrap{white-space:pre-wrap;overflow-wrap:anywhere}.orca-log-preview{font:11px/1.5 var(--dsw-font-mono,monospace);color:var(--dsw-alias-label-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:6px}
.orca-tool{border:1px solid var(--dsw-alias-border-l3);border-radius:var(--dsw-radius-md,10px);margin:6px 0;overflow:hidden;container-type:inline-size}.orca-tool-head{display:flex;align-items:flex-start;gap:8px;padding:10px 12px}.orca-toggle{border:0;background:transparent;text-align:left;padding:0;min-width:0;flex:1;display:flex;align-items:flex-start;gap:9px}.orca-tool-title{min-width:0;flex:1}.orca-tool-title strong{font-size:13px;font-weight:500;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.orca-tool-path{font-size:11px;color:var(--dsw-alias-label-tertiary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.orca-tool-body{padding:0 12px 12px;border-top:1px solid var(--dsw-alias-border-l3);overflow-wrap:anywhere}.orca-tool-body>.orca-toolbar{margin-top:10px;margin-bottom:4px}.orca-tool-actions{display:flex;flex-wrap:wrap;gap:6px}.orca-line{display:block;min-height:1.65em}.orca-line.add{background:color-mix(in srgb,var(--dsw-alias-state-success-primary) 12%,transparent)}.orca-line.del{background:color-mix(in srgb,var(--dsw-alias-state-error-primary) 12%,transparent)}.orca-line.hunk{color:var(--dsw-alias-label-tertiary)}.orca-number{display:inline-block;min-width:4.5em;padding-right:12px;text-align:right;user-select:none;color:var(--dsw-alias-label-tertiary)}.orca-match{background:color-mix(in srgb,var(--dsw-alias-state-business-primary) 12%,transparent)}.orca-root mark{background:color-mix(in srgb,var(--dsw-alias-state-warn-primary) 25%,transparent);color:inherit}.orca-file{margin:10px 0 4px;font:12px var(--dsw-font-mono,monospace);overflow-wrap:anywhere}.orca-chip-list{display:flex;flex-wrap:wrap;gap:6px}.orca-chip{padding:4px 8px;background:var(--dsw-alias-bg-layer-2);border-radius:var(--dsw-radius-sm,8px);font:12px var(--dsw-font-mono,monospace);overflow-wrap:anywhere;max-width:100%}
.orca-header{display:inline-flex;gap:6px;align-items:center;min-height:28px}.orca-spinner{width:11px;height:11px;border:1.5px solid var(--dsw-alias-border-l2);border-top-color:currentColor;border-radius:50%;animation:orca-spin 1s linear infinite;display:inline-block;flex-shrink:0}@keyframes orca-spin{to{transform:rotate(360deg)}}
@container(max-width:360px){.orca-tool-head{flex-wrap:wrap}.orca-tool-head>.orca-badge{margin-left:24px}.orca-kv{grid-template-columns:1fr}.orca-kv dd{text-align:left}.orca-job-head{flex-wrap:wrap}.orca-toolbar{align-items:flex-start}.orca-tool-path{white-space:normal;overflow-wrap:anywhere}}
@media(prefers-reduced-motion:reduce){.orca-spinner{animation:none}}
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
    function ServerIcon() {
      return h('svg', { viewBox: '0 0 20 20', fill: 'none', stroke: 'currentColor', strokeWidth: 1.4, 'aria-hidden': true }, [
        h('rect', { key: 'a', x: 3, y: 3, width: 14, height: 6, rx: 1.5 }), h('rect', { key: 'b', x: 3, y: 11, width: 14, height: 6, rx: 1.5 }),
        h('path', { key: 'c', d: 'M6 6h.1M6 14h.1M10 6h4M10 14h4', strokeLinecap: 'round' }),
      ])
    }
    const Button = ({ children, className = '', ...props }) => h('button', { type: 'button', className: `orca-btn ${className}`, ...props }, children)
    const Badge = ({ text, tone = '', spin = false }) => h('span', { className: `orca-badge ${tone}` }, spin ? h('i', { className: 'orca-spinner', 'aria-hidden': true }) : null, text)
    const Notice = ({ children, tone = '', role = 'status' }) => h('div', { className: `orca-notice ${tone}`, role }, children)
    const Empty = ({ children }) => h('div', { className: 'orca-empty' }, children)
    function Copy({ text, label, t }) {
      const [state, setState] = useState('')
      return h('span', null, h(Button, { onClick: async () => {
        try { await navigator.clipboard.writeText(text); setState('copied') } catch { setState('copyFailed') }
      } }, label ?? t('copy')), state ? h('span', { className: 'orca-caption', role: 'status' }, ` ${t(state)}`) : null)
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
      return h(React.Fragment, null,
        h('dl', { className: 'orca-kv' }, rows.flatMap(([k, v]) => [h('dt', { key: `${k}k` }, k), h('dd', { key: `${k}v` }, v)])),
        host.disk?.path ? h('div', { className: 'orca-caption orca-mono' }, host.disk.path) : null,
        gpu ? h('div', null, h('div', { className: 'orca-muted' }, `${gpu.count} × ${gpu.model ?? 'GPU'} · ${t('vram')} ${bytes(gpu.memUsedMib * 1024 ** 2)} / ${bytes(gpu.memTotalMib == null ? null : gpu.memTotalMib * 1024 ** 2)}`),
          h('div', { className: 'orca-gpus' }, (gpu.cards ?? []).map(card => h('div', { className: 'orca-gpu', key: card.index },
            `#${card.index} · ${card.util == null ? '—' : `${card.util}%`}`,
            h('div', { className: 'orca-meter', 'aria-hidden': true }, h('i', { style: { width: `${Math.min(100, Math.max(0, card.util ?? 0))}%` } })),
            `${bytes(card.used == null ? null : card.used * 1024 ** 2)} / ${bytes(card.total == null ? null : card.total * 1024 ** 2)}`)))) : h('div', { className: 'orca-caption' }, host.gpuError || t('noGpu')))
    }
    function HostRow({ host, t }) {
      const issue = host.error && host.attemptedAt ? 'unreachable' : !host.at ? 'unpolled' : host.stale ? 'stale' : 'reachable'
      return h('details', { className: 'orca-row' }, h('summary', null,
        h('span', { className: `orca-dot ${issue === 'unreachable' ? 'error' : ''}`, 'aria-hidden': true }),
        h('span', { className: 'orca-host-info' }, h('span', { className: 'orca-identity' }, h(ServerIcon), host.host),
          h('div', { className: 'orca-caption' }, t(issue), host.running ? ` · ${host.running} ${t('running')}` : '')),
        diskLow(host.disk) ? h(Badge, { text: t('diskLow'), tone: 'warn' }) : host.gpu?.utilAvg != null ? h('span', { className: 'orca-muted' }, `GPU ${host.gpu.utilAvg}%`) : null),
        h('div', { className: 'orca-caption', style: { marginTop: 8 } }, host.hostname),
        host.at ? h('div', { className: 'orca-caption' }, `${t('cache')} · `, h(Ago, { at: host.at, t })) : null,
        host.error && host.attemptedAt ? h(Notice, { tone: 'error' }, host.error) : null,
        host.at ? h(Metrics, { host, t }) : null)
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
      return h('article', { className: `orca-row ${focus ? 'orca-highlight' : ''}`, ref },
        h('div', { className: 'orca-job-head' }, h('div', { className: 'orca-job-name orca-mono' }, job.session),
          h(Badge, { text: t(words[state] ? state : 'unknown'), tone: state === 'failed' ? 'error' : '' })),
        h('div', { className: 'orca-job-meta' }, h('span', { className: 'orca-mono' }, `@${job.host}`), h(Ago, { at: job.startedAt, t }), job.exitCode != null ? `${t('exit')} ${job.exitCode}` : null, job.stale ? t('stale') : null),
        job.logTail ? h('div', { className: 'orca-log-preview', title: job.logTail }, job.logTail.trim()) : null,
        h('div', { className: 'orca-job-actions' }, h(Button, { className: 'quiet', disabled: logBusy, onClick: showLog ? () => setShowLog(false) : loadLog }, logBusy ? t('loading') : showLog ? t('collapse') : t('logs')),
          job.active && !done ? h(Button, { className: 'quiet', disabled: busy || job.stale || !job.jobId, title: job.stale ? t('staleStop') : undefined, onClick: () => setConfirm(true) }, t('stop')) : null),
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
    function Section({ title, count, children }) {
      return h('section', { className: 'orca-section' }, h('h4', null, title, count != null ? h('span', { className: 'orca-section-count' }, count) : null), children)
    }
    function History({ jobs, store, t, focus }) {
      const [open, setOpen] = useState(false)
      useEffect(() => { if (focus && jobs.some(j => j.host === focus.host && j.session === focus.session)) setOpen(true) }, [focus, jobs])
      return h('details', { className: 'orca-row orca-section', open, onToggle: e => setOpen(e.currentTarget.open) },
        h('summary', { className: 'orca-muted' }, t('recentJobs'), ` · ${jobs.length}`),
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
        h('div', { className: 'orca-toolbar' }, h('div', null, h('h3', null, t('fleet')),
          h('p', { className: 'orca-caption' }, data ? `${hosts.filter(host => host.ok && !host.stale).length}/${hosts.length} · ${t('reachable')}` : t('loading')),
          data?.lastRoundAt ? h('p', { className: 'orca-caption' }, `${t('lastProbe')} · `, h(Ago, { at: data.lastRoundAt, t })) : null),
          h(Button, { disabled: busy, onClick: () => { void store.load(true); void store.grants() } }, t(busy ? 'refreshing' : 'refresh'))),
        error ? h(Notice, { tone: 'error', role: 'alert' }, t(data ? 'refreshError' : 'transportHint'), h('div', { className: 'orca-caption' }, error), h(Button, { disabled: busy, onClick: () => store.load() }, t('retry'))) : null,
        !data ? (!error ? h(Empty, null, t('loading')) : null) : h(React.Fragment, null,
          h('div', { className: 'orca-caption' }, tr(t, 'coverage', { n: data.config.jobLimit })),
          !found ? h(Notice, null, t('jobMissing'), h(Button, { className: 'quiet', onClick: () => store.focus(null) }, t('dismiss'))) : null,
          h(Section, { title: t('runningJobs'), count: `${running.length}${data.jobsComplete ? '' : '+'}` }, running.length ? running.map(job => h(JobRow, { key: `${job.host}:${job.jobId ?? job.session}`, job, store, t, focus: focus?.host === job.host && focus?.session === job.session })) : h(Empty, null, t('noJobs'))),
          h(Section, { title: t('hosts'), count: hosts.length }, hosts.length ? hosts.map(host => h(HostRow, { host, t, key: host.host })) : h(Empty, null, t('noHosts'))),
          h(History, { jobs: recent, store, t, focus })),
        h(Section, { title: t('grants'), count: grants?.length }, grantsError ? h(Notice, { tone: 'error' }, grantsError, h(Button, { onClick: store.grants }, t('retry'))) : null,
          grants ? grants.length ? grants.map(grant => h(GrantRow, { key: JSON.stringify([grant.host, grant.workdir, grant.action]), grant, store, t })) : h(Empty, null, t('noGrants')) : !grantsError ? h(Empty, null, t('loading')) : null))
    }
    function Header({ store, t, openFleet }) {
      const { data, error } = useFleet(store)
      const jobs = data?.jobs ?? [], hosts = data?.hosts ?? []
      const count = jobs.filter(j => j.active && !j.stale).length
      const alert = error || hosts.some(host => host.error && host.attemptedAt || diskLow(host.disk)) || jobs.some(job => job.state === 'failed' && Date.now() - new Date(job.finishedAt).getTime() < 86400000)
      return h('span', { className: 'orca-root' }, h(Button, { className: 'quiet orca-header', title: `${t('allRegistered')}${data?.jobsComplete ? '' : ` · ${t('incomplete')}`}`, onClick: () => openFleet() },
        h('span', { className: 'orca-identity' }, h(ServerIcon)), t('fleet'), data ? `${count}${data.jobsComplete ? '' : '+'}` : '—', alert ? h('span', { className: 'orca-dot error', 'aria-label': t('attention'), title: t('attention') }) : null))
    }
    function Settings({ store, t, openFleet }) {
      const { data, error } = useFleet(store)
      const config = data?.config
      return h('div', { className: 'orca-root orca-settings' }, h('div', { className: 'orca-toolbar' }, h('h3', null, t('config'))),
        h(Notice, null, t('sessionRequired')),
        error ? h(Notice, { tone: 'error' }, t('transportHint'), h('div', null, error)) : null,
        !config ? (!error ? h(Empty, null, t('loading')) : null) : h(React.Fragment, null,
          h('dl', { className: 'orca-kv' }, h('dt', null, t('poll')), h('dd', null, config.pollSeconds ? `${config.pollSeconds} ${t('seconds')}` : t('manual')),
          h('dt', null, t('hosts')), h('dd', null, `${config.panelHosts.length}${config.discovered ? ` · ${t('discovery')}` : ''}`)),
          h(Section, { title: t('whitelist') }, h('div', { className: 'orca-chip-list' }, config.allowedCommands.length ? config.allowedCommands.map(s => h('span', { key: s, className: 'orca-chip' }, s)) : t('none'))),
          h(Section, { title: t('directories') }, h('div', { className: 'orca-chip-list' }, config.trustedWorkdirs.length ? config.trustedWorkdirs.map(s => h('span', { key: s, className: 'orca-chip' }, s)) : t('none'))),
          h(Section, { title: t('grants') }, h('p', { className: 'orca-muted' }, tr(t, 'approvalHint', { n: config.grantTtlDays })),
            h('div', { className: 'orca-chip-list' }, (config.askOnce ?? []).filter(s => !(config.alwaysAsk ?? []).includes(s)).map(s => h('span', { className: 'orca-chip', key: s }, s))))),
        h('p', { className: 'orca-muted' }, t('configHint')))
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
            h('span', { className: 'orca-caption', 'aria-hidden': true }, open ? '⌄' : '›'),
            h('span', { className: 'orca-tool-title' }, h('span', { className: 'orca-identity' }, h(ServerIcon), host ?? 'Orca', h('span', { className: 'orca-caption' }, ` · ${t(toolKey(toolName))}`)),
              h('strong', { className: 'orca-mono', title }, title), path && path !== title ? h('div', { className: 'orca-tool-path orca-mono', title: path }, path) : null)),
          h(Badge, { text: status, tone: errorStates.has(state) ? 'error' : warningStates.has(state) ? 'warn' : '', spin: !done })),
        open ? h('div', { className: 'orca-tool-body' },
          h('div', { className: 'orca-toolbar' }, h('div', { className: 'orca-tool-actions' }, done ? h(Copy, { text, t }) : null,
            done ? h(Button, { className: 'quiet', onClick: () => setFull(!full) }, t(full ? 'boundedOutput' : 'fullOutput')) : null,
            inspect ? h(Button, { className: 'quiet', onClick: inspect }, t('inspect')) : null)),
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
            guide: [{ id: 'orca-fleet', order: 35, title: () => t('fleet'), description: () => t('guide'), icon: ServerIcon }] }))
          scope.slots.inject('sidebar.right.pane.tab', () => scope.slots.register({ name: 'sidebar.right.pane.tab', key: 'dsh-orca', locale: 'orca' }, props => h(FleetPanel, { ...props, store })))
        })
      },
    }
  },
})
