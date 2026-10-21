/** Versioned, JSON-only presentation data. Never changes model-facing output. */
import { summarizeHost } from './cache.js'

const short = (s, n = 500) => typeof s === 'string' ? s.slice(0, n) : s
const span = (text, value, from = 0) => {
  if (!value) return null
  const start = text.indexOf(value, from)
  return start < 0 ? null : [start, start + value.length]
}

export function presentationMeta(name, args, value, content) {
  const text = content[0]?.text ?? ''
  const v = value ?? {}
  const meta = { version: 1, tool: name, host: args.host, workdir: args.workdir, path: args.path,
    command: short(args.command), session: args.session, body: [0, text.length] }
  switch (name) {
    case 'orca_run':
      Object.assign(meta, { exit_code: v.exit_code, timed_out: !!v.timed_out, truncated: !!v.truncated,
        stdout: v.stdout ? [0, v.stdout.length] : null,
        stderr: v.stderr ? span(text, v.stderr, (v.stdout?.length ?? 0) + '[stderr]\n'.length) : null })
      break
    case 'orca_read':
      Object.assign(meta, { first_line: v.first_line, last_line: v.last_line, total_lines: v.total_lines,
        eof: v.eof_reached, next_start_line: v.next_start_line, truncated: !!v.content_truncated,
        changed: v.snapshot_consistent === false, fragment: !!v.line_fragment,
        body: [text.length - (v.content?.length ?? 0), text.length] })
      break
    case 'orca_patch':
      Object.assign(meta, { mode: v.mode, state: v.state, patch_id: v.patch_id,
        files: (v.files ?? []).map(f => ({ path: f.path, action: f.action, move_to: f.move_to,
          added: f.added ?? 0, removed: f.removed ?? 0, fuzzy: (f.matches ?? []).filter(m => m.level !== 'exact' || m.also_matches_lines?.length) })),
        conflicts: (v.conflicts ?? []).map(c => ({ path: c.path, message: short(c.message, 1500) })),
        warnings: (v.warnings ?? []).map(s => short(s, 1500)), error: short(v.error, 1500),
        restore_errors: v.restore_errors ?? [], uncertain: short(v.uncertain, 1500),
        truncated: !!v.diff_truncated, body: v.diff ? [text.lastIndexOf(v.diff), text.lastIndexOf(v.diff) + v.diff.length] : null })
      break
    case 'orca_rollback':
      Object.assign(meta, { patch_id: args.patch_id, state: v.state, restored: v.restored ?? [], conflicts: v.conflicts ?? [], saved: v.saved ?? [] })
      break
    case 'orca_status':
      Object.assign(meta, summarizeHost(v), { observedAt: v.timestamp, body: null })
      break
    case 'orca_grep': {
      const rows = []
      let offset = text.indexOf('\n') + 1
      const add = (path, line, str, match) => {
        const prefix = match ? `${path}:${line}: ` : `${path}-${line}- `
        const start = offset + prefix.length
        const end = start + str.length
        rows.push({ path, line, match, body: [start, end] })
        offset = end + 1
      }
      for (const m of v.matches ?? []) {
        let line = m.line - (m.before?.length ?? 0)
        for (const s of m.before ?? []) add(m.path, line++, s, false)
        add(m.path, m.line, m.text, true)
        line = m.line + 1
        for (const s of m.after ?? []) add(m.path, line++, s, false)
      }
      Object.assign(meta, { rows, pattern: short(args.pattern), fixed: !!args.fixed_string, ignoreCase: !!args.ignore_case,
        count: v.matches?.length ?? 0, files_matched: v.files_matched ?? 0, files_scanned: v.files_scanned,
        truncated: !!v.truncated, timed_out: !!v.timed_out })
      break
    }
    case 'orca_ls':
      Object.assign(meta, { count: v.entries?.length ?? 0, truncated: !!v.truncated, body: [(v.entries?.length ?? 0) ? `[${v.path}; ${v.entries.length} entries${v.truncated ? '; truncated' : ''}]\n`.length : text.length, text.length] })
      break
    case 'orca_launch':
      Object.assign(meta, { job_id: v.job_id, log_path: v.log_path ?? args.log_path, structured: !!v.orca_log })
      break
    case 'orca_stop':
      Object.assign(meta, { was_running: v.was_running, session_stopped: v.session_stopped, hard_killed: v.hard_killed, sigkilled: v.sigkilled_pgids?.length ?? 0 })
      break
    case 'orca_grants':
      Object.assign(meta, { action: args.action, count: (v.grants ?? v.revoked ?? []).length })
      break
  }
  // DSH rejects undefined-valued properties as non-lossless JSON.
  return JSON.parse(JSON.stringify(meta))
}
