import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { discoverSshAliases, MAX_PANEL_HOSTS, orderForPanel, panelHosts } from '../lib/discovery.js'

function withConfig(text) {
  const dir = mkdtempSync(join(tmpdir(), 'orca-ssh-'))
  const path = join(dir, 'config')
  writeFileSync(path, text)
  try {
    return discoverSshAliases(path)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

test('discovery keeps concrete aliases and drops wildcards, negations and IPs', () => {
  const aliases = withConfig([
    'Host 13  Gpu13',
    '  HostName 10.0.0.13',
    'Host 16',
    'Host *',
    'Host 1?.?.?',
    'Host !blocked 21',
    'Host 154.9.24.14',
    '# Host commented-out',
    'Host HK_bigji_yuyun',
  ].join('\n'))
  assert.deepEqual(aliases, ['13', 'Gpu13', '16', '21', 'HK_bigji_yuyun'])
})

test('discovery tolerates a missing file', () => {
  assert.deepEqual(discoverSshAliases('/nonexistent/orca/ssh/config'), [])
})

test('panel ordering puts numbered GPU hosts first, numerically', () => {
  assert.deepEqual(orderForPanel(['HK_x', '16', '9', 'zeta', '10']), ['9', '10', '16', 'HK_x', 'zeta'])
})

test('configured hosts win; discovery is capped', () => {
  const configured = panelHosts(['13', '16'], ['21', '17'])
  assert.deepEqual(configured, { hosts: ['13', '16'], discovered: false })

  const many = Array.from({ length: MAX_PANEL_HOSTS + 5 }, (_, index) => String(index + 1))
  const discovered = panelHosts([], many)
  assert.equal(discovered.discovered, true)
  assert.equal(discovered.hosts.length, MAX_PANEL_HOSTS)
  assert.deepEqual(discovered.hosts.slice(0, 3), ['1', '2', '3'])
})
