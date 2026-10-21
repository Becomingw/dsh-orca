/** Authenticated owner-only routes, registered behind DSH connection admission. */
import { ACTIONS } from './policy.js'

const hostPattern = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/
const sessionPattern = /^[A-Za-z0-9_-]{1,80}$/
const json = (body, status = 200) => Response.json(body, { status, headers: { 'cache-control': 'no-store' } })
const fail = (message, status = 400) => { throw Object.assign(new Error(message), { status }) }

export function registerRoutes(ctx, { cache, config, selected, tools }) {
  const connection = ctx.get('connection')
  if (!connection?.fetch?.register) return
  const pendingStops = new Map()
  const execute = (name, args, signal) => tools.get(name).execute(args, { signal })
  const host = value => {
    if (typeof value !== 'string' || !hostPattern.test(value)) fail('Invalid host alias')
    if (config.hosts.length && !config.hosts.includes(value)) fail('Host is not allowed', 403)
    return value
  }
  const session = value => {
    if (typeof value !== 'string' || !sessionPattern.test(value)) fail('Invalid session name')
    return value
  }
  const handlers = {
    async summary(body) {
      if (body.refresh !== undefined && typeof body.refresh !== 'boolean') fail('refresh must be boolean')
      if (body.refresh) await cache.refresh()
      return { ...cache.snapshot(), config: { hosts: config.hosts, panelHosts: selected.hosts,
        discovered: selected.discovered, pollSeconds: config.panel.pollSeconds, jobLimit: config.panel.jobLimit,
        allowedCommands: config.policy.allowedCommands, trustedWorkdirs: config.policy.trustedWorkdirs,
        askOnce: config.policy.askOnce, alwaysAsk: config.policy.alwaysAsk, grantTtlDays: config.policy.grantTtlDays } }
    },
    async 'job/tail'(body, signal) {
      const lines = body.lines ?? 50
      if (!Number.isInteger(lines) || lines < 1 || lines > 200) fail('lines must be 1..200')
      const data = await execute('orca_status', { host: host(body.host), session: session(body.session), job_limit: 100, log_tail_lines: lines }, signal)
      const job = data.jobs?.find(j => j.session === body.session && (!body.jobId || j.job_id === body.jobId))
      if (!job) fail('Job record not found; refresh the fleet', 404)
      return { log_tail: job.log_tail, state: job.state, exit_code: job.exit_code, jobId: job.job_id }
    },
    async 'job/stop'(body, signal) {
      const args = { host: host(body.host), session: session(body.session), grace_seconds: body.grace_seconds ?? 10 }
      if (!Number.isInteger(args.grace_seconds) || args.grace_seconds < 0 || args.grace_seconds > 60) fail('grace_seconds must be 0..60')
      if (typeof body.jobId !== 'string' || !body.jobId || body.jobId.length > 200) fail('jobId is required')
      const key = JSON.stringify([args.host, args.session])
      if (pendingStops.has(key)) fail('A stop is already in progress', 409)
      const task = (async () => {
        // Recheck identity: never act on an unrelated reused session from a stale UI row.
        const status = await execute('orca_status', { host: args.host, session: args.session, job_limit: 100, log_tail_lines: 0 }, signal)
        const active = status.jobs?.find(j => j.session === args.session && j.active)
        if (!active || active.job_id !== body.jobId) fail('Job changed or already ended; refresh before stopping', 409)
        const result = await execute('orca_stop', args, signal)
        if (result.was_running && !result.session_stopped) fail('The session is still running; inspect the job before retrying', 409)
        await cache.pollHost(args.host)
        return result
      })()
      pendingStops.set(key, task)
      try { return await task } finally { pendingStops.delete(key) }
    },
    async 'grants/list'(_body, signal) { return execute('orca_grants', { action: 'list' }, signal) },
    async 'grants/revoke'(body, signal) {
      // UI revocation always targets one exact grant; the tool retains bulk revoke.
      if (typeof body.host !== 'string' || !hostPattern.test(body.host)) fail('Invalid host alias')
      const target = { action: 'revoke', host: body.host, workdir: body.workdir, tool: body.action }
      if (typeof target.workdir !== 'string' || !target.workdir.startsWith('/')) fail('Absolute workdir required')
      if (!Object.values(ACTIONS).includes(target.tool)) fail('Invalid grant action')
      // GrantStore supports configured action tiers; avoid constraining it to UI labels.
      return execute('orca_grants', target, signal)
    },
  }
  const lifetime = new AbortController()
  ctx.effect(() => {
    cache.start()
    return () => { lifetime.abort(); cache.stop() }
  })
  for (const [path, handle] of Object.entries(handlers)) {
    ctx.effect(() => connection.fetch.register({
      path: `/api/dsh-orca/${path}`, methods: ['POST'], requestBody: 'buffered',
      async fetch(request) {
        try {
          const raw = await request.text()
          if (raw.length > 16384) return json({ error: 'Request too large' }, 413)
          let body
          try { body = JSON.parse(raw || '{}') } catch { return json({ error: 'Invalid JSON' }, 400) }
          if (!body || typeof body !== 'object' || Array.isArray(body)) return json({ error: 'Object required' }, 400)
          return json(await handle(body, AbortSignal.any([request.signal, lifetime.signal])))
        } catch (error) { return json({ error: error.message ?? String(error) }, error.status ?? 500) }
      },
    }))
  }
}
