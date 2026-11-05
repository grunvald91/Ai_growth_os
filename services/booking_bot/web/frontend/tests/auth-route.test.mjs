import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../src/utils/authRouteAccess.ts', import.meta.url), 'utf8')
const javascript = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText
const { routeAccess } = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`)

test('an owner with a slow shared-cookie session waits instead of being redirected to login', () => {
  assert.equal(routeAccess(null, null, true, true, false), 'wait')
  assert.equal(routeAccess('restored', 'owner', true, false, false), 'allow')
})

test('public-page timeout does not authenticate missing or wrong-role tokens', () => {
  assert.equal(routeAccess(null, null, true, false, false), 'login')
  assert.equal(routeAccess('client', 'client', true, true, false), 'wait')
  assert.equal(routeAccess('client', 'client', true, false, false), 'login')
  assert.equal(routeAccess('client', 'client', false, false, false), 'allow')
})

test('explicit logout never restores an old shared-cookie session', () => {
  assert.equal(routeAccess(null, null, true, true, true), 'login')
  assert.equal(routeAccess('stale', 'owner', true, true, true), 'login')
})
