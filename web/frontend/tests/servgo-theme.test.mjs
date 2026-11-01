import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

const source = readFileSync(new URL('../src/utils/servgoTheme.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

function harness({ saved = {}, systemDark = false, telegram = null } = {}) {
  const store = new Map(Object.entries(saved))
  const listeners = new Map()
  let system = systemDark
  let mediaChange = null
  const emitted = []
  const meta = { content: '#ffffff' }
  const root = { dataset: {}, style: {} }
  const storage = {
    getItem(key) { return store.get(key) ?? null },
    setItem(key, value) { store.set(key, value) },
  }
  const mql = {
    get matches() { return system },
    addEventListener(name, fn) { if (name === 'change') mediaChange = fn },
  }
  const window = {
    localStorage: storage,
    Telegram: telegram ? { WebApp: { colorScheme: telegram } } : undefined,
    matchMedia: () => mql,
    addEventListener(name, fn) { listeners.set(name, fn) },
    dispatchEvent(event) { emitted.push(event); listeners.get(event.type)?.(event) },
  }
  const module = { exports: {} }
  runInNewContext(compiled, {
    module,
    exports: module.exports,
    window,
    document: { documentElement: root, querySelector: () => meta },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail } },
  })
  return {
    api: module.exports, store, root, meta, emitted,
    system(value) { system = value; mediaChange?.() },
    storage(key) { listeners.get('storage')?.({ key }) },
  }
}

test('fresh install resolves system theme without inventing an explicit preference', () => {
  const h = harness({ systemDark: true })
  h.api.initializeServgoTheme()
  assert.equal(h.api.getServgoThemePreference(), 'system')
  assert.equal(h.root.dataset.theme, 'dark')
  assert.equal(h.root.dataset.themePreference, 'system')
  assert.equal(h.meta.content, '#111a29')
  assert.equal(h.store.has('servgo-theme'), false)
})

test('migrate the old schedule theme exactly once', () => {
  const h = harness({ saved: { schedTheme: 'dark' } })
  h.api.initializeServgoTheme()
  assert.equal(h.store.get('servgo-theme'), 'dark')
  assert.equal(h.root.dataset.theme, 'dark')
  h.api.setServgoThemePreference('light')
  assert.equal(h.store.get('servgo-theme'), 'light')
  assert.equal(h.root.dataset.theme, 'light')
  assert.equal(h.store.get('schedTheme'), 'dark')
})

test('explicit ServGo preference overrides old separate module keys', () => {
  const h = harness({ saved: {
    'servgo-theme': 'system', schedTheme: 'dark', 'servgo-desktop-theme': 'dark',
  } })
  h.api.initializeServgoTheme()
  assert.equal(h.root.dataset.themePreference, 'system')
  assert.equal(h.root.dataset.theme, 'light')
})

test('system and Telegram theme changes update the root without overwriting explicit dark', () => {
  const h = harness()
  h.api.initializeServgoTheme()
  h.system(true)
  assert.equal(h.root.dataset.theme, 'dark')
  h.api.setServgoThemePreference('light')
  h.system(false)
  assert.equal(h.root.dataset.theme, 'light')
  const telegram = harness({ telegram: 'dark' })
  telegram.api.initializeServgoTheme()
  assert.equal(telegram.root.dataset.theme, 'dark')
})

test('storage events synchronize theme between tabs on the same origin', () => {
  const h = harness()
  h.api.initializeServgoTheme()
  h.store.set('servgo-theme', 'dark')
  h.storage('servgo-theme')
  assert.equal(h.root.dataset.theme, 'dark')
  assert.ok(h.emitted.some(e => e.type === 'servgo-themechange'))
})

test('unknown theme value cannot replace the saved preference', () => {
  const h = harness({ saved: { 'servgo-theme': 'dark' } })
  h.api.initializeServgoTheme()
  h.api.setServgoThemePreference('purple')
  assert.equal(h.store.get('servgo-theme'), 'dark')
  assert.equal(h.root.dataset.theme, 'dark')
})
