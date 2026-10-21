/**
 * Remembered approvals ("askOnce" grants). DSH approvals are single-use
 * (`allowed-once`), so orca keeps its own expiring grants, keyed by
 * host + workdir + action, in a plugin-owned JSON file.
 *
 * The file lives beside DSH's own storages (`$DSH_HOME/storages/orca/`),
 * is written atomically, and is re-read on every lookup so a revoke from
 * another session or process takes effect immediately.
 */
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'

export const DAY_MS = 24 * 60 * 60 * 1000

export function defaultGrantsPath(env = process.env) {
  const dshHome = env.DSH_HOME || join(homedir(), '.dsh')
  return join(dshHome, 'storages', 'orca', 'grants.json')
}

export function grantKey(host, workdir, action) {
  return JSON.stringify([host, workdir, action])
}

export class GrantStore {
  /**
   * @param {string} path - JSON file location.
   * @param {{ttlMs?: number, now?: () => number}} options
   */
  constructor(path, { ttlMs = 7 * DAY_MS, now = Date.now } = {}) {
    this.path = path
    this.ttlMs = ttlMs
    this.now = now
  }

  load() {
    try {
      const data = JSON.parse(readFileSync(this.path, 'utf8'))
      return data && typeof data.grants === 'object' && data.grants !== null ? data.grants : {}
    } catch {
      return {}
    }
  }

  save(grants) {
    mkdirSync(dirname(this.path), { recursive: true, mode: 0o700 })
    const tmp = `${this.path}.${process.pid}.tmp`
    writeFileSync(tmp, `${JSON.stringify({ version: 1, grants }, null, 2)}\n`, { mode: 0o600 })
    renameSync(tmp, this.path)
  }

  /** Unexpired grants only; expired ones are dropped on the next write. */
  active() {
    const now = this.now()
    return Object.values(this.load()).filter((grant) => grant.expiresAt > now)
  }

  has(host, workdir, action) {
    const grant = this.load()[grantKey(host, workdir, action)]
    return grant !== undefined && grant.expiresAt > this.now()
  }

  grant(host, workdir, action) {
    const now = this.now()
    const grants = Object.fromEntries(Object.entries(this.load()).filter(([, g]) => g.expiresAt > now))
    const record = { host, workdir, action, grantedAt: now, expiresAt: now + this.ttlMs }
    grants[grantKey(host, workdir, action)] = record
    this.save(grants)
    return record
  }

  /** Revoke grants matching every given field; no filter revokes all. */
  revoke({ host, workdir, action } = {}) {
    const grants = this.load()
    const removed = []
    for (const [key, grant] of Object.entries(grants)) {
      if ((host === undefined || grant.host === host)
        && (workdir === undefined || grant.workdir === workdir)
        && (action === undefined || grant.action === action)) {
        removed.push(grant)
        delete grants[key]
      }
    }
    if (removed.length > 0) this.save(grants)
    return removed
  }
}
