import assert from 'node:assert/strict'
import { test } from 'node:test'
import { FleetCache, summarizeHost } from '../lib/cache.js'
import { createTools, RENDERERS } from '../lib/tools.js'
import { presentationMeta } from '../lib/view-models.js'
import { registerRoutes } from '../lib/routes.js'
import { preflightPatch } from '../lib/approval.js'
import { Observations } from '../lib/observations.js'

const result = (name, args, value) => {
  const content = RENDERERS[name](value)
  return { meta: presentationMeta(name, args, value, content), text: content[0].text }
}

test('presentation offsets preserve unicode, repeated stderr and multiline content', () => {
  const { meta, text } = result('orca_run', { host: '13', command: 'echo ok' }, { stdout: '🙂\n[stderr]\n同一行', stderr: '同一行', exit_code: 1 })
  assert.equal(text.slice(...meta.stdout), '🙂\n[stderr]\n同一行')
  assert.equal(text.slice(...meta.stderr), '同一行')
  assert.equal(meta.exit_code, 1)
  const read = result('orca_read', { host: '13', path: '/a' }, { content: 'a\nb', first_line: 10, last_line: 11, content_truncated: true })
  assert.equal(read.text.slice(...read.meta.body), 'a\nb')
  assert.equal(read.meta.truncated, true)
})

test('grep offsets separate matches and context without parsing paths or regex in the client', () => {
  const { meta, text } = result('orca_grep', { pattern: 'x' }, { matches: [{ path: '/x:12-🙂', line: 3, text: 'x\t<unsafe>', before: ['before'], after: ['after'] }], files_matched: 1 })
  assert.deepEqual(meta.rows.map(row => [row.line, row.match, text.slice(...row.body)]), [[2, false, 'before'], [3, true, 'x\t<unsafe>'], [4, false, 'after']])
})

test('all ten tools expose versioned JSON presentation with exceptional patch states', () => {
  const tools = createTools(async () => ({}), { grants: { active: () => [], revoke: () => [] } })
  assert.equal(tools.length, 10)
  for (const tool of tools) {
    const meta = tool.output.presentationMeta({}, {})
    assert.equal(meta.tool, tool.name)
    assert.equal(meta.version, 1)
    assert.deepEqual(JSON.parse(JSON.stringify(meta)), meta)
  }
  for (const state of ['partial', 'restored', 'conflict', 'not_applied']) {
    const { meta, text } = result('orca_patch', {}, { state, diff: '@@\n-🙂\n+x', conflicts: [{ path: 'a', message: 'bad' }] })
    assert.equal(meta.state, state)
    assert.equal(text.slice(...meta.body), '@@\n-🙂\n+x')
  }
})

test('cache retains the last successful snapshot on failure and never claims truncated jobs are complete', async () => {
  let fail = false, calls = 0
  const cache = new FleetCache({ hosts: ['13'], pollSeconds: 0, jobLimit: 5, call: async (_name, args) => {
    calls++; assert.equal(args.log_tail_lines, 1)
    if (fail) throw new Error('SSH unavailable')
    return { hostname: 'gpu13', registry_records_total: 10, jobs: [{ session: 'old', active: true, job_id: 'j' }] }
  } })
  assert.equal(cache.snapshot().hosts[0].at, null)
  await Promise.all([cache.refresh(), cache.refresh()]); assert.equal(calls, 1)
  const before = cache.snapshot()
  assert.equal(before.jobsComplete, false)
  assert.equal(before.hosts[0].running, 1)
  fail = true; await cache.refresh()
  const after = cache.snapshot()
  assert.equal(after.hosts[0].at, before.hosts[0].at)
  assert.equal(after.hosts[0].hostname, 'gpu13')
  assert.equal(after.jobs[0].stale, true)
  assert.equal(after.hosts[0].ok, false)
})

test('cache does not truncate running rows at 25 and carries per-GPU values', async () => {
  const cache = new FleetCache({ hosts: ['13'], pollSeconds: 0, jobLimit: 50, call: async () => ({ registry_records_total: 30, jobs: Array.from({ length: 30 }, (_, i) => ({ session: `j${i}`, active: true })) }) })
  await cache.refresh(); assert.equal(cache.snapshot().jobs.length, 30)
  assert.equal(cache.snapshot().jobsComplete, true)
  assert.equal(summarizeHost({ gpus: [{ index: 2, utilization_percent: 42, memory_used_mib: 128, memory_total_mib: 1024 }] }).gpu.cards[0].util, 42)
})

function routesFixture() {
  const routes = new Map(), effects = [], calls = []
  let active = 'job-1', stopping
  const tools = new Map([
    ['orca_status', { execute: async args => { calls.push(['status', args]); return { jobs: [{ session: 'exp', job_id: active, active: true, log_tail: 'epoch 2', state: 'running' }] } } }],
    ['orca_stop', { execute: async args => { calls.push(['stop', args]); if (stopping) await stopping; return { was_running: true, session_stopped: true } } }],
    ['orca_grants', { execute: async args => { calls.push(['grants', args]); return { revoked: [] } } }],
  ])
  const cache = { start() {}, stop() {}, snapshot: () => ({ hosts: [], jobs: [] }), refresh: async () => calls.push(['refresh']), pollHost: async host => calls.push(['probe', host]) }
  const config = { hosts: ['13'], panel: { pollSeconds: 0, jobLimit: 5 }, policy: { allowedCommands: [], trustedWorkdirs: [], askOnce: ['run'], alwaysAsk: [], grantTtlDays: 7 } }
  const ctx = { get: () => ({ fetch: { register: route => { routes.set(route.path, route); return () => routes.delete(route.path) } } }), effect: fn => effects.push(fn()) }
  registerRoutes(ctx, { cache, config, selected: { hosts: ['13'], discovered: false }, tools })
  return { routes, calls, setActive: v => { active = v }, setStopping: v => { stopping = v }, close: () => effects.reverse().forEach(fn => fn?.()),
    request: async (path, body) => routes.get(`/api/dsh-orca/${path}`).fetch(new Request(`http://localhost/api/dsh-orca/${path}`, { method: 'POST', body: typeof body === 'string' ? body : JSON.stringify(body) })) }
}

test('routes use only authenticated connection registrations and validate before remote calls', async () => {
  const f = routesFixture()
  try {
    assert.equal(f.routes.size, 5)
    assert.equal(f.routes.has('/dsh-orca/summary'), false)
    for (const route of f.routes.values()) assert.deepEqual(route.methods, ['POST'])
    assert.equal((await f.request('summary', {})).status, 200)
    assert.equal(f.calls.length, 0)
    assert.equal((await f.request('summary', '{')).status, 400)
    assert.equal((await f.request('job/tail', { host: 'evil', session: 'exp' })).status, 403)
    assert.equal((await f.request('job/tail', { host: '13', session: 'x; rm' })).status, 400)
    assert.equal((await f.request('job/tail', { host: '13', session: 'exp', lines: 201 })).status, 400)
    assert.equal(f.calls.length, 0)
    assert.equal((await f.request('job/tail', { host: '13', session: 'exp', jobId: 'job-1' })).status, 200)
    assert.equal((await f.request('grants/revoke', {})).status, 400)
    assert.equal((await f.request('grants/revoke', { host: 'old-host', workdir: '/old', action: 'run' })).status, 200)
  } finally { f.close() }
  assert.equal(f.routes.size, 0)
})

test('UI stop uses the same tool handler and rejects stale identities and duplicate actions', async () => {
  const f = routesFixture()
  try {
    const body = { host: '13', session: 'exp', jobId: 'job-1' }
    f.setActive('job-2')
    assert.equal((await f.request('job/stop', body)).status, 409)
    assert.equal(f.calls.filter(([op]) => op === 'stop').length, 0)
    f.setActive('job-1')
    let release
    f.setStopping(new Promise(resolve => { release = resolve }))
    const first = f.request('job/stop', body)
    await new Promise(resolve => setImmediate(resolve))
    assert.equal((await f.request('job/stop', body)).status, 409)
    release()
    assert.equal((await first).status, 200)
    assert.equal(f.calls.filter(([op]) => op === 'stop').length, 1)
    assert.deepEqual(f.calls.at(-1), ['probe', '13'])
  } finally { f.close() }
})

test('patch preflight preserves last-read hashes and maps conflict, failure and cancellation', async () => {
  const observations = new Observations(); observations.record('13', '/p/a', '0'.repeat(64))
  const exec = { arguments: { host: '13', workdir: '/p', patch: '*** Begin Patch\n*** Update File: a\n@@\n-a\n+b\n*** End Patch' }, signal: new AbortController().signal }
  const decision = { kind: 'ask', displayReason: { en: 'Apply', zh: '应用' } }
  const denied = await preflightPatch(async (_name, args) => {
    assert.equal(args.expected_sha256.a, '0'.repeat(64)); assert.equal(args.dry_run, true)
    return { state: 'conflict', conflicts: [{ path: 'a', message: 'changed' }] }
  }, exec, decision, observations)
  assert.equal(denied.kind, 'deny'); assert.equal(denied.info.code, 'ORCA_DENIED')
  assert.equal(observations.get('13', '/p/a'), '0'.repeat(64))
  const passed = await preflightPatch(async () => ({ state: 'clean', files: [{ path: 'a', added: 2, removed: 1 }] }), exec, decision, observations)
  assert.match(passed.displayReason.zh, /a \+2 −1/)
  const failed = await preflightPatch(async () => { throw new Error('SSH timeout') }, exec, decision, observations)
  assert.equal(failed.kind, 'ask'); assert.match(failed.displayReason.zh, /未能预检/)
  const cancelled = await preflightPatch(async () => { throw new Error('cancel') }, { ...exec, signal: AbortSignal.abort() }, decision, observations)
  assert.equal(cancelled.kind, 'cancel')
})
