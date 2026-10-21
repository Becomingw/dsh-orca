import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { after, before, test } from 'node:test'
import { setTimeout as sleep } from 'node:timers/promises'
import { apply, normalizeConfig } from '../index.js'
import { callEngine, EngineError } from '../lib/engine.js'
import { fakeContext, fakeFleet } from './helpers.mjs'

let fleet
before(() => {
  fleet = fakeFleet()
})
after(() => {
  try {
    execFileSync('tmux', ['kill-server'], { env: fleet.env, stdio: 'ignore' })
  } catch {}
  fleet.cleanup()
})

const engine = (tool, args, options = {}) => callEngine(tool, args, { env: fleet.env, ...options })
const grantsPath = () => join(fleet.root, 'grants.json')
const ALL_TOOLS = ['orca_grants', 'orca_grep', 'orca_launch', 'orca_ls', 'orca_patch', 'orca_read', 'orca_rollback', 'orca_run', 'orca_status', 'orca_stop']

// -- plugin wiring ---------------------------------------------------------------

test('apply registers every tool; only read-only ones are concurrency-safe', () => {
  const ctx = fakeContext()
  apply(ctx, { grantsPath: grantsPath() })
  assert.deepEqual([...ctx.registered.keys()].sort(), ALL_TOOLS)
  for (const name of ['orca_run', 'orca_launch', 'orca_stop', 'orca_patch', 'orca_rollback']) {
    assert.equal(ctx.registered.get(name).isConcurrencySafe, undefined, name)
  }
  for (const name of ['orca_status', 'orca_read', 'orca_grep', 'orca_ls']) {
    assert.equal(ctx.registered.get(name).isConcurrencySafe(), true, name)
  }
})

test('askOnce: approving once remembers host+workdir+action', async () => {
  const path = join(fleet.root, 'grants-askonce.json')
  const ctx = fakeContext({ outcome: 'allowed-once' })
  apply(ctx, { grantsPath: path })
  const exec = { name: 'orca_run', arguments: { host: '13', workdir: '/w', command: 'make' } }
  assert.deepEqual(await ctx.preExecute(exec), { kind: 'allow' })
  assert.equal(ctx.requests.length, 1)
  assert.match(ctx.requests[0].displayReason.zh, /make/)
  assert.deepEqual(await ctx.preExecute(exec), { kind: 'allow' })
  assert.equal(ctx.requests.length, 1, 'second call must not ask')
  // Another workdir still asks.
  await ctx.preExecute({ ...exec, arguments: { ...exec.arguments, workdir: '/other' } })
  assert.equal(ctx.requests.length, 2)
  // Revoking through the tool makes it ask again.
  const revoked = await ctx.registered.get('orca_grants').execute({ action: 'revoke', host: '13' }, {})
  assert.equal(revoked.revoked.length, 2)
  await ctx.preExecute(exec)
  assert.equal(ctx.requests.length, 3)
})

test('askOnce: a rejection is a deny and is not remembered', async () => {
  const path = join(fleet.root, 'grants-reject.json')
  const ctx = fakeContext({ outcome: 'rejected' })
  apply(ctx, { grantsPath: path })
  const exec = { name: 'orca_launch', arguments: { host: '13', workdir: '/w', session: 's', command: 'x', log_path: '/w/x.log' } }
  assert.equal((await ctx.preExecute(exec)).kind, 'deny')
  assert.equal(existsSync(path), false)
})

test('alwaysAsk tools hand an ask to the pipeline; read-only and foreign tools pass', async () => {
  const ctx = fakeContext()
  apply(ctx, { grantsPath: grantsPath() })
  const stop = await ctx.preExecute({ name: 'orca_stop', arguments: { host: '13', session: 'exp' } })
  assert.equal(stop.kind, 'ask')
  assert.match(stop.displayReason.zh, /exp/)
  assert.deepEqual(await ctx.preExecute({ name: 'orca_status', arguments: { host: '13' } }), { kind: 'allow' })
  assert.deepEqual(await ctx.preExecute({ name: 'bash', arguments: { command: 'sudo x' } }), { kind: 'allow' })
  assert.equal(ctx.requests.length, 0)
})

test('guard denies default-denied commands for run and launch even if something allowed them', () => {
  const ctx = fakeContext({ approvalPolicy: 'never' })
  apply(ctx, { grantsPath: grantsPath() })
  const [guard] = ctx.guards
  assert.match(guard({ name: 'orca_run', arguments: { command: 'sudo rm x' } }), /deniedCommands/)
  assert.match(guard({ name: 'orca_launch', arguments: { command: 'reboot now' } }), /deniedCommands/)
  assert.equal(guard({ name: 'orca_run', arguments: { command: 'make' } }), undefined)
  assert.equal(guard({ name: 'bash', arguments: { command: 'sudo rm x' } }), undefined)
})

test('approval "never" denies a write before it reaches the engine and never asks', async () => {
  const ctx = fakeContext({ approvalPolicy: 'never' })
  apply(ctx, { grantsPath: grantsPath() })
  const decision = await ctx.preExecute({
    name: 'orca_run',
    arguments: { host: '13', workdir: '/data/p', command: 'python train.py' },
  })
  assert.equal(decision.kind, 'deny')
  assert.match(decision.reason, /approval policy is "never"/)
  assert.equal(ctx.requests.length, 0)
  // Read-only tools still pass through in headless mode.
  assert.deepEqual(await ctx.preExecute({ name: 'orca_status', arguments: { host: '13' } }), { kind: 'allow' })
})

test('config validation', () => {
  assert.throws(() => normalizeConfig({ hosts: ['-oProxyCommand=x'] }), /invalid host/)
  assert.throws(() => normalizeConfig({ policy: { allowedCommands: 'ls' } }), /list of strings/)
  assert.throws(() => normalizeConfig({ policy: { askOnce: ['deploy'] } }), /unknown action/)
  assert.throws(() => normalizeConfig({ policy: { grantTtlDays: 0 } }), /grantTtlDays/)
  assert.equal(normalizeConfig({ logging: { structured: false } }).logging, null)
  assert.deepEqual(normalizeConfig({}).logging, { tail_lines: 2000, rotate_bytes: 32 << 20, keep_segments: 5 })
  assert.deepEqual(normalizeConfig({}).hosts, [])
})

// -- engine bridge, end to end through a fake ssh ------------------------------------

test('orca_run executes remotely and reports the exit code', async () => {
  const workdir = join(fleet.home, 'proj')
  mkdirSync(workdir, { recursive: true })
  const value = await engine('remote_run', { host: 'fake', workdir, command: 'echo hello; echo oops >&2; exit 3' })
  assert.equal(value.exit_code, 3)
  assert.equal(value.stdout, 'hello\n')
  assert.equal(value.stderr, 'oops\n')
})

test('orca_read pages a remote file', async () => {
  const path = join(fleet.home, 'notes.txt')
  writeFileSync(path, 'one\ntwo\nthree\n')
  const value = await engine('remote_read_file', { host: 'fake', path, start_line: 2, end_line: 2 })
  assert.equal(value.content, 'two\n')
  assert.equal(value.next_start_line, 3)
})

test('orca_status reports host facts', async () => {
  const value = await engine('remote_status', { host: 'fake' })
  assert.equal(value.host, 'fake')
  assert.ok(value.hostname)
})

test('engine validation errors become EngineError TOOL', async () => {
  await assert.rejects(
    engine('remote_status', { host: 'not-allowed' }),
    (error) => error instanceof EngineError && error.code === 'TOOL' && /not allowed/.test(error.message),
  )
})

test('abort kills the worker and its remote command promptly', async () => {
  const controller = new AbortController()
  const started = Date.now()
  const pending = engine(
    'remote_run',
    { host: 'fake', workdir: fleet.home, command: 'sleep 30', timeout_seconds: 60 },
    { signal: controller.signal },
  )
  setTimeout(() => controller.abort(new Error('user cancelled')), 300)
  await assert.rejects(pending, /user cancelled/)
  assert.ok(Date.now() - started < 5000)
})

test('missing python is a clear spawn error', async () => {
  await assert.rejects(engine('remote_status', { host: 'fake' }, { python: '/nonexistent/python3' }), (error) => error.code === 'SPAWN')
})

test('launch → status (ring tail) → stop, with structured logging', async () => {
  const workdir = join(fleet.home, 'train')
  mkdirSync(workdir, { recursive: true })
  const logPath = join(workdir, 'logs', 'exp1.log')
  const orcaLog = normalizeConfig({}).logging
  const launched = await engine('remote_launch_tmux', {
    host: 'fake', workdir, session: 'exp1', log_path: logPath, orca_log: orcaLog,
    command: "printf 'step 1\\rstep 2\\rstep 3\\n'; echo ready; sleep 300",
  })
  assert.equal(launched.orca_dir, `${logPath}.orca`)
  let job
  for (let i = 0; i < 50; i++) {
    const status = await engine('remote_status', { host: 'fake', session: 'exp1' })
    job = status.jobs[0]
    if (job?.log_tail?.includes('ready')) break
    await sleep(200)
  }
  assert.equal(job.state, 'running')
  assert.equal(job.log_tail_source, 'ring')
  assert.match(job.log_tail, /^step 3$/m)
  assert.doesNotMatch(job.log_tail, /step 1/)
  const stopped = await engine('remote_stop_job', { host: 'fake', session: 'exp1', grace_seconds: 2 })
  assert.equal(stopped.session_stopped, true)
  const after = await engine('remote_status', { host: 'fake', session: 'exp1' })
  assert.equal(after.jobs[0].state, 'stopped')
})

test('launch rejects a session name tmux would rewrite', async () => {
  await assert.rejects(
    engine('remote_launch_tmux', { host: 'fake', workdir: fleet.home, session: 'a.b', log_path: join(fleet.home, 'a.log'), command: 'true' }),
    /may not contain/,
  )
})

test('grep and ls run server-side', async () => {
  const dir = join(fleet.home, 'src')
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'model.py'), 'class Net:\n    def forward(self):\n        raise ValueError("x")\n')
  const grep = await engine('orca_grep', { host: 'fake', path: dir, pattern: 'ValueError', context_lines: 1 })
  assert.equal(grep.matches.length, 1)
  assert.equal(grep.matches[0].line, 3)
  const ls = await engine('orca_ls', { host: 'fake', path: fleet.home, depth: 2 })
  assert.ok(ls.entries.some((e) => e.path === 'src/model.py' && e.type === 'f'))
})

test('patch: dry run, atomic apply with backup, rollback', async () => {
  const workdir = join(fleet.home, 'repo')
  mkdirSync(workdir, { recursive: true })
  writeFileSync(join(workdir, 'a.py'), 'x = 1\ny = 2\n')
  const patch = '*** Begin Patch\n*** Update File: a.py\n@@\n x = 1\n-y = 2\n+y = 3\n*** Add File: b.py\n+new\n*** End Patch'

  const dry = await engine('orca_patch', { host: 'fake', workdir, patch, dry_run: true })
  assert.equal(dry.ok, true)
  assert.match(dry.diff, /\+y = 3/)
  assert.equal(readFileSync(join(workdir, 'a.py'), 'utf8'), 'x = 1\ny = 2\n')

  const applied = await engine('orca_patch', { host: 'fake', workdir, patch })
  assert.equal(applied.ok, true)
  assert.equal(readFileSync(join(workdir, 'a.py'), 'utf8'), 'x = 1\ny = 3\n')
  assert.equal(readFileSync(join(workdir, 'b.py'), 'utf8'), 'new\n')

  const rolled = await engine('orca_rollback', { host: 'fake', patch_id: applied.patch_id })
  assert.equal(rolled.ok, true)
  assert.equal(readFileSync(join(workdir, 'a.py'), 'utf8'), 'x = 1\ny = 2\n')
  assert.equal(existsSync(join(workdir, 'b.py')), false)
  const again = await engine('orca_rollback', { host: 'fake', patch_id: applied.patch_id })
  assert.equal(again.state, 'already_rolled_back')
})

test('patch with a stale hunk changes nothing', async () => {
  const workdir = join(fleet.home, 'repo2')
  mkdirSync(workdir, { recursive: true })
  writeFileSync(join(workdir, 'a.py'), 'one\n')
  writeFileSync(join(workdir, 'b.py'), 'two\n')
  const patch = '*** Begin Patch\n*** Update File: a.py\n@@\n-one\n+ONE\n*** Update File: b.py\n@@\n-missing\n+X\n*** End Patch'
  let result
  try {
    result = await engine('orca_patch', { host: 'fake', workdir, patch })
  } catch (error) {
    result = { ok: false, error: error.message }
  }
  assert.equal(result.ok, false)
  assert.equal(readFileSync(join(workdir, 'a.py'), 'utf8'), 'one\n')
})

test('tool execute goes through the configured engine and renders', async () => {
  const saved = { PATH: process.env.PATH, HOME: process.env.HOME }
  process.env.PATH = fleet.env.PATH
  process.env.HOME = fleet.env.HOME
  try {
    const ctx = fakeContext()
    apply(ctx, { hosts: ['fake'], sshPersistSeconds: 0, grantsPath: grantsPath() })
    const run = ctx.registered.get('orca_run')
    const value = await run.execute({ host: 'fake', workdir: fleet.home, command: 'printf ok' }, { signal: new AbortController().signal })
    assert.equal(value.stdout, 'ok')
    assert.match(run.output.render({}, value)[0].text, /\[exit code: 0\]$/)
  } finally {
    process.env.PATH = saved.PATH
    process.env.HOME = saved.HOME
  }
})

test('patch refuses a file changed since orca_read, via automatic hashes', async () => {
  const saved = { PATH: process.env.PATH, HOME: process.env.HOME }
  process.env.PATH = fleet.env.PATH
  process.env.HOME = fleet.env.HOME
  try {
    const workdir = join(fleet.home, 'cas')
    mkdirSync(workdir, { recursive: true })
    writeFileSync(join(workdir, 'cfg.py'), 'lr = 0.1\n')
    const ctx = fakeContext()
    apply(ctx, { hosts: ['fake'], sshPersistSeconds: 0, grantsPath: grantsPath() })
    const run = (name, args) => ctx.registered.get(name).execute(args, { signal: new AbortController().signal })
    const read = await run('orca_read', { host: 'fake', path: join(workdir, 'cfg.py') })
    assert.match(read.sha256, /^[0-9a-f]{64}$/)
    // Someone edits the file behind the model's back.
    writeFileSync(join(workdir, 'cfg.py'), 'lr = 0.1\nepochs = 99\n')
    const patch = '*** Begin Patch\n*** Update File: cfg.py\n@@\n-lr = 0.1\n+lr = 0.2\n*** End Patch'
    const refused = await run('orca_patch', { host: 'fake', workdir, patch })
    assert.equal(refused.state, 'conflict')
    assert.match(refused.conflicts[0].message, /changed since it was last read/)
    assert.deepEqual(refused.checked_against_last_read, ['cfg.py'])
    assert.equal(readFileSync(join(workdir, 'cfg.py'), 'utf8'), 'lr = 0.1\nepochs = 99\n')
    // After re-reading, the same patch goes through, and our own write becomes the new baseline.
    await run('orca_read', { host: 'fake', path: join(workdir, 'cfg.py') })
    const ok = await run('orca_patch', { host: 'fake', workdir, patch })
    assert.equal(ok.state, 'applied')
    const next = await run('orca_patch', { host: 'fake', workdir, patch: '*** Begin Patch\n*** Update File: cfg.py\n@@\n-lr = 0.2\n+lr = 0.3\n*** End Patch' })
    assert.equal(next.state, 'applied')
    assert.match(readFileSync(join(workdir, 'cfg.py'), 'utf8'), /lr = 0.3/)
  } finally {
    process.env.PATH = saved.PATH
    process.env.HOME = saved.HOME
  }
})
