import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { test } from 'node:test'
import { ENGINE_PATH } from '../lib/engine.js'
import { createTools, ENGINE_TOOL, parameterSchemas, renderGrep, renderPatch, renderRead, renderRun } from '../lib/tools.js'

// DSH's tool registry accepts only this subset (dsh-tools assertSupportedJsonSchema).
const DSH_KEYWORDS = new Set([
  'type', 'oneOf', 'properties', 'required', 'additionalProperties', 'items', 'enum', 'const',
  'description', 'title', 'default', 'examples',
])

function engineSchemas() {
  const script = [
    'import importlib.util, json, sys',
    's = importlib.util.spec_from_file_location("e", sys.argv[1])',
    'e = importlib.util.module_from_spec(s); s.loader.exec_module(e)',
    'print(json.dumps({t["name"]: t["inputSchema"] for t in e.TOOLS}))',
  ].join('\n')
  return JSON.parse(execFileSync('python3', ['-B', '-c', script, ENGINE_PATH], { encoding: 'utf8' }))
}

function walk(schema, visit, path = 'schema') {
  visit(schema, path)
  for (const [key, child] of Object.entries(schema.properties ?? {})) walk(child, visit, `${path}.${key}`)
  if (schema.items) walk(schema.items, visit, `${path}.items`)
}

test('schemas use only keywords DSH accepts', () => {
  for (const [name, schema] of Object.entries(parameterSchemas(['13', '16']))) {
    walk(schema, (node, path) => {
      for (const key of Object.keys(node)) assert.ok(DSH_KEYWORDS.has(key), `${name} ${path}.${key}`)
      // DSH only accepts a boolean here.
      if ('additionalProperties' in node) assert.equal(typeof node.additionalProperties, 'boolean', `${name} ${path}`)
    })
  }
})

test('schemas match the engine on names, types, defaults, enums and required', () => {
  const engine = engineSchemas()
  for (const [name, ours] of Object.entries(parameterSchemas())) {
    // orca-only tools have no upstream MCP schema; their handlers validate.
    if (!ENGINE_TOOL[name]?.startsWith('remote_')) continue
    const theirs = engine[ENGINE_TOOL[name]]
    assert.ok(theirs, `engine lacks ${ENGINE_TOOL[name]}`)
    assert.deepEqual(Object.keys(ours.properties).sort(), Object.keys(theirs.properties).sort(), name)
    assert.deepEqual([...ours.required].sort(), [...theirs.required].sort(), name)
    for (const [key, prop] of Object.entries(ours.properties)) {
      const other = theirs.properties[key]
      assert.equal(prop.type, other.type, `${name}.${key} type`)
      assert.deepEqual(prop.default, other.default, `${name}.${key} default`)
      assert.deepEqual(prop.enum, other.enum, `${name}.${key} enum`)
    }
  }
})

test('configured hosts become an enum', () => {
  assert.deepEqual(parameterSchemas(['13', '16']).orca_run.properties.host.enum, ['13', '16'])
  assert.equal(parameterSchemas([]).orca_run.properties.host.enum, undefined)
})

test('run renders an exit-code marker after output', () => {
  const [{ text }] = renderRun({ exit_code: 3, stdout: 'out', stderr: 'err\n', timed_out: true, truncated: false })
  assert.equal(text, 'out\n[stderr]\nerr\n[exit code: 3; timed out]')
  assert.equal(renderRun({ exit_code: 0, stdout: '', stderr: '' })[0].text, '[exit code: 0]')
})

test('read renders paging cursors in a header line', () => {
  const [{ text }] = renderRead({
    path: '/x.log', content: 'a\nb\n', first_line: 1, last_line: 2, total_lines: 9,
    next_start_line: 3, next_offset_bytes: 4, eof_reached: false, size_bytes: 18,
  })
  assert.equal(text, '[/x.log; lines 1-2 of 9; next_start_line=3; next_offset_bytes=4; 18 bytes]\na\nb\n')
})

test('grep renders matches with context line numbers', () => {
  const [{ text }] = renderGrep({ files_matched: 1, files_scanned: 2, matches: [{ path: '/a.py', line: 5, text: 'raise X', before: ['b'], after: ['c'] }] })
  assert.equal(text, '[1 matches in 1/2 files]\n/a.py-4- b\n/a.py:5: raise X\n/a.py-6- c')
})

test('patch renders every state distinctly', () => {
  const render = (value) => renderPatch(value)[0].text
  assert.match(render({ mode: 'apply', ok: true, state: 'applied', patch_id: 'orca-1', files: [{ action: 'update', path: 'a', added: 1, removed: 1, matches: [{ hunk: 1, line: 4, level: 'strip', also_matches_lines: [9] }] }] }), /patch_id=orca-1[\s\S]*hunk 1 at line 4 \(strip match\) — also matches lines 9/)
  assert.match(render({ mode: 'apply', ok: false, state: 'conflict', conflicts: [{ path: 'a', message: 'context not found' }] }), /NOT applied: conflicts[\s\S]*conflict a/)
  assert.match(render({ mode: 'apply', ok: false, state: 'partial', patch_id: 'p9', error: 'x' }), /PARTIALLY APPLIED — run orca_rollback patch_id=p9/)
  assert.match(render({ mode: 'apply', ok: true, state: 'already_applied', patch_id: 'p9' }), /already applied/)
  assert.match(render({ mode: 'apply', ok: true, state: 'applied', patch_id: 'p9', uncertain: 'timed out' }), /reply was lost: timed out/)
  assert.match(render({ mode: 'dry_run', ok: false, state: 'conflict', conflicts: [] }), /dry run: patch does NOT apply/)
})

test('launch passes the configured structured-log options to the engine', async () => {
  const calls = []
  const tools = createTools(async (tool, args) => { calls.push([tool, args]); return {} }, { orcaLog: { tail_lines: 9 } })
  await tools.find((t) => t.name === 'orca_launch').execute({ host: 'h' }, {})
  await tools.find((t) => t.name === 'orca_run').execute({ host: 'h' }, {})
  assert.deepEqual(calls, [['remote_launch_tmux', { host: 'h', orca_log: { tail_lines: 9 } }], ['remote_run', { host: 'h' }]])
})

// Optional: validate against DSH's own schema checker when a dsh-tools copy is available
// (ORCA_DSH_TOOLS=<path to @deepseek-ai/dsh-tools/lib/index.js>).
test('DSH accepts every tool schema', { skip: !process.env.ORCA_DSH_TOOLS }, async () => {
  const dsh = await import(process.env.ORCA_DSH_TOOLS)
  for (const tool of createTools(async () => ({}), { hosts: ['13'], grants: { active: () => [], revoke: () => [] } })) {
    dsh.assertSupportedJsonSchema(tool.parameters)
    dsh.assertSupportedJsonSchema(tool.output.schema)
  }
  const patch = createTools(async () => ({})).find((t) => t.name === 'orca_patch')
  const errors = dsh.validateJsonSchemaValue(patch.parameters, { host: 'h', workdir: '/w', patch: 'p', expected_sha256: { 'a.py': null, 'b.py': 'ab' } }, '')
  assert.deepEqual(errors, [])
})
