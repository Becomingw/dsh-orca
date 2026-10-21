/**
 * Orca panel: cached fleet status, recent jobs, and a read-only view of the
 * effective policy. Everything comes from the host's `/dsh-orca/summary`
 * snapshot — opening the panel never talks to a remote host.
 *
 * Registered as a Settings section (`settings.section`), the slot the shell
 * projects with id/order/label.
 */
window.__ModuleLoader__.load({
  id: 'dsh-orca',
  factory(require) {
    const React = require('react')
    const h = React.createElement
    const { useCallback, useEffect, useState } = React

    const CSS = `
      .orca-panel { display: flex; flex-direction: column; gap: 18px; padding: 2px 0 8px; }
      .orca-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
      .orca-head h3 { margin: 0; font-size: 15px; font-weight: 600; }
      .orca-muted { color: color-mix(in srgb, currentColor 55%, transparent); font-size: 12px; }
      .orca-spacer { flex: 1 1 auto; }
      .orca-btn { border: 1px solid color-mix(in srgb, currentColor 22%, transparent);
        background: transparent; color: inherit; border-radius: 8px; padding: 4px 10px;
        font-size: 12px; cursor: pointer; }
      .orca-btn:hover:not(:disabled) { background: color-mix(in srgb, currentColor 7%, transparent); }
      .orca-btn:disabled { opacity: .5; cursor: default; }
      .orca-grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(268px, 1fr)); }
      .orca-card { border: 1px solid color-mix(in srgb, currentColor 16%, transparent);
        border-radius: 12px; padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
      .orca-card-top { display: flex; align-items: center; gap: 8px; }
      .orca-name { font-weight: 600; font-size: 13px; }
      .orca-host { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; opacity: .6; }
      .orca-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
      .orca-dot.ok { background: #35b37e; }
      .orca-dot.busy { background: #d9a13a; }
      .orca-dot.bad { background: #d0563f; }
      .orca-rows { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
      .orca-row { display: flex; gap: 8px; justify-content: space-between; }
      .orca-row > span:first-child { opacity: .6; }
      .orca-num { font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
      .orca-err { color: #d0563f; font-size: 11px; word-break: break-word; }
      .orca-table { width: 100%; border-collapse: collapse; font-size: 12px; }
      .orca-table th { text-align: left; font-weight: 500; opacity: .6; padding: 4px 8px;
        border-bottom: 1px solid color-mix(in srgb, currentColor 16%, transparent); }
      .orca-table td { padding: 4px 8px; border-bottom: 1px solid color-mix(in srgb, currentColor 8%, transparent); }
      .orca-state { font-size: 11px; padding: 1px 6px; border-radius: 999px;
        border: 1px solid color-mix(in srgb, currentColor 22%, transparent); }
      .orca-state.running { color: #d9a13a; border-color: #d9a13a66; }
      .orca-state.succeeded { color: #35b37e; border-color: #35b37e66; }
      .orca-state.failed, .orca-state.stopped { color: #d0563f; border-color: #d0563f66; }
      .orca-chips { display: flex; flex-wrap: wrap; gap: 6px; }
      .orca-chip { font-size: 11px; padding: 2px 8px; border-radius: 999px;
        border: 1px solid color-mix(in srgb, currentColor 18%, transparent);
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
      .orca-section-title { font-size: 12px; font-weight: 600; opacity: .75; margin: 2px 0 6px; }
      .orca-empty { font-size: 12px; opacity: .6; padding: 10px 0; }
    `

    function fmtBytes(bytes) {
      if (typeof bytes !== 'number' || !Number.isFinite(bytes)) return '—'
      const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']
      let value = bytes
      let unit = 0
      while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1 }
      return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`
    }

    function fmtAgo(ts) {
      if (!ts) return '从未'
      const seconds = Math.max(0, Math.round((Date.now() - ts) / 1000))
      if (seconds < 60) return `${seconds} 秒前`
      if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`
      return `${Math.round(seconds / 3600)} 小时前`
    }

    function fmtWhen(iso) {
      if (!iso) return '—'
      const date = new Date(iso)
      if (Number.isNaN(date.getTime())) return '—'
      return date.toLocaleString(undefined, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    }

    function dotClass(host) {
      if (!host.ok || !host.at) return 'bad'
      return host.running > 0 ? 'busy' : 'ok'
    }

    function HostCard({ host }) {
      const gpu = host.gpu
      const disk = host.disk
      const mem = host.memory
      return h('div', { className: 'orca-card' }, [
        h('div', { className: 'orca-card-top', key: 'top' }, [
          h('span', { className: `orca-dot ${dotClass(host)}`, key: 'd' }),
          h('span', { className: 'orca-name', key: 'n' }, host.hostname ?? host.host),
          h('span', { className: 'orca-host', key: 'h' }, host.host),
          h('span', { className: 'orca-spacer', key: 's' }),
          host.running > 0
            ? h('span', { className: 'orca-muted', key: 'r' }, `${host.running} 个任务在跑`)
            : null,
        ]),
        host.ok
          ? h('div', { className: 'orca-rows', key: 'rows' }, [
              host.load
                ? h('div', { className: 'orca-row', key: 'load' }, [
                    h('span', { key: 'l' }, '负载'),
                    h('span', { className: 'orca-num', key: 'v' }, host.load.map((n) => Number(n).toFixed(1)).join(' / ')),
                  ])
                : null,
              gpu
                ? h('div', { className: 'orca-row', key: 'gpu' }, [
                    h('span', { key: 'l' }, 'GPU'),
                    h('span', { className: 'orca-num', key: 'v' },
                      `${gpu.count} 张 · 平均 ${gpu.utilAvg ?? 0}% · 显存 ${fmtBytes(gpu.memUsedMib * 1024 * 1024)}`),
                  ])
                : null,
              disk
                ? h('div', { className: 'orca-row', key: 'disk' }, [
                    h('span', { key: 'l' }, '磁盘可用'),
                    h('span', { className: 'orca-num', key: 'v' },
                      `${fmtBytes(disk.free_bytes)} / ${fmtBytes(disk.total_bytes)}`),
                  ])
                : null,
              mem
                ? h('div', { className: 'orca-row', key: 'mem' }, [
                    h('span', { key: 'l' }, '内存可用'),
                    h('span', { className: 'orca-num', key: 'v' },
                      `${fmtBytes(mem.available_bytes)} / ${fmtBytes(mem.total_bytes)}`),
                  ])
                : null,
              h('div', { className: 'orca-row', key: 'at' }, [
                h('span', { key: 'l' }, '同步于'),
                h('span', { key: 'v' }, fmtAgo(host.at)),
              ]),
            ])
          : h('div', { className: 'orca-err', key: 'err' }, host.error ?? '尚无数据'),
      ])
    }

    function JobRows({ jobs }) {
      if (!jobs || jobs.length === 0) return h('div', { className: 'orca-empty' }, '还没有任务记录')
      return h('table', { className: 'orca-table' },
        h('thead', null, h('tr', null, [
          h('th', { key: 'h' }, '主机'),
          h('th', { key: 's' }, '任务'),
          h('th', { key: 'st' }, '状态'),
          h('th', { key: 'b' }, '开始'),
          h('th', { key: 'e' }, '结束'),
          h('th', { key: 'x' }, '退出码'),
        ])),
        h('tbody', null, jobs.map((job, index) => h('tr', { key: `${job.host}-${job.session}-${index}` }, [
          h('td', { key: 'h', className: 'orca-host' }, job.host),
          h('td', { key: 's' }, job.session),
          h('td', { key: 'st' }, h('span', { className: `orca-state ${job.state}` }, job.state)),
          h('td', { key: 'b' }, fmtWhen(job.startedAt)),
          h('td', { key: 'e' }, fmtWhen(job.finishedAt)),
          h('td', { key: 'x', className: 'orca-num' }, job.exitCode ?? '—'),
        ]))),
      )
    }

    function ConfigView({ config }) {
      if (!config) return null
      const commands = config.allowedCommands ?? []
      const workdirs = config.trustedWorkdirs ?? []
      return h('div', null, [
        h('div', { className: 'orca-section-title', key: 't' }, '当前配置'),
        h('div', { className: 'orca-rows', key: 'r' }, [
          h('div', { className: 'orca-row', key: 'p' }, [
            h('span', { key: 'l' }, '轮询间隔'),
            h('span', { className: 'orca-num', key: 'v' },
              config.pollSeconds > 0 ? `${config.pollSeconds} 秒` : '关闭（仅手动刷新）'),
          ]),
          h('div', { className: 'orca-row', key: 'h' }, [
            h('span', { key: 'l' }, '监控主机'),
            h('span', { key: 'v' }, `${(config.hosts ?? []).length} 台`),
          ]),
        ]),
        h('div', { className: 'orca-section-title', key: 'w', style: { marginTop: 10 } }, '免询问白名单'),
        commands.length > 0
          ? h('div', { className: 'orca-chips', key: 'c' }, commands.map((item) => h('span', { className: 'orca-chip', key: item }, item)))
          : h('div', { className: 'orca-empty', key: 'c0' }, '未配置：所有写操作都需要审批'),
        h('div', { className: 'orca-section-title', key: 't2', style: { marginTop: 10 } }, '信任目录'),
        workdirs.length > 0
          ? h('div', { className: 'orca-chips', key: 'wd' }, workdirs.map((item) => h('span', { className: 'orca-chip', key: item }, item)))
          : h('div', { className: 'orca-empty', key: 'wd0' }, '未配置：任何目录都按配置分级处理'),
        h('div', { className: 'orca-muted', key: 'hint', style: { marginTop: 10 } },
          '修改配置请在 ~/.dsh/profiles/desktop/cordis.patch.yml 里的 - id: orca 下编辑 policy / panel，保存后插件会重新组合。'),
      ])
    }

    function Panel() {
      const [data, setData] = useState(null)
      const [error, setError] = useState(null)
      const [busy, setBusy] = useState(false)

      const load = useCallback(async (refresh) => {
        setBusy(true)
        try {
          const response = await fetch(`/dsh-orca/summary${refresh ? '?refresh=1' : ''}`, {
            credentials: 'same-origin',
            cache: 'no-store',
          })
          if (!response.ok) throw new Error(`HTTP ${response.status}`)
          setData(await response.json())
          setError(null)
        } catch (cause) {
          setError(cause instanceof Error ? cause.message : String(cause))
        } finally {
          setBusy(false)
        }
      }, [])

      useEffect(() => {
        void load(false)
        const timer = setInterval(() => { void load(false) }, 15000)
        return () => clearInterval(timer)
      }, [load])

      const hosts = data?.hosts ?? []
      const jobs = data?.jobs ?? []
      const online = hosts.filter((host) => host.ok).length
      const running = hosts.reduce((sum, host) => sum + (host.running ?? 0), 0)

      return h('div', { className: 'orca-panel' }, [
        h('style', { key: 'css' }, CSS),
        h('div', { className: 'orca-head', key: 'head' }, [
          h('h3', { key: 't' }, 'Orca 机群'),
          h('span', { className: 'orca-muted', key: 'm' },
            data ? `${online}/${hosts.length} 台在线 · ${running} 个任务运行中 · 缓存于 ${fmtAgo(data.lastRoundAt)}` : '读取中…'),
          h('span', { className: 'orca-spacer', key: 's' }),
          h('button', {
            key: 'b',
            className: 'orca-btn',
            disabled: busy,
            onClick: () => { void load(true) },
          }, busy ? '刷新中…' : '刷新'),
        ]),
        error ? h('div', { className: 'orca-err', key: 'e' }, `面板接口不可用：${error}`) : null,
        hosts.length > 0
          ? h('div', { className: 'orca-grid', key: 'grid' }, hosts.map((host) => h(HostCard, { host, key: host.host })))
          : h('div', { className: 'orca-empty', key: 'none' }, '还没有配置主机：在 cordis.patch.yml 的 - id: orca 下设置 hosts。'),
        h('div', { key: 'jobs' }, [
          h('div', { className: 'orca-section-title', key: 't' }, '最近任务'),
          h(JobRows, { jobs, key: 'rows' }),
        ]),
        h('div', { key: 'cfg' }, h(ConfigView, { config: data?.config })),
      ])
    }

    return {
      inject: ['slots'],
      apply(ctx) {
        ctx.slots.inject('settings.section', () => ctx.slots.register({
          name: 'settings.section',
          id: 'orca',
          order: 120,
          label: () => 'Orca 机群',
        }, Panel))
      },
    }
  },
})
