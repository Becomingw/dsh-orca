import { withObservedHashes } from './observations.js'

export const denial = reason => ({ kind: 'deny', reason, info: { name: 'OrcaPolicyDenied', code: 'ORCA_DENIED', reason } })

/** Preview exactly the hashes an apply will use, without teaching away a stale read. */
export async function preflightPatch(call, exec, decision, observations, timeoutMs = 20000) {
  const { args } = withObservedHashes(observations, exec.arguments)
  try {
    const signal = AbortSignal.any([exec.signal, AbortSignal.timeout(timeoutMs)])
    const result = await call('orca_patch', { ...args, dry_run: true }, signal)
    if (signal.aborted) throw signal.reason
    if (result.state === 'conflict' || result.ok === false) {
      const detail = (result.conflicts ?? []).map(c => `${c.path}: ${c.message}`).join('\n')
      return denial(`Patch preflight failed: ${detail || result.error || result.state}`)
    }
    const files = result.files ?? []
    const detail = files.slice(0, 8).map(f => `${f.path} +${f.added ?? 0} −${f.removed ?? 0}`).join(', ')
    const where = `${args.host}:${args.workdir}`
    return { ...decision, displayReason: {
      en: `Modify ${files.length} files on ${where}: ${detail}${files.length > 8 ? ' …' : ''}\nPreview passed; files are checked again on apply.`,
      zh: `在 ${where} 修改 ${files.length} 个文件：${detail}${files.length > 8 ? ' …' : ''}\n预检通过；执行时会再次核对文件。`,
    } }
  } catch (error) {
    if (exec.signal.aborted) return { kind: 'cancel' }
    return { ...decision, displayReason: {
      en: `${decision.displayReason.en}\nPreview unavailable: ${String(error.message).slice(0, 300)}`,
      zh: `${decision.displayReason.zh}\n未能预检：${String(error.message).slice(0, 300)}`,
    } }
  }
}
