/**
 * dsh-orca host plugin: remote-fleet's remote execution as native DSH tools.
 * Local decisions, remote execution; see README.md and docs/.
 */
import { callEngine, engineEnv, ENGINE_PATH } from './lib/engine.js'
import { FleetCache } from './lib/cache.js'
import { discoverSshAliases, panelHosts } from './lib/discovery.js'
import { DAY_MS, defaultGrantsPath, GrantStore } from './lib/grants.js'
import { Observations } from './lib/observations.js'
import { ACTIONS, decide, deniedReason } from './lib/policy.js'
import { createTools } from './lib/tools.js'
import { registerRoutes } from './lib/routes.js'
import { denial, preflightPatch } from './lib/approval.js'

import { dirname, join } from 'node:path'
import { normalizePolicy, PolicySettings } from './lib/settings.js'

export const name = 'orca'
export const inject = ['tools']

const HOST_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/

function stringList(value, fallback, label) {
  const list = value ?? fallback
  if (!Array.isArray(list) || !list.every((item) => typeof item === 'string')) {
    throw new TypeError(`orca: ${label} must be a list of strings`)
  }
  return list
}

function integer(value, fallback, label, min, max) {
  const result = value ?? fallback
  if (!Number.isInteger(result) || result < min || result > max) {
    throw new TypeError(`orca: ${label} must be an integer in ${min}..${max}`)
  }
  return result
}

function normalizeLogging(logging = {}) {
  if (logging.structured === false) return null
  return {
    tail_lines: integer(logging.tailLines, 2000, 'config.logging.tailLines', 10, 20000),
    rotate_bytes: integer(logging.rotateBytes, 32 << 20, 'config.logging.rotateBytes', 0, 2 ** 34),
    keep_segments: integer(logging.keepSegments, 5, 'config.logging.keepSegments', 1, 1000),
  }
}

/** Panel polling: 0 disables the background probe (refresh stays manual). */
function normalizePanel(panel = {}) {
  return {
    pollSeconds: integer(panel.pollSeconds, 120, 'config.panel.pollSeconds', 0, 3600),
    jobLimit: integer(panel.jobLimit, 5, 'config.panel.jobLimit', 1, 50),
  }
}

/** Validate and default the plugin row's `config` (no schemastery: plain JS keeps tests standalone). */
export function normalizeConfig(raw = {}) {
  const config = raw ?? {}
  const hosts = stringList(config.hosts, [], 'config.hosts')
  for (const host of hosts) {
    if (!HOST_PATTERN.test(host)) throw new TypeError(`orca: invalid host alias ${JSON.stringify(host)}`)
  }
  if (config.sshPersistSeconds !== undefined) integer(config.sshPersistSeconds, 0, 'config.sshPersistSeconds', 0, 86400)
  const grantsPath = typeof config.grantsPath === 'string' && config.grantsPath ? config.grantsPath : defaultGrantsPath()
  return {
    hosts,
    python: typeof config.python === 'string' && config.python ? config.python : 'python3',
    enginePath: typeof config.enginePath === 'string' && config.enginePath ? config.enginePath : ENGINE_PATH,
    sshPersistSeconds: config.sshPersistSeconds,
    grantsPath,
    policy: normalizePolicy(config.policy),
    policyPath: typeof config.policyPath === 'string' && config.policyPath ? config.policyPath : join(dirname(grantsPath), 'policy.json'),
    logging: normalizeLogging(config.logging),
    panel: normalizePanel(config.panel),
  }
}

/**
 * Ask through DSH's approval service ourselves so the outcome is known and an
 * askOnce grant can be recorded. Returns a pre-execute decision.
 */
async function askAndRemember(approval, exec, decision, grants, next, ttlMs) {
  if (approval === undefined || exec.agent === undefined) {
    return { kind: 'deny', reason: `${exec.name} requires approval, but no approval channel is available` }
  }
  const outcome = await approval.request({
    agent: exec.agent,
    toolName: exec.name,
    callId: exec.callId,
    reason: decision.reason,
    displayReason: decision.displayReason,
    signal: exec.signal,
  })
  switch (outcome) {
    case 'allowed-once':
      // Use the duration shown in this approval, even if settings changed while waiting.
      grants.ttlMs = ttlMs
      grants.grant(decision.grant.host, decision.grant.workdir, decision.grant.action)
      return next()
    case 'rejected':
      return { kind: 'deny', reason: `the user rejected ${exec.name}; do not retry it another way, explain instead` }
    case 'cancelled':
      return { kind: 'cancel' }
    default:
      return { kind: 'deny', reason: `${exec.name} requires approval, but no approval channel is available` }
  }
}

export function apply(ctx, rawConfig) {
  const config = normalizeConfig(rawConfig)
  const settings = new PolicySettings(config.policyPath, config.policy)
  const env = engineEnv(config)
  const grants = new GrantStore(config.grantsPath, { ttlMs: config.policy.grantTtlDays * DAY_MS })
  const call = (tool, args, signal) =>
    callEngine(tool, args, { signal, env, python: config.python, enginePath: config.enginePath })

  const observations = new Observations()
  const tools = new Map(createTools(call, { hosts: config.hosts, orcaLog: config.logging, grants, observations }).map(tool => [tool.name, tool]))
  for (const tool of tools.values()) ctx.tools.register(tool)

  const approvalPolicy = (exec) => {
    const approval = ctx.get('approval')
    if (!approval || !exec.agent) return 'ask'
    return approval.effectivePolicy(exec.agent.session)
  }

  ctx.on('tools/pre-execute', async (exec, next) => {
    if (!(exec.name in ACTIONS)) return next()
    let policy
    try { policy = settings.snapshot().policy } catch { return denial('Orca policy settings could not be read; repair the settings file before executing tools.') }
    let decision = decide(exec, policy, approvalPolicy(exec), grants)
    if (decision.kind === 'pass') return next()
    if (decision.kind === 'deny') return denial(decision.reason)
    if (exec.name === 'orca_patch' && decision.kind === 'ask') decision = await preflightPatch(call, exec, decision, observations)
    if (decision.kind === 'askOnce') return askAndRemember(ctx.get('approval'), exec, decision, grants, next, policy.grantTtlDays * DAY_MS)
    return decision
  })

  // Controlled-mode backstop. Open mode leaves decisions to the remaining pipeline.
  ctx.tools.guard((exec) => {
    if (exec.name !== 'orca_run' && exec.name !== 'orca_launch') return undefined
    let policy
    try { policy = settings.snapshot().policy } catch { return 'Orca policy settings could not be read' }
    if (policy.mode === 'open') return undefined
    const command = exec.arguments?.command
    return typeof command === 'string' ? deniedReason(command, policy.deniedCommands) : undefined
  })

  const mount = scope => {
    const selected = panelHosts(config.hosts, discoverSshAliases())
    const cache = new FleetCache({ hosts: selected.hosts, pollSeconds: config.panel.pollSeconds,
      jobLimit: config.panel.jobLimit, call: (tool, args) => call(tool, args) })
    registerRoutes(scope, { cache, config, selected, tools, settings })
  }
  if (typeof ctx.inject === 'function') ctx.inject(['connection'], mount)
  else if (ctx.get('connection')) mount(ctx)
}

