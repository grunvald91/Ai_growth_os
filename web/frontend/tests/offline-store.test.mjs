import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { webcrypto } from 'node:crypto'
import { TextEncoder } from 'node:util'
import ts from 'typescript'

const source = readFileSync(new URL('../src/utils/offlineStore.ts', import.meta.url), 'utf8')
const js = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

function fakeIndexedDb() {
  const rows = new Map()
  let storeExists = false
  let deletionCount = 0
  function transaction() {
    let pending = 0
    const tx = { oncomplete: null, onerror: null, onabort: null, error: null, abort() {} }
    const enqueue = (task) => {
      pending++
      queueMicrotask(() => {
        task()
        pending--
        if (pending === 0) queueMicrotask(() => tx.oncomplete?.())
      })
    }
    const store = {
      put(row) { enqueue(() => rows.set(row.id, row)); return {} },
      delete(id) { enqueue(() => rows.delete(id)); return {} },
      get(id) {
        const req = { result: undefined, onsuccess: null }
        enqueue(() => { req.result = rows.get(id); req.onsuccess?.() })
        return req
      },
      openCursor() {
        const req = { result: undefined, onsuccess: null }
        const entries = [...rows.entries()]
        let offset = 0
        const next = () => {
          if (offset === entries.length) { req.result = null; req.onsuccess?.(); return }
          const [id, value] = entries[offset++]
          req.result = {
            value,
            delete() { rows.delete(id) },
            continue() { enqueue(next) },
          }
          req.onsuccess?.()
        }
        enqueue(next)
        return req
      },
    }
    tx.objectStore = () => store
    return tx
  }
  const db = {
    objectStoreNames: { contains: () => storeExists },
    createObjectStore: () => { storeExists = true },
    transaction,
    close() {},
  }
  return {
    rows,
    get deletionCount() { return deletionCount },
    indexedDB: {
      open() {
        const req = { result: db, onupgradeneeded: null, onsuccess: null, onerror: null, onblocked: null }
        queueMicrotask(() => {
          if (!storeExists) req.onupgradeneeded?.()
          req.onsuccess?.()
        })
        return req
      },
      deleteDatabase() {
        const req = { onsuccess: null, onerror: null, onblocked: null }
        queueMicrotask(() => {
          rows.clear()
          deletionCount++
          req.onsuccess?.()
        })
        return req
      },
    },
  }
}

function harness() {
  const db = fakeIndexedDb()
  const fields = new Map()
  let now = 1_700_000_000_000
  const exports = {}
  const events = []
  const localStorage = {
    getItem(name) { return fields.get(name) ?? null },
    setItem(name, value) { fields.set(name, value) },
    removeItem(name) { fields.delete(name) },
  }
  const FakeDate = class extends Date { static now() { return now } }
  runInNewContext(js, {
    exports, indexedDB: db.indexedDB, localStorage,
    crypto: webcrypto, TextEncoder, Uint8Array,
    URL, Date: FakeDate,
    window: { location: { origin: 'https://servgo.ru' }, dispatchEvent(event) { events.push(event) } },
    CustomEvent: class { constructor(type, opts) { this.type = type; this.detail = opts.detail } },
    console,
  })
  return { code: exports, db, localStorage, events, advance(ms) { now += ms } }
}

test('only explicit schedule and booking GET resources are eligible for snapshots', () => {
  const { code } = harness()
  assert.equal(code.OFFLINE_RETENTION_MS, 7 * 24 * 60 * 60 * 1000)
  for (const path of [
    '/admin/bookings?business_id=42&date=2026-10-31',
    '/admin/businesses', '/admin/businesses/42/services',
    '/admin/businesses/42/employees', '/master/schedule?date_from=2026-10-31',
    '/master/me', '/my/bookings',
  ]) assert.equal(code.canSaveOffline(path), true, path)
  for (const path of ['/api/owner-mail/inbox', '/studio/products',
    '/auth/session', '/admin/businesses/42/contacts', '/admin/bookings/42/status']) {
    assert.equal(code.canSaveOffline(path), false, path)
  }
})

test('seven-day data expiry, same-login access and logout deletion', async () => {
  const h = harness()
  const path = '/admin/bookings?business_id=42&date=2026-10-31'
  const data = [{ id: 5, client_name: 'Example', client_phone: '+70000000000', notes: 'Comment' }]
  h.localStorage.setItem('token', 'login-A')
  await h.code.saveOfflineSnapshot(path, data, 'login-A')
  const hit = await h.code.loadOfflineSnapshot(path, 'login-A')
  assert.deepEqual(JSON.stringify(hit.data), JSON.stringify(data))
  h.localStorage.setItem('token', 'login-B')
  assert.equal(await h.code.loadOfflineSnapshot(path, 'login-B'), null, 'different login cannot read A')
  assert.equal(await h.code.loadOfflineSnapshot(path, 'login-A'), null, 'stale token rejected')
  h.localStorage.setItem('token', 'login-A')
  h.advance(7 * 24 * 60 * 60 * 1000 + 1)
  assert.equal(await h.code.loadOfflineSnapshot(path, 'login-A'), null, 'expired after seven days')
  await h.code.saveOfflineSnapshot(path, data, 'login-A')
  assert.equal(h.db.rows.size, 1)
  await h.code.clearOfflineSnapshots()
  assert.equal(h.db.rows.size, 0, 'logout removes stored personal data')
})

test('online weekly schedule becomes individually readable days while offline', async () => {
  const h = harness()
  h.localStorage.setItem('token', 'admin-login')
  const week = '/admin/bookings?business_id=42&start_date=2026-10-26&end_date=2026-11-01'
  const rows = [
    { id: 10, date: '2026-10-31', client_name: 'Anna', client_phone: '+70000000001', notes: 'Important note' },
    { id: 11, date: '2026-11-01', client_name: 'Maria', client_phone: '+70000000002', notes: 'Second note' },
  ]
  await h.code.saveOfflineSnapshot(week, rows, 'admin-login')
  const saturday = await h.code.loadOfflineSnapshot('/admin/bookings?date=2026-10-31&business_id=42', 'admin-login')
  assert.equal(saturday.data.length, 1)
  assert.equal(saturday.data[0].notes, 'Important note')
  const monday = await h.code.loadOfflineSnapshot('/admin/bookings?business_id=42&date=2026-10-26', 'admin-login')
  assert.deepEqual(JSON.stringify(monday.data), '[]', 'empty days are cached as empty arrays')
  const sunday = await h.code.loadOfflineSnapshot('/admin/bookings?business_id=42&date=2026-11-01', 'admin-login')
  assert.equal(sunday.data[0].client_phone, '+70000000002')
  assert.equal(h.db.rows.size, 8, 'weekly snapshot and seven individual days')
})
