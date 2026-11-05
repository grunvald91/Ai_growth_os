import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const load = (relative) => readFileSync(new URL(relative, import.meta.url), 'utf8')
const canonical = load('../src/styles/design-system.css')
const tokens = load('../../api/routes/servgo-air-tokens.css')
const routes = load('../../api/routes/mail_campaign_ui.py')

test('Mail tokens are an exact export of the canonical ServGo Air light/dark palette', () => {
  const start = canonical.indexOf(':root,')
  const end = canonical.indexOf('/* ─────────────────────────── 2. БАЗА')
  assert.ok(start >= 0 && end > start)
  const expected = canonical.slice(start, end).trim() + '\n'
  assert.ok(tokens.endsWith(expected), 'Mail tokens drifted from BookingBot design-system.css')
  assert.match(tokens, /--s-bg:\s*#ffffff/)
  assert.match(tokens, /--s-bg:\s*#111a29/)
})

test('both standalone Mail pages include shared theme resources and visible picker', () => {
  for (const path of [
    '../../api/routes/owner_mail_ui.html',
    '../../api/routes/mail_campaign_dashboard.html',
  ]) {
    const page = load(path)
    for (const asset of ['/api/servgo-air-tokens.css', '/api/servgo-mail-air.css',
      '/api/servgo-air-theme.js', '/api/servgo-mail-theme-picker.js']) {
      assert.ok(page.includes(asset), path + ' missing ' + asset)
    }
    assert.match(page, /id="servgo-mail-theme"/)
    assert.match(page, /value="system"/)
    assert.match(page, /value="light"/)
    assert.match(page, /value="dark"/)
  }
})

test('the Mail login also uses canonical colors and permits only its own static theme assets', () => {
  const source = load('../../api/routes/owner_mail.py')
  for (const asset of ['/api/servgo-air-tokens.css', '/api/servgo-mail-air.css',
    '/api/servgo-air-theme.js', '/api/servgo-mail-theme-picker.js']) {
    assert.ok(source.includes(asset), 'login missing ' + asset)
  }
  assert.match(source, /id="servgo-mail-theme"/)
  assert.ok(source.includes("script-src 'self' 'unsafe-inline'"))
  assert.ok(source.includes("style-src 'self' 'unsafe-inline'"))
})

test('Mail resource endpoints have no write capability and dashboard remains owner-only', () => {
  assert.match(routes, /@router\.get\("\/api\/servgo-air-tokens\.css"/)
  assert.match(routes, /@router\.get\("\/api\/servgo-mail-air\.css"/)
  assert.match(routes, /@router\.get\("\/api\/servgo-air-theme\.js"/)
  assert.match(routes, /await mail_owner\(request, session\)/)
  assert.doesNotMatch(routes, /@router\.post\("\/api\/servgo-air/)
})


test('client booking journey inherits dark ServGo Air without changing its light reference', () => {
  const booking = load('../src/pages/client/booking-flow.css')
  assert.match(booking, /ServGo Air unified dark skin/)
  assert.match(booking, /html\[data-theme='dark'\] \.bf-root/)
  assert.match(booking, /--bf-ink: var\(--s-text\)/)
  assert.match(booking, /background: var\(--s-surface\)/)
  assert.match(booking, /color-scheme: dark/)
  assert.match(booking, /color-scheme: light/)
})
