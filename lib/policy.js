/**
 * Approval decisions for orca tools (docs/policy.md).
 *
 * Pure functions only: index.js wires them to `tools/pre-execute`,
 * `ctx.approval` and `ctx.tools.guard()`.
 */
import { posix } from 'node:path'

/** Tool → policy action name used in config lists and grant keys. */
export const ACTIONS = {
  orca_status: 'status',
  orca_read: 'read',
  orca_grep: 'grep',
  orca_ls: 'ls',
  orca_grants: 'grants',
  orca_run: 'run',
  orca_launch: 'launch',
  orca_patch: 'patch',
  orca_rollback: 'rollback',
  orca_stop: 'stop',
}

/** Actions that never change remote state. `grants` only lists or revokes. */
export const READ_ONLY = new Set(['status', 'read', 'grep', 'ls', 'grants'])
/** Never remembered, whatever the config says: each stop is confirmed on its own. */
export const NEVER_REMEMBER = new Set(['stop'])

export const DEFAULT_POLICY = {
  mode: 'controlled',
  autoAllow: ['status', 'read', 'grep', 'ls', 'grants'],
  askOnce: ['run', 'launch'],
  alwaysAsk: ['patch', 'rollback', 'stop'],
  allowedCommands: [],
  deniedCommands: ['rm -rf /*', 'sudo *', 'shutdown*', 'reboot*', 'mkfs*', 'kill -9 -1*'],
  trustedWorkdirs: [],
  grantTtlDays: 7,
}

// Kept for callers that only need the command list.
export const DEFAULT_DENIED_COMMANDS = DEFAULT_POLICY.deniedCommands

/** Collapse runs of whitespace so patterns are not defeated by spacing. */
function normalize(command) {
  return command.trim().replace(/\s+/g, ' ')
}

/** Shell-style glob: `*` any run, `?` one char, everything else literal; anchored. */
export function globToRegExp(pattern) {
  let source = ''
  for (const ch of normalize(pattern)) {
    if (ch === '*') source += '.*'
    else if (ch === '?') source += '.'
    else source += ch.replace(/[\\^$.|+()[\]{}]/g, '\\$&')
  }
  return new RegExp(`^${source}$`, 's')
}

export function matchesAny(command, patterns = []) {
  const text = normalize(command)
  return patterns.some((pattern) => globToRegExp(pattern).test(text))
}

/**
 * A command is pre-approved only when every `;`, `&&`, `||`, `|` segment is,
 * so `git status; rm -rf x` never rides on an allowed `git status`.
 * Command substitution and redirections are never pre-approved.
 */
export function isPreApproved(command, allowed = []) {
  if (allowed.length === 0) return false
  if (/[`\n]|\$\(|[<>]/.test(command)) return false
  const segments = command.split(/&&|\|\||[;|&]/).map((part) => part.trim()).filter(Boolean)
  return segments.length > 0 && segments.every((segment) => matchesAny(segment, allowed))
}

/** Denied when the whole command or any segment matches. */
export function deniedReason(command, denied = []) {
  const candidates = [command, ...command.split(/&&|\|\||[;|&\n]/)]
  for (const candidate of candidates) {
    for (const pattern of denied) {
      if (candidate.trim() && globToRegExp(pattern).test(normalize(candidate))) {
        return `orca: command matches deniedCommands pattern ${JSON.stringify(pattern)}`
      }
    }
  }
  return undefined
}

/** Lexically normalized absolute workdir (`..` folded), or undefined. */
export function normalizeWorkdir(workdir) {
  if (typeof workdir !== 'string' || !workdir.startsWith('/')) return undefined
  const normalized = posix.normalize(workdir)
  return normalized.length > 1 ? normalized.replace(/\/$/, '') : normalized
}

/** With no trustedWorkdirs configured everything counts as trusted. */
export function isTrusted(workdir, trusted = []) {
  if (trusted.length === 0) return true
  if (workdir === undefined) return false
  return trusted.some((pattern) => {
    const re = globToRegExp(pattern)
    return re.test(workdir) || re.test(`${workdir}/`)
  })
}

/**
 * Resolve an action's tier from the configured lists (alwaysAsk > askOnce >
 * autoAllow). Unlisted read-only actions are auto, unlisted writes always ask,
 * and stop is never remembered or auto-allowed.
 */
export function tierOf(action, policy) {
  let tier
  if (policy.alwaysAsk.includes(action)) tier = 'alwaysAsk'
  else if (policy.askOnce.includes(action)) tier = 'askOnce'
  else if (policy.autoAllow.includes(action)) tier = 'auto'
  else tier = READ_ONLY.has(action) ? 'auto' : 'alwaysAsk'
  return NEVER_REMEMBER.has(action) && tier !== 'alwaysAsk' ? 'alwaysAsk' : tier
}

/** Summarize a Codex patch's file headers for the approval prompt. */
export function patchSummary(patch) {
  if (typeof patch !== 'string') return ''
  const files = []
  for (const line of patch.split(/\r\n|[\n\r]/)) {
    const match = /^\*\*\* (Add|Delete|Update) File: (.+)$/.exec(line.trim())
    if (match) files.push(`${match[1]} ${match[2].trim()}`)
  }
  const shown = files.slice(0, 8).join(', ')
  return files.length > 8 ? `${shown}, … (${files.length} files)` : shown
}

function describe(action, args) {
  const host = args.host
  const where = args.workdir ? `${host}:${args.workdir}` : host
  switch (action) {
    case 'run':
      return { en: `Run on ${where}: ${args.command}`, zh: `在 ${where} 上执行：${args.command}` }
    case 'launch':
      return { en: `Launch tmux job "${args.session}" on ${where}: ${args.command}`, zh: `在 ${where} 启动 tmux 任务「${args.session}」：${args.command}` }
    case 'patch': {
      const files = patchSummary(args.patch)
      return { en: `Patch files on ${where}: ${files}`, zh: `修改 ${where} 上的文件：${files}` }
    }
    case 'rollback':
      return { en: `Roll back patch ${args.patch_id} on ${host}${args.force ? ' (force)' : ''}`, zh: `在 ${host} 上回滚补丁 ${args.patch_id}${args.force ? '（强制）' : ''}` }
    case 'stop':
      return { en: `Stop job "${args.session}" on ${host}`, zh: `停止 ${host} 上的任务「${args.session}」` }
    default:
      return { en: `${action} on ${where}`, zh: `${where} 上的 ${action}` }
  }
}

/**
 * Decide one orca tool call.
 * @param {{name: string, arguments: object}} exec
 * @param {typeof DEFAULT_POLICY} policy - normalized policy.
 * @param {'ask' | 'never'} approvalPolicy - the session's effective approval policy.
 * @param {{has(host, workdir, action): boolean}} grants
 * @returns {{kind: 'pass'} | {kind: 'deny', reason: string}
 *   | {kind: 'ask', reason: string, displayReason: {en: string, zh: string}}
 *   | {kind: 'askOnce', grant: {host, workdir, action}, reason: string, displayReason: {en: string, zh: string}}}
 */
export function decide(exec, policy, approvalPolicy, grants) {
  const action = ACTIONS[exec.name]
  if (action === undefined) return { kind: 'pass' }
  // Explicit owner configuration, independent of the Harness session policy.
  // Continue the pipeline; do not override other plugins or engine validation.
  if (policy.mode === 'open') return { kind: 'pass' }
  const args = exec.arguments ?? {}

  if (action === 'run' || action === 'launch') {
    if (typeof args.command !== 'string') return { kind: 'pass' } // the engine rejects it
    const denial = deniedReason(args.command, policy.deniedCommands)
    if (denial) return { kind: 'deny', reason: denial }
  }

  // A dry run never touches the remote tree.
  if (action === 'patch' && args.dry_run === true) return { kind: 'pass' }

  let tier = tierOf(action, policy)
  if (tier === 'auto' && READ_ONLY.has(action)) return { kind: 'pass' }

  // Writes outside trustedWorkdirs always ask, even when configured as auto.
  const workdir = normalizeWorkdir(args.workdir)
  const trusted = isTrusted(workdir, policy.trustedWorkdirs)
  if (!trusted) tier = 'alwaysAsk'
  if (tier === 'auto') return { kind: 'pass' }

  if (action === 'run' && trusted && isPreApproved(args.command, policy.allowedCommands)) return { kind: 'pass' }

  const display = describe(action, args)
  const reason = `orca_${action}: ${display.en}`

  // DSH's `never` policy is strict headless: every approval request is denied
  // without prompting. Deny here as well instead of becoming a way around the
  // session policy; read-only work, pre-approved commands and an existing grant
  // all returned above, so only calls that would really prompt are affected.
  const deniedBySessionPolicy = () => ({
    kind: 'deny',
    reason: `orca_${action}: the session approval policy is "never" (strict headless — requests are denied without prompting); switch the session to "ask", or add the command to policy.allowedCommands`,
  })

  if (tier === 'askOnce' && workdir !== undefined && typeof args.host === 'string') {
    if (grants.has(args.host, workdir, action)) return { kind: 'pass' }
    if (approvalPolicy === 'never') return deniedBySessionPolicy()
    const days = policy.grantTtlDays
    return {
      kind: 'askOnce',
      grant: { host: args.host, workdir, action },
      reason,
      displayReason: {
        en: `Remembered approval for ${days} days: "${action}" in ${args.host}:${workdir}. The “Allow once” button also creates this grant, not just permission for this command. Revoke in Fleet → Remembered approvals.\n${display.en}`,
        zh: `记住授权 ${days} 天：${args.host}:${workdir} 下的 ${action} 操作。点击“允许一次”也会建立此授权，不仅批准当前命令；可在机群 → 已记住的授权中撤销。\n${display.zh}`,
      },
    }
  }

  if (approvalPolicy === 'never') return deniedBySessionPolicy()

  const untrustedNote = trusted ? null : { en: ' (outside trustedWorkdirs)', zh: '（不在 trustedWorkdirs 内）' }
  return {
    kind: 'ask',
    reason,
    displayReason: {
      en: display.en + (untrustedNote?.en ?? ''),
      zh: display.zh + (untrustedNote?.zh ?? ''),
    },
  }
}
