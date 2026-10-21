/** Owner-editable policy overlay. Atomic persistence; no reload or YAML rewrite. */
import { createHash, randomUUID } from 'node:crypto'
import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'
import { ACTIONS, DEFAULT_POLICY } from './policy.js'

const actions = new Set(Object.values(ACTIONS))
const lists = ['autoAllow', 'askOnce', 'alwaysAsk', 'allowedCommands', 'deniedCommands', 'trustedWorkdirs']
const invalid = message => { throw Object.assign(new TypeError(`orca: ${message}`), { status: 400 }) }

export function normalizePolicy(value = {}, { complete = false } = {}) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalid('policy must be an object')
  if (complete && (Object.keys(value).some(key => !Object.hasOwn(DEFAULT_POLICY, key)) || Object.keys(DEFAULT_POLICY).some(key => !Object.hasOwn(value, key)))) invalid('policy must contain exactly the supported fields')
  const mode = value.mode ?? DEFAULT_POLICY.mode
  if (!['controlled', 'open'].includes(mode)) invalid('policy.mode must be controlled or open')
  const result = { mode }
  for (const key of lists) {
    const list = value[key] ?? DEFAULT_POLICY[key]
    if (!Array.isArray(list) || !list.every(item => typeof item === 'string')) invalid(`policy.${key} must be a list of strings`)
    if (list.length > 200 || list.some(item => !item.trim() || item.length > 2048 || /[\r\n\0]/.test(item))) invalid(`policy.${key} contains an empty, multiline or oversized rule`)
    if (['autoAllow', 'askOnce', 'alwaysAsk'].includes(key) && list.some(item => !actions.has(item))) invalid(`unknown action in policy.${key}`)
    if (key === 'trustedWorkdirs' && list.some(item => !item.startsWith('/'))) invalid('trustedWorkdirs must be absolute paths')
    result[key] = [...new Set(list)]
  }
  result.grantTtlDays = value.grantTtlDays ?? DEFAULT_POLICY.grantTtlDays
  if (!Number.isInteger(result.grantTtlDays) || result.grantTtlDays < 1 || result.grantTtlDays > 90) invalid('policy.grantTtlDays must be an integer in 1..90')
  if (complete && Object.values(value).some(item => item === null)) invalid('policy fields cannot be null')
  return result
}

export class PolicySettings {
  constructor(path, base) { this.path = path; this.base = normalizePolicy(base) }

  snapshot() {
    let raw = null
    try { raw = readFileSync(this.path, 'utf8') } catch (error) { if (error.code !== 'ENOENT') throw error }
    let policy = this.base, source = 'config'
    if (raw !== null) {
      const record = JSON.parse(raw)
      if (record?.version !== 1 || !Object.hasOwn(record, 'policy')) throw new Error('Invalid Orca policy settings file')
      if (record.policy !== null) { policy = normalizePolicy(record.policy, { complete: true }); source = 'ui' }
    }
    const revision = createHash('sha256').update(JSON.stringify(this.base)).update(raw ?? '').digest('hex')
    return { policy: structuredClone(policy), basePolicy: structuredClone(this.base), source, revision }
  }

  save({ policy, revision, reset = false }) {
    if (typeof revision !== 'string' || typeof reset !== 'boolean' || (reset && policy !== undefined)) invalid('revision and either policy or reset are required')
    const next = reset ? null : normalizePolicy(policy, { complete: true })
    mkdirSync(dirname(this.path), { recursive: true, mode: 0o700 })
    // Synchronous critical section serializes tabs in the owning host process.
    if (revision !== this.snapshot().revision) throw Object.assign(new Error('Settings changed. Reload the saved settings before saving again.'), { status: 409 })
    const tmp = `${this.path}.${randomUUID()}.tmp`
    try {
      writeFileSync(tmp, `${JSON.stringify({ version: 1, policy: next }, null, 2)}\n`, { mode: 0o600, flag: 'wx' })
      renameSync(tmp, this.path)
    } finally { rmSync(tmp, { force: true }) }
    return this.snapshot()
  }
}
