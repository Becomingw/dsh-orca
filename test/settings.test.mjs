import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { PolicySettings, normalizePolicy } from '../lib/settings.js'
import { apply } from '../index.js'
import { DEFAULT_POLICY, ACTIONS, decide } from '../lib/policy.js'
import { registerRoutes } from '../lib/routes.js'
import { fakeContext } from './helpers.mjs'

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'orca-policy-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  const path = join(root, 'policy.json'), settings = new PolicySettings(path, DEFAULT_POLICY)
  return { root, path, settings }
}

test('UI settings persist, survive restart, reject stale saves and restore the base config', t => {
  const { path, settings } = fixture(t)
  const initial = settings.snapshot()
  assert.equal(initial.policy.mode, 'controlled')
  assert.equal(initial.source, 'config')
  const open = settings.save({ revision: initial.revision, policy: { ...initial.policy, mode: 'open', grantTtlDays: 14 } })
  assert.equal(open.source, 'ui')
  assert.equal(statSync(path).mode & 0o777, 0o600)
  assert.equal(new PolicySettings(path, DEFAULT_POLICY).snapshot().policy.mode, 'open')
  assert.throws(() => settings.save({ revision: initial.revision, policy: initial.policy }), error => error.status === 409)
  assert.equal(settings.snapshot().policy.mode, 'open')
  const reset = settings.save({ revision: open.revision, reset: true })
  assert.deepEqual(reset.policy, normalizePolicy(DEFAULT_POLICY))
  assert.equal(reset.source, 'config')
  assert.equal(new PolicySettings(path, { ...DEFAULT_POLICY, grantTtlDays: 3 }).snapshot().policy.grantTtlDays, 3)
})

test('invalid updates do not change persisted policy; invalid persisted content never falls back', t => {
  const { path, settings } = fixture(t)
  const initial = settings.snapshot()
  settings.save({ revision: initial.revision, policy: initial.policy })
  const snapshot = settings.snapshot(), before = readFileSync(path, 'utf8')
  for (const patch of [{ mode: 'anything' }, { mode: null }, { allowedCommands: 'ls' }, { grantTtlDays: 0 }, { askOnce: ['unknown'] }, { trustedWorkdirs: ['relative'] }, { deniedCommands: [''] }, { foo: true }, { allowedCommands: ['a\nb'] }]) {
    assert.throws(() => settings.save({ revision: snapshot.revision, policy: { ...snapshot.policy, ...patch } }), error => error.status === 400)
    assert.equal(readFileSync(path, 'utf8'), before)
  }
  assert.throws(() => settings.save({ revision: snapshot.revision }), /supported fields/)
  assert.throws(() => settings.save({ revision: snapshot.revision, reset: true, policy: snapshot.policy }), /either policy or reset/)
  for (const raw of ['', '{', '{"version": 9, "policy": null}', '{"version":1,"policy":{"mode":"open"}}']) {
    writeFileSync(path, raw)
    assert.throws(() => settings.snapshot())
  }
})

test('open mode passes every Orca action, including denied commands, to the Harness pipeline', () => {
  const policy = { ...DEFAULT_POLICY, mode: 'open', trustedWorkdirs: ['/trusted'] }
  for (const name of Object.keys(ACTIONS)) {
    for (const approvalPolicy of ['ask', 'never']) {
      assert.deepEqual(decide({ name, arguments: { host: 'test', workdir: '/outside', command: 'sudo test; reboot' } }, policy, approvalPolicy, { has() { throw Error('open mode must not look up grants') } }), { kind: 'pass' })
    }
  }
})

test('saving a mode changes both pre-execute and guard without reloading; corrupt storage fails closed', async t => {
  const { root, path, settings } = fixture(t), ctx = fakeContext({ approvalPolicy: 'never' })
  apply(ctx, { grantsPath: join(root, 'grants.json'), policyPath: path })
  const call = { name: 'orca_run', arguments: { host: 'test', workdir: '/outside', command: 'sudo test' } }
  assert.equal((await ctx.preExecute(call)).kind, 'deny')
  assert.match(ctx.guards[0](call), /deniedCommands/)
  const initial = settings.snapshot()
  const saved = settings.save({ revision: initial.revision, policy: { ...initial.policy, mode: 'open' } })
  assert.equal((await ctx.preExecute(call)).kind, 'allow')
  assert.equal(ctx.guards[0](call), undefined)
  assert.equal((await ctx.preExecute({ name: 'orca_stop', arguments: { host: 'test', session: 'sample' } })).kind, 'allow')
  assert.equal(ctx.requests.length, 0)
  settings.save({ revision: saved.revision, reset: true })
  assert.equal((await ctx.preExecute(call)).kind, 'deny')
  assert.match(ctx.guards[0](call), /deniedCommands/)
  writeFileSync(path, '')
  assert.equal((await ctx.preExecute(call)).kind, 'deny')
  assert.match(ctx.guards[0](call), /could not be read/)
  assert.equal((await ctx.preExecute({ name: 'other_tool' })).kind, 'allow')
})

test('new grants use the changed duration; existing grants retain their expiry', async t => {
  const { root, settings, path } = fixture(t), grantsPath = join(root, 'grants.json'), ctx = fakeContext()
  apply(ctx, { grantsPath, policyPath: path })
  const args = { host: 'test', workdir: '/work', command: 'echo test' }
  await ctx.preExecute({ name: 'orca_run', arguments: args })
  const first = Object.values(JSON.parse(readFileSync(grantsPath)).grants)[0]
  const current = settings.snapshot()
  settings.save({ revision: current.revision, policy: { ...current.policy, grantTtlDays: 14 } })
  await ctx.preExecute({ name: 'orca_run', arguments: { ...args, workdir: '/second' } })
  const grants = Object.values(JSON.parse(readFileSync(grantsPath)).grants)
  assert.deepEqual(grants[0], first)
  assert.equal(grants[1].expiresAt - grants[1].grantedAt, 14 * 86400000)
})

test('settings endpoints use owner connection admission, validate inputs and protect against stale edits', async t => {
  const { settings } = fixture(t), routes = new Map(), effects = []
  const ctx = { get: name => name === 'connection' ? { fetch: { register: route => { routes.set(route.path, route); return () => routes.delete(route.path) } } } : undefined, effect: fn => effects.push(fn()) }
  const config = { hosts: [], panel: { pollSeconds: 0, jobLimit: 5 }, policy: DEFAULT_POLICY }
  registerRoutes(ctx, { settings, config, cache: { start() {}, stop() {}, snapshot: () => ({}) }, selected: { hosts: [], discovered: true }, tools: new Map() })
  t.after(() => effects.reverse().forEach(fn => fn?.()))
  const request = (name, body = {}) => routes.get(`/api/dsh-orca/${name}`).fetch(new Request('http://localhost/api/dsh-orca/' + name, { method: 'POST', body: JSON.stringify(body) }))
  for (const name of ['settings/get', 'settings/save']) assert.deepEqual(routes.get(`/api/dsh-orca/${name}`).methods, ['POST'])
  const original = await (await request('settings/get')).json()
  assert.equal((await request('settings/save', { policy: { ...original.policy, mode: 'open' } })).status, 400)
  const body = { revision: original.revision, policy: { ...original.policy, mode: 'open' } }
  assert.equal((await request('settings/save', { ...body, hosts: ['elsewhere'] })).status, 400)
  assert.equal((await request('settings/save', body)).status, 200)
  assert.equal((await (await request('summary')).json()).config.mode, 'open')
  assert.equal((await request('settings/save', body)).status, 409)
  assert.equal((await request('settings/save', { ...body, revision: settings.snapshot().revision, policy: {} })).status, 400)
  assert.equal((await request('settings/save', { large: 'x'.repeat(16384) })).status, 413)
})

test('an approval already on screen retains its displayed TTL while newer calls use updated settings', async t => {
  const { root, settings, path } = fixture(t), grantsPath = join(root, 'grants.json'), ctx = fakeContext()
  let release
  ctx.get('approval').request = request => request.reason.includes('/first') ? new Promise(resolve => { release = resolve }) : Promise.resolve('allowed-once')
  apply(ctx, { grantsPath, policyPath: path })
  const args = { host: 'test', workdir: '/first', command: 'echo test' }
  const pending = ctx.preExecute({ name: 'orca_run', arguments: args })
  const current = settings.snapshot()
  settings.save({ revision: current.revision, policy: { ...current.policy, grantTtlDays: 14 } })
  await ctx.preExecute({ name: 'orca_run', arguments: { ...args, workdir: '/second' } })
  release('allowed-once'); await pending
  for (const grant of Object.values(JSON.parse(readFileSync(grantsPath)).grants)) {
    assert.equal(grant.expiresAt - grant.grantedAt, (grant.workdir === '/first' ? 7 : 14) * 86400000)
  }
})
