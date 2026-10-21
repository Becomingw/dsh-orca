/**
 * Host discovery for the panel.
 *
 * An empty `hosts` config means "every concrete alias in ~/.ssh/config" for the
 * tools, so the panel cannot treat it as "monitor nothing". Reading the file is
 * cheap and side-effect free; the engine still validates every alias again.
 */
import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

const MAX_PANEL_HOSTS = 12

/** `Host a b c` lines, minus wildcards, `!negations`, and bare IPs. */
export function discoverSshAliases(path = join(homedir(), '.ssh', 'config')) {
  let text
  try {
    text = readFileSync(path, 'utf8')
  } catch {
    return []
  }
  const aliases = []
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line.toLowerCase().startsWith('host ')) continue
    for (const token of line.slice(5).trim().split(/\s+/)) {
      if (!token || token.startsWith('!') || token.includes('*') || token.includes('?')) continue
      // Bare IPs are aliases here, but they are not machines worth polling by name.
      if (/^\d{1,3}(\.\d{1,3}){3}$/.test(token)) continue
      if (!aliases.includes(token)) aliases.push(token)
    }
  }
  return aliases
}

/** Numbered GPU boxes first (`12` before `HK_…`), then the rest alphabetically. */
export function orderForPanel(aliases) {
  const numbered = []
  const named = []
  for (const alias of aliases) {
    if (/^\d+$/.test(alias)) numbered.push(alias)
    else named.push(alias)
  }
  numbered.sort((a, b) => Number(a) - Number(b))
  named.sort((a, b) => a.localeCompare(b))
  return [...numbered, ...named]
}

/**
 * Which hosts the panel polls.
 *
 * `config.hosts` wins; otherwise every discovered alias, capped so a big
 * `~/.ssh/config` cannot turn the poller into a fleet-wide probe.
 */
export function panelHosts(configured, aliases) {
  if (Array.isArray(configured) && configured.length > 0) {
    return { hosts: [...configured], discovered: false }
  }
  const ordered = orderForPanel(aliases)
  return { hosts: ordered.slice(0, MAX_PANEL_HOSTS), discovered: true }
}

export { MAX_PANEL_HOSTS }
