/**
 * Bridge to the frozen Python engine: one `cli-json` worker process per call,
 * the same path upstream's MCP server uses internally.
 *
 * Contract (engine/remote_fleet_mcp.py `cli-json`): stdin carries
 * `{"tool", "arguments"}`; stdout carries exactly one JSON object.
 * Exit 0 is the result, exit 1 is `{"error"}` from validation or the remote
 * side, exit 2 is a malformed request or an engine crash (traceback on stderr).
 */
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'

export const ENGINE_PATH = fileURLToPath(new URL('../engine/remote_fleet_mcp.py', import.meta.url))

const MAX_STDOUT_BYTES = 8 * 1024 * 1024
const MAX_STDERR_BYTES = 64 * 1024
const ENV_PASSTHROUGH = ['HOME', 'USER', 'LOGNAME', 'PATH', 'SSH_AUTH_SOCK', 'LANG', 'LC_ALL', 'TMPDIR']
// Engine-side ceilings plus slack; the engine enforces the real deadlines.
const DEFAULT_TIMEOUT_MS = 5 * 60_000
const RUN_SLACK_MS = 60_000

export class EngineError extends Error {
  constructor(message, { code, stderr } = {}) {
    super(message)
    this.name = 'EngineError'
    this.code = code
    this.stderr = stderr
  }
}

/** Build the worker environment: an allowlist of the host env plus engine knobs. */
export function engineEnv(config = {}, base = process.env) {
  const env = {}
  for (const key of ENV_PASSTHROUGH) {
    if (base[key] !== undefined) env[key] = base[key]
  }
  // Finder-launched apps get a minimal PATH; make sure ssh and python resolve.
  env.PATH = [env.PATH, '/usr/bin', '/bin', '/usr/sbin', '/sbin'].filter(Boolean).join(':')
  env.PYTHONDONTWRITEBYTECODE = '1'
  if (Array.isArray(config.hosts) && config.hosts.length > 0) {
    env.REMOTE_FLEET_HOSTS = config.hosts.join(',')
  }
  if (config.sshPersistSeconds !== undefined) {
    env.REMOTE_FLEET_SSH_PERSIST = String(config.sshPersistSeconds)
  }
  return env
}

function timeoutFor(tool, args) {
  if (tool === 'remote_run') return (args.timeout_seconds ?? 60) * 1000 + RUN_SLACK_MS
  return DEFAULT_TIMEOUT_MS
}

/**
 * Run one engine tool call.
 * @param {string} tool - upstream tool name, e.g. `remote_status`.
 * @param {object} args - upstream argument names.
 * @param {{signal?: AbortSignal, python?: string, enginePath?: string, env?: object, timeoutMs?: number}} options
 * @returns {Promise<object>} the engine's JSON result.
 */
export function callEngine(tool, args, options = {}) {
  const {
    signal,
    python = 'python3',
    enginePath = ENGINE_PATH,
    env = engineEnv(),
    timeoutMs = timeoutFor(tool, args),
  } = options

  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason ?? new EngineError('aborted', { code: 'ABORTED' }))
      return
    }

    // detached: the worker leads its own process group, so one kill takes ssh with it.
    const child = spawn(python, ['-B', enginePath, 'cli-json'], {
      env,
      detached: true,
      stdio: ['pipe', 'pipe', 'pipe'],
    })

    const stdout = []
    let stdoutBytes = 0
    let stderr = ''
    let settled = false
    let killedFor

    const killGroup = (why) => {
      killedFor ??= why
      try {
        process.kill(-child.pid, 'SIGKILL')
      } catch {
        // already gone
      }
    }

    const finish = (fn, value) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      fn(value)
    }

    const onAbort = () => killGroup('aborted')
    signal?.addEventListener('abort', onAbort, { once: true })
    const timer = setTimeout(() => killGroup('timeout'), timeoutMs)

    child.stdout.on('data', (chunk) => {
      stdoutBytes += chunk.length
      if (stdoutBytes > MAX_STDOUT_BYTES) killGroup('oversize')
      else stdout.push(chunk)
    })
    child.stderr.on('data', (chunk) => {
      if (stderr.length < MAX_STDERR_BYTES) stderr += chunk.toString('utf8')
    })
    child.on('error', (error) => {
      finish(reject, new EngineError(`cannot start engine with ${python}: ${error.message}`, { code: 'SPAWN' }))
    })
    child.on('close', (code) => {
      if (killedFor === 'aborted') {
        finish(reject, signal?.reason ?? new EngineError('aborted', { code: 'ABORTED' }))
        return
      }
      if (killedFor === 'timeout') {
        finish(reject, new EngineError(`engine did not finish within ${Math.round(timeoutMs / 1000)}s`, { code: 'TIMEOUT', stderr }))
        return
      }
      if (killedFor === 'oversize') {
        finish(reject, new EngineError(`engine output exceeded ${MAX_STDOUT_BYTES} bytes`, { code: 'OVERSIZE' }))
        return
      }
      let payload
      try {
        payload = JSON.parse(Buffer.concat(stdout).toString('utf8'))
      } catch {
        finish(reject, new EngineError(`engine exited ${code} without JSON output${stderr ? `: ${stderr.trim().slice(-2000)}` : ''}`, { code: 'PROTOCOL', stderr }))
        return
      }
      if (code === 0) finish(resolve, payload)
      else if (code === 1) finish(reject, new EngineError(String(payload.error ?? 'remote error'), { code: 'TOOL', stderr }))
      else finish(reject, new EngineError(`engine failure: ${payload.error ?? `exit ${code}`}${stderr ? `\n${stderr.trim().slice(-2000)}` : ''}`, { code: 'ENGINE', stderr }))
    })

    child.stdin.on('error', () => {
      // The worker may exit before reading stdin (e.g. spawn failure); 'close' reports it.
    })
    child.stdin.end(JSON.stringify({ tool, arguments: args }))
  })
}
