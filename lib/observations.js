/**
 * Last observed content hash per remote file, so orca_patch can refuse to edit
 * a file that changed since the model last saw it (compare-and-swap), the way
 * Claude Code, Zed and DSH's own fs tools do for local files.
 *
 * Sources: orca_read (small files are hashed automatically), orca_patch dry
 * runs (state previewed) and applies (state we wrote). A stale entry only
 * costs a "changed since it was last read" conflict, which tells the model to
 * re-read: the safe direction.
 */
import { posix } from 'node:path'

const MAX_ENTRIES = 5000

export function remoteKey(host, path) {
  return `${host}\u0000${posix.normalize(path)}`
}

export class Observations {
  constructor(maxEntries = MAX_ENTRIES) {
    this.maxEntries = maxEntries
    this.map = new Map()
  }

  get(host, path) {
    return this.map.get(remoteKey(host, path))
  }

  /** sha256 hex, or null meaning "known not to exist". */
  record(host, path, sha256) {
    const key = remoteKey(host, path)
    this.map.delete(key) // re-insert to refresh LRU order
    this.map.set(key, sha256)
    if (this.map.size > this.maxEntries) this.map.delete(this.map.keys().next().value)
  }

  forget(host, path) {
    this.map.delete(remoteKey(host, path))
  }
}

/** Paths an apply_patch document will read before writing (Update/Delete sources). */
export function patchSources(patch) {
  if (typeof patch !== 'string') return []
  const paths = []
  for (const line of patch.split(/\r\n|[\n\r]/)) {
    const match = /^\*\*\* (?:Update|Delete) File: (.+)$/.exec(line.trim())
    if (match) paths.push(match[1].trim())
  }
  return paths
}

/**
 * Fill expected_sha256 for touched files the model did not pin itself.
 * @returns {{args: object, checked: string[]}}
 */
export function withObservedHashes(observations, args) {
  if (args.dry_run === true || typeof args.workdir !== 'string') return { args, checked: [] }
  const expected = { ...(args.expected_sha256 ?? {}) }
  const checked = []
  for (const rel of patchSources(args.patch)) {
    if (Object.hasOwn(expected, rel)) continue
    const sha = observations.get(args.host, posix.join(args.workdir, rel))
    if (typeof sha === 'string') {
      expected[rel] = sha
      checked.push(rel)
    }
  }
  return checked.length ? { args: { ...args, expected_sha256: expected }, checked } : { args, checked }
}

/** Learn file states from an orca_patch / orca_read result. */
export function learn(observations, tool, args, value) {
  if (!value || typeof value !== 'object') return
  if (tool === 'orca_read') {
    if (typeof value.sha256 === 'string') observations.record(args.host, args.path, value.sha256)
    return
  }
  if (tool === 'orca_patch' && typeof args.workdir === 'string') {
    const abs = (rel) => posix.join(args.workdir, rel)
    const applied = value.state === 'applied'
    for (const file of value.files ?? []) {
      if (value.mode === 'dry_run') {
        if (file.sha256_before) observations.record(args.host, abs(file.path), file.sha256_before)
        continue
      }
      if (!applied) continue
      if (file.move_to) {
        observations.forget(args.host, abs(file.path))
        observations.record(args.host, abs(file.move_to), file.sha256_after)
      } else if (file.action === 'delete') {
        observations.forget(args.host, abs(file.path))
      } else if (file.sha256_after) {
        observations.record(args.host, abs(file.path), file.sha256_after)
      }
    }
  }
  if (tool === 'orca_rollback' && typeof value.workdir === 'string') {
    for (const rel of value.restored ?? []) observations.forget(args.host, posix.join(value.workdir, rel))
  }
}
