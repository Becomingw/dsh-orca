import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { GrantStore } from '../lib/grants.js'
import {
  DEFAULT_POLICY, decide, deniedReason, globToRegExp, isPreApproved, isTrusted, normalizeWorkdir, patchSummary, tierOf,
} from '../lib/policy.js'

const policy = {
  ...DEFAULT_POLICY,
  allowedCommands: ['git status', 'nvidia-smi *', 'tail *'],
  deniedCommands: ['rm -rf /*', 'sudo *'],
}
const noGrants = { has: () => false }
const allGrants = { has: () => true }
const call = (name, args) => ({ name, arguments: { host: '13', workdir: '/data/p', ...args } })
const run = (command, extra = {}) => call('orca_run', { command, ...extra })

test('glob is anchored and treats regex metacharacters literally', () => {
  assert.ok(globToRegExp('tail *').test('tail -n 5 x.log'))
  assert.ok(!globToRegExp('tail *').test('xtail -n 5'))
  assert.ok(globToRegExp('a.b').test('a.b'))
  assert.ok(!globToRegExp('a.b').test('axb'))
})

test('read-only tools pass through to the rest of the pipeline', () => {
  for (const name of ['orca_status', 'orca_read', 'orca_grep', 'orca_ls', 'orca_grants']) {
    assert.deepEqual(decide(call(name, {}), policy, 'ask', noGrants), { kind: 'pass' }, name)
  }
})

test('run and launch are askOnce keyed by host, normalized workdir and action', () => {
  const decision = decide(run('python train.py', { workdir: '/data/p/' }), policy, 'ask', noGrants)
  assert.equal(decision.kind, 'askOnce')
  assert.deepEqual(decision.grant, { host: '13', workdir: '/data/p', action: 'run' })
  assert.match(decision.displayReason.zh, /7 天内/)
  assert.equal(decide(call('orca_launch', { session: 's', command: 'x' }), policy, 'ask', noGrants).kind, 'askOnce')
})

test('an active grant skips the ask', () => {
  assert.deepEqual(decide(run('python train.py'), policy, 'ask', allGrants), { kind: 'pass' })
})

test('patch, rollback and stop always ask; stop can never be remembered or auto', () => {
  for (const [name, args] of [['orca_patch', { patch: '*** Begin Patch\n*** Update File: a.py\n*** End Patch' }], ['orca_rollback', { patch_id: 'p' }], ['orca_stop', { session: 's' }]]) {
    const decision = decide(call(name, args), policy, 'ask', allGrants)
    assert.equal(decision.kind, 'ask', name)
  }
  assert.equal(tierOf('stop', { ...policy, askOnce: ['stop'], alwaysAsk: [] }), 'alwaysAsk')
  assert.equal(tierOf('stop', { ...policy, autoAllow: ['stop'], alwaysAsk: [] }), 'alwaysAsk')
})

test('patch prompt lists files; dry run passes', () => {
  const patch = '*** Begin Patch\n*** Update File: a.py\n@@\n-x\n+y\n*** Add File: b.py\n+z\n*** End Patch'
  assert.match(decide(call('orca_patch', { patch }), policy, 'ask', noGrants).displayReason.zh, /Update a\.py, Add b\.py/)
  assert.deepEqual(decide(call('orca_patch', { patch, dry_run: true }), policy, 'ask', noGrants), { kind: 'pass' })
  assert.match(patchSummary(Array.from({ length: 10 }, (_, i) => `*** Add File: f${i}`).join('\n')), /10 files/)
})

test('allowedCommands skip the ask only when every segment is allowed', () => {
  assert.equal(decide(run('git status'), policy, 'ask', noGrants).kind, 'pass')
  assert.equal(decide(run('git status && nvidia-smi -L'), policy, 'ask', noGrants).kind, 'pass')
  assert.equal(decide(run('git status; python evil.py'), policy, 'ask', noGrants).kind, 'askOnce')
  assert.equal(decide(run('tail x.log | sh'), policy, 'ask', noGrants).kind, 'askOnce')
})

test('substitution and redirection are never pre-approved', () => {
  assert.ok(!isPreApproved('tail $(rm x)', policy.allowedCommands))
  assert.ok(!isPreApproved('tail `rm x`', policy.allowedCommands))
  assert.ok(!isPreApproved('tail x > y', policy.allowedCommands))
})

test('deniedCommands win over full access, grants and allowed segments, for run and launch', () => {
  assert.equal(decide(run('sudo reboot'), policy, 'never', allGrants).kind, 'deny')
  assert.equal(decide(run('git status;  rm  -rf /data'), policy, 'ask', noGrants).kind, 'deny')
  assert.equal(decide(call('orca_launch', { session: 's', command: 'sudo x' }), policy, 'never', noGrants).kind, 'deny')
  assert.ok(deniedReason('cd x && sudo ls', policy.deniedCommands))
})

test('approval "never" denies writes instead of running them (strict headless)', () => {
  // DSH defines `never` as "deny every request without prompting"; running the
  // call instead would turn the lockdown mode into full access.
  assert.equal(decide(run('python train.py'), policy, 'never', noGrants).kind, 'deny')
  assert.equal(decide(call('orca_stop', { session: 's' }), policy, 'never', noGrants).kind, 'deny')
  assert.equal(decide(call('orca_patch', { workdir: '/w', patch: '*** Begin Patch\n*** End Patch' }), policy, 'never', noGrants).kind, 'deny')
  // Paths that never needed approval keep working in headless mode.
  assert.equal(decide(run('git status'), policy, 'never', noGrants).kind, 'pass')
  assert.equal(decide(run('python train.py'), policy, 'never', allGrants).kind, 'pass')
  assert.equal(decide(call('orca_status', { host: '13' }), policy, 'never', noGrants).kind, 'pass')
})

test('writes outside trustedWorkdirs always ask, even with grants, allowed commands or autoAllow', () => {
  const trusted = { ...policy, trustedWorkdirs: ['/data/local_14T/mengfei/*'], autoAllow: [...policy.autoAllow, 'launch'], askOnce: ['run'] }
  assert.equal(decide(run('git status', { workdir: '/data/other' }), trusted, 'ask', allGrants).kind, 'ask')
  assert.equal(decide(run('git status', { workdir: '/data/local_14T/mengfei/p' }), trusted, 'ask', noGrants).kind, 'pass')
  assert.equal(decide(call('orca_launch', { workdir: '/data/other', session: 's', command: 'x' }), trusted, 'ask', noGrants).kind, 'ask')
  assert.equal(decide(call('orca_launch', { workdir: '/data/local_14T/mengfei/p', session: 's', command: 'x' }), trusted, 'ask', noGrants).kind, 'pass')
  // `..` cannot climb out of a trusted root.
  assert.equal(normalizeWorkdir('/data/local_14T/mengfei/../../etc'), '/data/etc')
  assert.ok(!isTrusted(normalizeWorkdir('/data/local_14T/mengfei/../x'), trusted.trustedWorkdirs))
  assert.ok(isTrusted('/data/local_14T/mengfei', trusted.trustedWorkdirs))
})

test('grant store remembers, expires and revokes', () => {
  const dir = mkdtempSync(join(tmpdir(), 'orca-grants-'))
  try {
    let now = 1_000
    const store = new GrantStore(join(dir, 'g', 'grants.json'), { ttlMs: 100, now: () => now })
    assert.equal(store.has('13', '/w', 'run'), false)
    store.grant('13', '/w', 'run')
    store.grant('16', '/w', 'launch')
    assert.equal(store.has('13', '/w', 'run'), true)
    assert.equal(store.has('13', '/w', 'launch'), false)
    // A second store on the same file sees the grant (cross-session).
    assert.equal(new GrantStore(store.path, { now: () => now }).has('13', '/w', 'run'), true)
    assert.deepEqual(store.revoke({ host: '16' }).map((g) => g.host), ['16'])
    assert.equal(store.active().length, 1)
    now += 101
    assert.equal(store.has('13', '/w', 'run'), false)
    assert.equal(store.active().length, 0)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('observations: auto-fill only unpinned Update/Delete sources; learn from results', async () => {
  const { Observations, withObservedHashes, learn, patchSources } = await import('../lib/observations.js')
  const obs = new Observations(3)
  const patch = '*** Begin Patch\n*** Update File: a.py\n@@\n-x\n+y\n  *** Delete File: b.py\n*** Add File: c.py\n+z\n*** End Patch'
  assert.deepEqual(patchSources(patch), ['a.py', 'b.py'])
  obs.record('13', '/w/./a.py', 'aa')
  obs.record('13', '/w/b.py', 'bb')
  const { args, checked } = withObservedHashes(obs, { host: '13', workdir: '/w', patch, expected_sha256: { 'b.py': 'mine' } })
  assert.deepEqual(args.expected_sha256, { 'a.py': 'aa', 'b.py': 'mine' })
  assert.deepEqual(checked, ['a.py'])
  assert.deepEqual(withObservedHashes(obs, { host: '16', workdir: '/w', patch }).checked, [])
  assert.deepEqual(withObservedHashes(obs, { host: '13', workdir: '/w', patch, dry_run: true }).checked, [])
  learn(obs, 'orca_patch', { host: '13', workdir: '/w' }, { mode: 'apply', state: 'applied', files: [
    { path: 'a.py', action: 'update', sha256_after: 'a2' }, { path: 'b.py', action: 'delete' }, { path: 'm.py', move_to: 'n.py', action: 'move', sha256_after: 'n1' }] })
  assert.equal(obs.get('13', '/w/a.py'), 'a2')
  assert.equal(obs.get('13', '/w/b.py'), undefined)
  assert.equal(obs.get('13', '/w/n.py'), 'n1')
  learn(obs, 'orca_patch', { host: '13', workdir: '/w' }, { mode: 'apply', state: 'conflict', files: [{ path: 'a.py', sha256_after: 'zz' }] })
  assert.equal(obs.get('13', '/w/a.py'), 'a2', 'a refused patch teaches nothing')
  obs.record('13', '/x', '1'); obs.record('13', '/y', '2')
  assert.equal(obs.map.size, 3, 'bounded LRU')
})
