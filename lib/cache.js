/**
 * Fleet status cache for the Orca panel.
 *
 * The panel must never trigger SSH on open: the host polls on its own schedule
 * and the HTTP route only reads this snapshot. Polling is cheap on purpose —
 * `log_tail_lines: 0` and a small `job_limit` — because the target hosts are
 * shared training machines.
 */

/** Per-host entry kept between polls. */
function emptyEntry() {
  return { at: 0, ok: false, data: undefined, error: 'not polled yet' }
}

/** Compact a host's `remote_status` payload into what the panel renders. */
function summarizeHost(data) {
  const gpus = Array.isArray(data.gpus) ? data.gpus : []
  const util = Array.isArray(data.gpu_utilization_percent) ? data.gpu_utilization_percent : []
  const memUsed = Array.isArray(data.gpu_memory_used_mib) ? data.gpu_memory_used_mib : []
  const gpu = gpus.length > 0
    ? {
        count: gpus.length,
        model: data.gpu_model ?? gpus[0].model ?? null,
        utilAvg: gpus.length > 0
          ? Math.round(gpus.reduce((sum, item) => sum + (item.utilization_percent ?? 0), 0) / gpus.length)
          : null,
        memUsedMib: gpus.reduce((sum, item) => sum + (item.memory_used_mib ?? 0), 0),
        memTotalMib: gpus.reduce((sum, item) => sum + (item.memory_total_mib ?? 0), 0),
      }
    : util.length > 0
      ? {
          count: util.length,
          model: data.gpu_model ?? null,
          utilAvg: Math.round(util.reduce((sum, item) => sum + (item ?? 0), 0) / util.length),
          memUsedMib: memUsed.reduce((sum, item) => sum + (item ?? 0), 0),
          memTotalMib: null,
        }
      : null

  const jobs = (Array.isArray(data.jobs) ? data.jobs : []).map((job) => ({
    session: job.session ?? job.job_id ?? '?',
    jobId: job.job_id ?? null,
    state: job.state ?? (job.active ? 'running' : 'unknown'),
    active: job.active === true,
    startedAt: job.started_at ?? null,
    finishedAt: job.finished_at ?? null,
    exitCode: job.exit_code ?? null,
    logPath: job.log_path ?? null,
  }))

  return {
    hostname: data.hostname ?? data.host ?? null,
    load: Array.isArray(data.load_average) ? data.load_average.slice(0, 2) : null,
    gpu,
    disk: data.disk ?? null,
    memory: data.memory ?? null,
    running: jobs.filter((job) => job.active).length,
    jobs,
  }
}

export class FleetCache {
  /**
   * @param {{hosts: string[], pollSeconds: number, jobLimit: number,
   *          call: (tool: string, args: object) => Promise<any>}} options
   */
  constructor({ hosts, pollSeconds, jobLimit, call }) {
    this.hosts = [...hosts]
    this.pollSeconds = pollSeconds
    this.jobLimit = jobLimit
    this.call = call
    this.entries = new Map(this.hosts.map((host) => [host, emptyEntry()]))
    this.timer = undefined
    this.inflight = undefined
    this.rounds = 0
    this.lastRoundAt = 0
  }

  async pollHost(host) {
    try {
      const data = await this.call('remote_status', {
        host,
        job_limit: this.jobLimit,
        log_tail_lines: 0,
      })
      this.entries.set(host, { at: Date.now(), ok: true, data: summarizeHost(data), error: null })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      this.entries.set(host, { at: Date.now(), ok: false, data: undefined, error: message })
    }
  }

  /** Poll every host once. Concurrent callers share the round in flight. */
  refresh() {
    if (this.hosts.length === 0) return Promise.resolve()
    if (this.inflight !== undefined) return this.inflight
    this.inflight = Promise.all(this.hosts.map((host) => this.pollHost(host)))
      .finally(() => {
        this.inflight = undefined
        this.rounds += 1
        this.lastRoundAt = Date.now()
      })
    return this.inflight
  }

  start() {
    if (!(this.pollSeconds > 0) || this.timer !== undefined) return
    void this.refresh()
    this.timer = setInterval(() => { void this.refresh() }, this.pollSeconds * 1000)
    this.timer.unref?.()
  }

  stop() {
    if (this.timer !== undefined) {
      clearInterval(this.timer)
      this.timer = undefined
    }
  }

  /** The panel payload: cached values only, no I/O. */
  snapshot() {
    const hosts = this.hosts.map((host) => {
      const entry = this.entries.get(host) ?? emptyEntry()
      return {
        host,
        at: entry.at || null,
        ok: entry.ok,
        error: entry.error,
        ...(entry.data ?? {}),
      }
    })
    const jobs = hosts
      .flatMap((host) => (host.jobs ?? []).map((job) => ({ ...job, host: host.host })))
      .sort((a, b) => String(b.startedAt ?? '').localeCompare(String(a.startedAt ?? '')))
      .slice(0, 25)
    return {
      generatedAt: Date.now(),
      pollSeconds: this.pollSeconds,
      rounds: this.rounds,
      lastRoundAt: this.lastRoundAt || null,
      polling: this.timer !== undefined,
      hosts,
      jobs,
    }
  }
}
