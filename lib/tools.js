/**
 * Model-facing orca_* tools. Parameter schemas mirror the engine's
 * `inputSchema` (test/schemas.test.mjs fails on drift). DSH accepts only
 * type/enum/properties/required/... plus annotations, so numeric ranges and
 * patterns live in descriptions and the engine enforces them. Renderers keep
 * results compact because tool output stays in context until compaction.
 */
import { learn, withObservedHashes } from './observations.js'

/** orca tool name → engine (upstream) tool name. */
export const ENGINE_TOOL = {
  orca_status: 'remote_status',
  orca_read: 'remote_read_file',
  orca_run: 'remote_run',
  orca_launch: 'remote_launch_tmux',
  orca_stop: 'remote_stop_job',
  orca_grep: 'orca_grep',
  orca_ls: 'orca_ls',
  orca_patch: 'orca_patch',
  orca_rollback: 'orca_rollback',
}

/** Tools that change remote state run one at a time. */
const EXCLUSIVE = new Set(['orca_run', 'orca_launch', 'orca_stop', 'orca_patch', 'orca_rollback'])

function hostSchema(hosts) {
  const schema = { type: 'string', description: 'Concrete SSH alias from ~/.ssh/config.' }
  if (Array.isArray(hosts) && hosts.length > 0) schema.enum = [...hosts]
  return schema
}

export function parameterSchemas(hosts) {
  const host = hostSchema(hosts)
  return {
    orca_status: {
      type: 'object',
      additionalProperties: false,
      properties: {
        host,
        workdir: { type: 'string', description: 'Path whose disk usage to report (default: remote home).' },
        session: { type: 'string', description: 'Optional exact tmux session/job filter.' },
        log_tail_lines: { type: 'integer', default: 10, description: 'Log lines per job, 0-200.' },
        job_limit: { type: 'integer', default: 20, description: 'Newest jobs to report, 1-100.' },
        detail: {
          type: 'string',
          enum: ['compact', 'full'],
          default: 'compact',
          description: 'compact omits GPU UUIDs, static host facts, and registry bookkeeping; full returns everything.',
        },
        include_gpus: {
          type: 'boolean',
          default: false,
          description: 'With a session filter, keep per-GPU objects instead of utilization/memory lists.',
        },
      },
      required: ['host'],
    },
    orca_read: {
      type: 'object',
      additionalProperties: false,
      properties: {
        host,
        path: { type: 'string', description: 'Absolute remote file path.' },
        start_line: { type: 'integer', default: 1, description: 'First line to return (>= 1), counted from offset_bytes.' },
        end_line: { type: 'integer', default: 0, description: 'Inclusive end line; 0 = until EOF or max_bytes.' },
        max_bytes: { type: 'integer', default: 262144, description: 'Byte budget, 1-1048576; continue with next_start_line.' },
        offset_bytes: {
          type: 'integer',
          default: 0,
          description: 'Byte offset to start at; line numbers restart there. Prefer next_offset_bytes for paging huge files.',
        },
        include_sha256: { type: 'boolean', default: false, description: 'Also hash the whole file (slow on huge files).' },
      },
      required: ['host', 'path'],
    },
    orca_run: {
      type: 'object',
      additionalProperties: false,
      properties: {
        host,
        workdir: { type: 'string', description: 'Absolute remote working directory.' },
        command: { type: 'string', description: 'Command interpreted by remote `bash -lc`.' },
        timeout_seconds: { type: 'integer', default: 60, description: 'Seconds, 1-3600; the remote process group is killed after it.' },
        max_output_chars: {
          type: 'integer',
          default: 100000,
          description: 'Decoded-character budget shared by stdout and stderr, 1000-200000.',
        },
      },
      required: ['host', 'workdir', 'command'],
    },
    orca_launch: {
      type: 'object',
      additionalProperties: false,
      properties: {
        host,
        workdir: { type: 'string', description: 'Absolute remote working directory.' },
        session: { type: 'string', description: 'Unique tmux session name: letters, digits, _ and - (no dots), at most 80 chars.' },
        command: { type: 'string', description: 'Long-running command for remote bash.' },
        log_path: { type: 'string', description: 'Absolute tee log path; parent dirs are created.' },
        append_log: { type: 'boolean', default: false, description: 'Allow appending to an existing log.' },
      },
      required: ['host', 'workdir', 'session', 'command', 'log_path'],
    },
    orca_stop: {
      type: 'object',
      additionalProperties: false,
      properties: {
        host,
        session: { type: 'string', description: 'Session of a job launched by orca_launch or remote-fleet.' },
        grace_seconds: { type: 'integer', default: 10, description: 'Seconds, 0-60, after SIGTERM before the session is killed; survivors are then SIGKILLed.' },
      },
      required: ['host', 'session'],
    },
    orca_grep: {
      type: 'object',
      additionalProperties: false,
      properties: {
        host,
        path: { type: 'string', description: 'Absolute file or directory. Directories are walked, skipping hidden, .git, node_modules, venvs; .gz files are searched too.' },
        pattern: { type: 'string', description: 'Python regular expression (or literal text with fixed_string).' },
        fixed_string: { type: 'boolean', default: false },
        ignore_case: { type: 'boolean', default: false },
        glob: { type: 'string', description: 'Only files whose name or relative path matches, e.g. "*.py".' },
        context_lines: { type: 'integer', default: 0, description: 'Lines of context before and after each match, 0-10.' },
        max_matches: { type: 'integer', default: 100, description: '1-2000.' },
        include_hidden: { type: 'boolean', default: false },
        timeout_seconds: { type: 'integer', default: 30, description: '1-300; partial results are returned with timed_out.' },
      },
      required: ['host', 'path', 'pattern'],
    },
    orca_ls: {
      type: 'object',
      additionalProperties: false,
      properties: {
        host,
        path: { type: 'string', description: 'Absolute directory.' },
        depth: { type: 'integer', default: 1, description: 'Levels to descend, 1-5.' },
        glob: { type: 'string', description: 'Only entries whose name or relative path matches.' },
        max_entries: { type: 'integer', default: 300, description: '1-5000.' },
        include_hidden: { type: 'boolean', default: false },
      },
      required: ['host', 'path'],
    },
    orca_patch: {
      type: 'object',
      additionalProperties: false,
      properties: {
        host,
        workdir: { type: 'string', description: 'Absolute patch root directory; every path stays inside it.' },
        patch: { type: 'string', description: 'Codex patch document (*** Begin Patch ... *** End Patch) with Add/Delete/Update File and optional Move to.' },
        expected_sha256: {
          type: 'object',
          description: 'Optional map of relative path -> 64-hex SHA-256 the file must have, or null = must not exist; get hashes from orca_read include_sha256.',
          additionalProperties: true,
        },
        dry_run: { type: 'boolean', default: false, description: 'Validate and return the unified diff without writing anything.' },
        strict: { type: 'boolean', default: false, description: 'Refuse hunks that match ambiguously or only after whitespace/Unicode normalisation.' },
        patch_id: { type: 'string', description: 'Idempotency key. Omit normally; when a call timed out, retry with the patch_id it reported and the patch is never applied twice.' },
      },
      required: ['host', 'workdir', 'patch'],
    },
    orca_rollback: {
      type: 'object',
      additionalProperties: false,
      properties: {
        host,
        patch_id: { type: 'string', description: 'patch_id returned by a successful orca_patch.' },
        force: { type: 'boolean', default: false, description: 'Restore even if files changed after the patch (overwrites those changes).' },
      },
      required: ['host', 'patch_id'],
    },
    orca_grants: {
      type: 'object',
      additionalProperties: false,
      properties: {
        action: { type: 'string', enum: ['list', 'revoke'], description: 'list remembered approvals, or revoke those matching the filters (no filter = all).' },
        host: { type: 'string' },
        workdir: { type: 'string' },
        tool: { type: 'string', enum: ['run', 'launch'], description: 'Remembered action to revoke.' },
      },
      required: ['action'],
    },
  }
}

export const DESCRIPTIONS = {
  orca_status:
    'Remote host load, memory, disk, GPUs, tmux sessions, and registered long-job status (jobs launched by orca or remote-fleet). Read-only. Use a session filter to check one job.',
  orca_read:
    'Read a line slice of a UTF-8 text file on a remote SSH host, streamed remotely so it works on huge files. Read-only. The header line reports paging cursors.',
  orca_run:
    'Run a bounded shell command in an absolute working directory on a trusted remote SSH host (shared GPU machines: avoid touching other users\' processes). For long jobs use orca_launch instead. Check the [exit code: N] marker.',
  orca_launch:
    'Start a long-running command in a detached tmux session with a tee log, registered job identity, exit code and a structured log (normalized tail used by orca_status). Visible to remote-fleet in Codex/Claude too. Check progress with orca_status session filter.',
  orca_stop:
    'Stop a job started by orca_launch or remote-fleet: SIGTERM, grace period, kill the session, SIGKILL survivors, record the exit status. Refuses tmux sessions that are not registered jobs.',
  orca_grep:
    'Search remote files server-side and return only matching lines (with optional context). Read-only. Use on logs (including rotated .gz events) and source trees instead of reading whole files.',
  orca_ls:
    'List a remote directory (type, size, mtime), optionally a few levels deep. Read-only.',
  orca_patch:
    'Edit files on a remote host with a Codex patch, atomically: all files change or none. dry_run returns the diff without writing. A real apply backs up the originals and returns patch_id for orca_rollback. Files read with orca_read are checked automatically: if one changed since, the patch is refused; re-read and regenerate it. Line endings, BOM, owner and hard links are preserved.',
  orca_rollback:
    'Undo an orca_patch by patch_id, restoring the exact original bytes and modes. Refuses if a file changed after the patch unless force.',
  orca_grants:
    'List or revoke remembered orca approvals (e.g. "run on host:workdir for 7 days"). Cannot create approvals.',
}

// -- renderers ----------------------------------------------------------------

function text(value) {
  return [{ type: 'text', text: value }]
}

export function renderStatus(value) {
  return text(JSON.stringify(value))
}

export function renderRead(value) {
  const parts = [value.path]
  if (value.first_line !== undefined) {
    const range = `lines ${value.first_line}-${value.last_line ?? value.first_line}`
    parts.push(value.total_lines !== undefined ? `${range} of ${value.total_lines}` : range)
  }
  if (value.eof_reached) parts.push('eof')
  if (value.next_start_line !== undefined) parts.push(`next_start_line=${value.next_start_line}`)
  if (value.next_offset_bytes !== undefined) parts.push(`next_offset_bytes=${value.next_offset_bytes}`)
  if (value.content_truncated) parts.push('content truncated')
  if (value.line_fragment) parts.push('line fragment')
  if (value.size_bytes !== undefined) parts.push(`${value.size_bytes} bytes`)
  if (value.sha256) parts.push(`sha256=${value.sha256}`)
  if (value.snapshot_consistent === false) parts.push('file changed while reading')
  return text(`[${parts.join('; ')}]\n${value.content ?? ''}`)
}

export function renderRun(value) {
  const flags = []
  if (value.timed_out) flags.push('timed out')
  if (value.truncated) flags.push('output truncated')
  let out = value.stdout ?? ''
  if (value.stderr) out += `${out && !out.endsWith('\n') ? '\n' : ''}[stderr]\n${value.stderr}`
  const marker = `[exit code: ${value.exit_code}${flags.length ? `; ${flags.join('; ')}` : ''}]`
  return text(out ? `${out}${out.endsWith('\n') ? '' : '\n'}${marker}` : marker)
}

export function renderJson(value) {
  return text(JSON.stringify(value))
}

export function renderGrep(value) {
  const lines = []
  for (const match of value.matches ?? []) {
    let line = match.line - (match.before?.length ?? 0)
    for (const before of match.before ?? []) lines.push(`${match.path}-${line++}- ${before}`)
    lines.push(`${match.path}:${match.line}: ${match.text}`)
    line = match.line + 1
    for (const after of match.after ?? []) lines.push(`${match.path}-${line++}- ${after}`)
  }
  const notes = [`${value.matches?.length ?? 0} matches in ${value.files_matched ?? 0}/${value.files_scanned ?? 0} files`]
  if (value.truncated) notes.push(`truncated: ${value.truncated}`)
  if (value.timed_out) notes.push('timed out (partial)')
  return text(`[${notes.join('; ')}]${lines.length ? `\n${lines.join('\n')}` : ''}`)
}

export function renderLs(value) {
  const rows = (value.entries ?? []).map((entry) => {
    const when = new Date(entry.mtime * 1000).toISOString().slice(0, 16).replace('T', ' ')
    if (entry.type === 'd') return `d ${''.padStart(10)} ${when} ${entry.path}/`
    if (entry.type === 'l') return `l ${''.padStart(10)} ${when} ${entry.path} -> ${entry.target ?? '?'}`
    return `f ${String(entry.size ?? '').padStart(10)} ${when} ${entry.path}`
  })
  const head = `[${value.path}; ${rows.length} entries${value.truncated ? '; truncated' : ''}]`
  return text(rows.length ? `${head}\n${rows.join('\n')}` : head)
}

const PATCH_HEADS = {
  clean: '[dry run: patch applies cleanly; nothing written]',
  applied: '[applied; patch_id={id}; undo with orca_rollback]',
  already_applied: '[already applied earlier (patch_id={id}); nothing written now]',
  unchanged: '[patch changes nothing; nothing written]',
  restored: '[NOT applied: a write failed and every touched file was restored]',
  partial: '[PARTIALLY APPLIED — run orca_rollback patch_id={id}]',
  not_applied: '[NOT applied; nothing changed]',
}

function conflictLines(value) {
  return (value.conflicts ?? []).map((c) => `conflict ${c.path}: ${c.message}`)
}

export function renderPatch(value) {
  let head
  if (value.state === 'conflict') head = value.mode === 'dry_run' ? '[dry run: patch does NOT apply; nothing written]' : '[NOT applied: conflicts; nothing written]'
  else head = (PATCH_HEADS[value.state] ?? `[${value.state ?? (value.ok ? 'ok' : 'failed')}]`).replace('{id}', value.patch_id ?? '?')
  const parts = [head]
  if (value.uncertain) parts.push(`(the reply was lost: ${value.uncertain}; the state above was checked afterwards)`)
  if (value.error) parts.push(value.error)
  for (const f of value.files ?? []) {
    const fuzz = (f.matches ?? []).map((m) => `hunk ${m.hunk} at line ${m.line}${m.level !== 'exact' ? ` (${m.level} match)` : ''}${m.also_matches_lines ? ` — also matches lines ${m.also_matches_lines.join(', ')}` : ''}`)
    parts.push(`${f.action} ${f.path}${f.move_to ? ` -> ${f.move_to}` : ''} (+${f.added ?? 0}/-${f.removed ?? 0})${f.unchanged ? ' unchanged' : ''}${fuzz.length ? `; ${fuzz.join('; ')}` : ''}`)
  }
  parts.push(...conflictLines(value))
  for (const e of value.restore_errors ?? []) parts.push(`restore failed ${e.path}: ${e.message}`)
  for (const w of value.warnings ?? []) parts.push(`warning: ${w}`)
  if (value.checked_against_last_read?.length) parts.push(`checked unchanged since last read: ${value.checked_against_last_read.join(', ')}`)
  if (value.diff) parts.push(value.diff + (value.diff_truncated ? '\n[diff truncated]' : ''))
  return text(parts.join('\n'))
}

export function renderRollback(value) {
  const parts = []
  switch (value.state) {
    case 'rolled_back': parts.push(`[rolled back ${value.patch_id}]`, ...(value.restored ?? [])); break
    case 'already_rolled_back': parts.push(`[nothing to do] ${value.note ?? ''}`); break
    case 'conflict': parts.push('[rollback refused: files changed after the patch]', ...conflictLines(value), value.hint ?? ''); break
    default: parts.push(`[rollback ${value.state ?? 'failed'}] ${value.error ?? ''}`, ...(value.errors ?? []).map((e) => `${e.path}: ${e.message}`))
  }
  for (const s of value.saved ?? []) parts.push(`overwritten content of ${s.path} saved to ${s.saved_as}`)
  return text(parts.filter(Boolean).join('\n'))
}

export const RENDERERS = {
  orca_status: renderStatus,
  orca_read: renderRead,
  orca_run: renderRun,
  orca_launch: renderJson,
  orca_stop: renderJson,
  orca_grep: renderGrep,
  orca_ls: renderLs,
  orca_patch: renderPatch,
  orca_rollback: renderRollback,
  orca_grants: renderJson,
}

function definition(name, parameters, execute) {
  return {
    name,
    description: DESCRIPTIONS[name],
    parameters,
    output: {
      schema: { type: 'object' },
      render: (_args, value) => RENDERERS[name](value),
    },
    ...(EXCLUSIVE.has(name) ? {} : { isConcurrencySafe: () => true }),
    execute,
  }
}

/**
 * Build raw tool definitions for `ctx.tools.register`.
 * @param {(tool: string, args: object, signal: AbortSignal) => Promise<object>} call - engine bridge.
 * @param {{hosts?: string[], orcaLog?: object | null, grants?: import('./grants.js').GrantStore}} options
 */
export function createTools(call, { hosts, orcaLog = null, grants, observations } = {}) {
  const schemas = parameterSchemas(hosts)
  const tools = Object.keys(ENGINE_TOOL).map((name) =>
    definition(name, schemas[name], async (args, exec) => {
      let engineArgs = args
      let checked = []
      if (name === 'orca_launch' && orcaLog) engineArgs = { ...args, orca_log: orcaLog }
      if (name === 'orca_read' && observations) engineArgs = { ...args, orca_auto_sha256: true }
      if (name === 'orca_patch' && observations) ({ args: engineArgs, checked } = withObservedHashes(observations, args))
      const value = await call(ENGINE_TOOL[name], engineArgs, exec.signal)
      if (observations) learn(observations, name, args, value)
      return checked.length && value && typeof value === 'object' ? { ...value, checked_against_last_read: checked } : value
    }),
  )
  if (grants) {
    tools.push(definition('orca_grants', schemas.orca_grants, async (args) => {
      if (args.action === 'list') return { grants: grants.active() }
      const revoked = grants.revoke({ host: args.host, workdir: args.workdir, action: args.tool })
      return { revoked }
    }))
  }
  return tools
}
