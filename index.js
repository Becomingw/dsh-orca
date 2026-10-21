/**
 * dsh-orca host plugin: remote-fleet's remote execution as native DSH tools.
 * Local decisions, remote execution; see README.md and docs/.
 */
import { callEngine, engineEnv, ENGINE_PATH } from './lib/engine.js'
import { FleetCache } from './lib/cache.js'
import { DAY_MS, defaultGrantsPath, GrantStore } from './lib/grants.js'
import { Observations } from './lib/observations.js'
import { ACTIONS, decide, DEFAULT_POLICY, deniedReason } from './lib/policy.js'
import { createTools } from './lib/tools.js'

export const name = 'orca'
export const inject = ['tools']

const HOST_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/
const KNOWN_ACTIONS = new Set(Object.values(ACTIONS))

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

function normalizePolicy(policy = {}) {
  const normalized = {}
  for (const key of ['autoAllow', 'askOnce', 'alwaysAsk']) {
    normalized[key] = stringList(policy[key], DEFAULT_POLICY[key], `config.policy.${key}`)
    for (const action of normalized[key]) {
      if (!KNOWN_ACTIONS.has(action)) throw new TypeError(`orca: unknown action ${JSON.stringify(action)} in config.policy.${key}`)
    }
  }
  for (const key of ['allowedCommands', 'deniedCommands', 'trustedWorkdirs']) {
    normalized[key] = stringList(policy[key], DEFAULT_POLICY[key], `config.policy.${key}`)
  }
  normalized.grantTtlDays = integer(policy.grantTtlDays, DEFAULT_POLICY.grantTtlDays, 'config.policy.grantTtlDays', 1, 90)
  return normalized
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
  return {
    hosts,
    python: typeof config.python === 'string' && config.python ? config.python : 'python3',
    enginePath: typeof config.enginePath === 'string' && config.enginePath ? config.enginePath : ENGINE_PATH,
    sshPersistSeconds: config.sshPersistSeconds,
    grantsPath: typeof config.grantsPath === 'string' && config.grantsPath ? config.grantsPath : defaultGrantsPath(),
    policy: normalizePolicy(config.policy),
    logging: normalizeLogging(config.logging),
    panel: normalizePanel(config.panel),
  }
}

/**
 * Ask through DSH's approval service ourselves so the outcome is known and an
 * askOnce grant can be recorded. Returns a pre-execute decision.
 */
async function askAndRemember(approval, exec, decision, grants, next) {
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
  const env = engineEnv(config)
  const grants = new GrantStore(config.grantsPath, { ttlMs: config.policy.grantTtlDays * DAY_MS })
  const call = (tool, args, signal) =>
    callEngine(tool, args, { signal, env, python: config.python, enginePath: config.enginePath })

  const observations = new Observations()
  for (const tool of createTools(call, { hosts: config.hosts, orcaLog: config.logging, grants, observations })) {
    ctx.tools.register(tool)
  }

  const approvalPolicy = (exec) => {
    const approval = ctx.get('approval')
    if (!approval || !exec.agent) return 'ask'
    return approval.effectivePolicy(exec.agent.session)
  }

  ctx.on('tools/pre-execute', async (exec, next) => {
    if (!(exec.name in ACTIONS)) return next()
    const decision = decide(exec, config.policy, approvalPolicy(exec), grants)
    if (decision.kind === 'pass') return next()
    if (decision.kind === 'askOnce') return askAndRemember(ctx.get('approval'), exec, decision, grants, next)
    return decision
  })

  // Monotonic backstop: no later listener can turn a denied command into an allow.
  ctx.tools.guard((exec) => {
    if (exec.name !== 'orca_run' && exec.name !== 'orca_launch') return undefined
    const command = exec.arguments?.command
    return typeof command === 'string' ? deniedReason(command, config.policy.deniedCommands) : undefined
  })

  mountPanel(ctx, config, call)
}

/** JSON helper for the panel routes (same-origin, loopback-only server). */
function sendJson(response, status, value) {
  const body = JSON.stringify(value)
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
  })
  response.end(body)
}

/**
 * The Orca panel: one cached snapshot route plus a force-refresh.
 *
 * Polling lives on the host so opening the panel costs no SSH, and shared
 * training hosts only see one scheduled probe per interval.
 */
function mountPanel(ctx, config, call) {
  const webServer = (() => {
    try {
      return ctx.get?.('webServer') ?? ctx.webServer
    } catch {
      return undefined
    }
  })()
  if (webServer === undefined || config.hosts.length === 0) return

  const cache = new FleetCache({
    hosts: config.hosts,
    pollSeconds: config.panel.pollSeconds,
    jobLimit: config.panel.jobLimit,
    call: (tool, args) => call(tool, args),
  })

  ctx.effect(() => {
    cache.start()
    const disposers = [
      webServer.register({
        kind: 'exact',
        path: '/dsh-orca/summary',
        handler: async (request, response) => {
          if (request.method !== 'GET' && request.method !== 'POST') {
            response.writeHead(405, { allow: 'GET, POST' })
            response.end()
            return
          }
          try {
            const url = new URL(request.url ?? '/', 'http://127.0.0.1')
            if (url.searchParams.get('refresh') === '1' || request.method === 'POST') {
              await cache.refresh()
            }
            sendJson(response, 200, {
              ...cache.snapshot(),
              config: {
                hosts: config.hosts,
                pollSeconds: config.panel.pollSeconds,
                jobLimit: config.panel.jobLimit,
                allowedCommands: config.policy.allowedCommands,
                trustedWorkdirs: config.policy.trustedWorkdirs,
              },
            })
          } catch (error) {
            sendJson(response, 500, { error: error instanceof Error ? error.message : String(error) })
          }
        },
      }),
    ]
    return () => {
      cache.stop()
      for (const dispose of disposers) {
        try {
          dispose()
        } catch {
          // already gone
        }
      }
    }
  })
}

