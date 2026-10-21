import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

/**
 * A sandbox whose `ssh` runs the remote command locally, so the full
 * Node → engine → "ssh" → remote program path runs without a real host.
 * tmux gets its own socket directory so tests never touch a real server.
 */
export function fakeFleet() {
  // /tmp keeps the tmux socket path short.
  const root = mkdtempSync(join('/tmp', 'orca-test-'))
  const bin = join(root, 'bin')
  const home = join(root, 'home')
  mkdirSync(bin)
  mkdirSync(home)
  mkdirSync(join(root, 'tmux'))
  writeFileSync(
    join(bin, 'ssh'),
    // The remote command is the last argument; the alias is the one before it.
    '#!/bin/sh\nfor last; do :; done\nexec /bin/sh -c "$last"\n',
    { mode: 0o755 },
  )
  const env = {
    HOME: home,
    PATH: `${bin}:${process.env.PATH}`,
    PYTHONDONTWRITEBYTECODE: '1',
    REMOTE_FLEET_HOSTS: 'fake',
    REMOTE_FLEET_SSH_PERSIST: '0',
    TMUX_TMPDIR: join(root, 'tmux'),
    LANG: 'C.UTF-8',
  }
  return { root, home, env, cleanup: () => rmSync(root, { recursive: true, force: true }) }
}

export { tmpdir }

/** Minimal stand-in for the Cordis context the plugin touches. */
export function fakeContext({ approvalPolicy = 'ask', outcome = 'allowed-once' } = {}) {
  const tools = new Map()
  const guards = []
  const listeners = new Map()
  const requests = []
  const approval = {
    effectivePolicy: () => approvalPolicy,
    async request(req) {
      requests.push(req)
      return outcome
    },
  }
  return {
    tools: {
      register(definition) {
        tools.set(definition.name, definition)
        return () => tools.delete(definition.name)
      },
      guard(fn) {
        guards.push(fn)
        return () => {}
      },
    },
    on(event, listener) {
      listeners.set(event, listener)
      return () => listeners.delete(event)
    },
    get(name) {
      return name === 'approval' ? approval : undefined
    },
    registered: tools,
    guards,
    requests,
    preExecute(exec) {
      const full = { agent: { session: {} }, callId: 'c1', signal: new AbortController().signal, ...exec }
      return listeners.get('tools/pre-execute')(full, async () => ({ kind: 'allow' }))
    },
  }
}
