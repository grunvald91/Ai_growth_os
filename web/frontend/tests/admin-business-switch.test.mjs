import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

const raw = readFileSync(new URL('../src/hooks/useAdminBusiness.ts', import.meta.url), 'utf8')
const js = ts.transpileModule(raw, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText.replace(/^import .*;\s*$/gm, '').replace(/^export /gm, '')

function harness() {
  const data = new Map()
  const listeners = new Map()
  const requests = []
  const localStorage = {
    getItem(key) { return data.has(key) ? data.get(key) : null },
    setItem(key, value) { data.set(key, String(value)) },
    removeItem(key) { data.delete(key) },
  }
  const window = {
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, [])
      listeners.get(type).push(handler)
    },
    removeEventListener(type, handler) {
      listeners.set(type, (listeners.get(type) || []).filter(x => x !== handler))
    },
    dispatchEvent(event) {
      for (const handler of listeners.get(event.type) || []) handler(event)
    },
  }
  const api = { getAdminBusinesses() {
    return new Promise(resolve => {
      requests.push({ token: localStorage.getItem('token'), resolve })
    })
  } }
  const sandbox = {
    window, localStorage, api,
    Event: class { constructor(type) { this.type = type } },
    useState(initial) {
      let value = typeof initial === 'function' ? initial() : initial
      return [value, next => { value = typeof next === 'function' ? next(value) : next }]
    },
    useEffect(effect) { effect() },
  }
  vm.runInNewContext(js + '\n;globalThis.testHook = useAdminBusiness; globalThis.peek = () => ({ cachedToken, cachedBusinesses, cacheEpoch, fetchPromise, fetchToken });', sandbox)
  return { window, localStorage, requests, use: sandbox.testHook, peek: sandbox.peek }
}

async function settle() {
  for (let i = 0; i < 6; i++) await Promise.resolve()
}

test('late response from account A cannot overwrite B or clear B request', async () => {
  const h = harness()
  h.localStorage.setItem('token', 'token-A')
  h.use()
  assert.equal(h.requests.length, 1)
  h.localStorage.setItem('token', 'token-B')
  h.window.dispatchEvent({ type: 'servgo-account-changed' })
  h.use()
  assert.equal(h.requests.length, 2)
  assert.equal(h.requests[1].token, 'token-B')
  h.requests[0].resolve([{ id: 1, name: 'PRIVATE A' }])
  await settle()
  assert.equal(h.peek().cachedBusinesses, null)
  assert.notEqual(h.peek().fetchPromise, null)
  h.requests[1].resolve([{ id: 2, name: 'B ONLY' }])
  await settle()
  assert.equal(h.peek().cachedToken, 'token-B')
  assert.equal(h.peek().cachedBusinesses[0].name, 'B ONLY')
  assert.equal(h.localStorage.getItem('adminBusinessId'), '2')
})

test('logout prevents pending request from refilling the company list', async () => {
  const h = harness()
  h.localStorage.setItem('token', 'token-A')
  h.use()
  h.localStorage.removeItem('token')
  h.window.dispatchEvent({ type: 'servgo-logout' })
  h.requests[0].resolve([{ id: 1, name: 'PRIVATE A' }])
  await settle()
  assert.equal(h.peek().cachedBusinesses, null)
  assert.equal(h.peek().cachedToken, null)
})
