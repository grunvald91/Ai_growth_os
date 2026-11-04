import { readFileSync } from 'node:fs'
import test from 'node:test'
import assert from 'node:assert/strict'

const root = new URL('../src/', import.meta.url)
const urlSource = readFileSync(new URL('utils/studioTicketUrl.ts', root), 'utf8')
const js = urlSource.replace('raw: string', 'raw').replace('): boolean', ')')
const { isTrustedStudioTicketUrl } = await import('data:text/javascript,' + encodeURIComponent(js))

test('accepts only fixed trusted one-time Studio ticket destinations', () => {
  assert.equal(isTrustedStudioTicketUrl('https://growth.servgo.ru/studio/sso?t=a.b_c-9'), false)
  assert.equal(isTrustedStudioTicketUrl('https://servgo.ru/studio/sso?t=a.b_c-9'), true)
  for (const bad of [
    'http://servgo.ru/studio/sso?t=abc',
    'https://servgo.ru.evil.example/studio/sso?t=abc',
    'https://servgo.ru@evil.example/studio/sso?t=abc',
    'https://user@servgo.ru/studio/sso?t=abc',
    'https://mail.servgo.ru/studio/sso?t=abc',
    'https://servgo.ru/studio/login?t=abc',
    'https://servgo.ru/studio/sso?next=https://evil.example&t=abc',
    'https://servgo.ru/studio/sso?t=abc&t=def',
    'https://servgo.ru/studio/sso?t=abc#next',
    'https://servgo.ru/studio/sso',
    '/studio/sso?t=abc',
  ]) assert.equal(isTrustedStudioTicketUrl(bad), false, bad)
})

test('PWA Growth entry issues fresh ticket, not an unauthenticated /studio redirect', () => {
  const app = readFileSync(new URL('App.tsx', root), 'utf8')
  const entry = readFileSync(new URL('components/StudioEntry.tsx', root), 'utf8')
  const screen = readFileSync(new URL('pages/client/GrowthWorkspacePage.tsx', root), 'utf8')
  assert.match(app, /path="\/workspace\/growth" element=\{<AdminRoute/)
  assert.match(screen, /<StudioEntry showUnavailable/)
  assert.match(screen, /<AdminShell title="AI Growth OS"/)
  assert.doesNotMatch(screen, /<ClientShell/)
  assert.match(entry, /api\.studioTicket\(id\)/)
  assert.match(entry, /isTrustedStudioTicketUrl\(url\)/)
  assert.match(entry, /window\.location\.assign\(url\)/)
})


test('first visit uses native ServGo confirmation, no ticket or personal data in URL', () => {
  const screen = readFileSync(new URL('pages/client/GrowthWorkspacePage.tsx', root), 'utf8')
  assert.match(screen, /searchParams\.get\('setup'\) === '1'/)
  assert.match(screen, /Подготовить маркетинг для этого бизнеса\?/)
  assert.match(screen, /method="post" action="\/studio\/sso\/create"/)
  assert.match(screen, /name="business_id" value=\{businessId\}/)
  assert.match(screen, /Создать и открыть/)
  assert.match(screen, /to="\/admin"/)
  assert.doesNotMatch(screen, /searchParams\.get\('(c|ticket|t)'\)/)
  assert.doesNotMatch(screen, /localStorage\.setItem\(['"]sg_growth_setup/)
})

test('Growth entry from admin fails closed in the admin shell instead of legacy login', () => {
  const hook = readFileSync(new URL('hooks/useOpenStudio.ts', root), 'utf8')
  const dashboard = readFileSync(new URL('pages/admin/AdminDashboard.tsx', root), 'utf8')
  assert.match(hook, /isTrustedStudioTicketUrl\(url\)/)
  assert.match(hook, /setError\('Не удалось открыть маркетинг/)
  assert.doesNotMatch(hook, /location\.assign\('https:\/\/growth\.servgo\.ru\/studio'\)/)
  assert.match(dashboard, /studioError && <p role="alert"/)
})

test('native Growth setup has a safe error screen and a protected admin route', () => {
  const app = readFileSync(new URL('App.tsx', root), 'utf8')
  const screen = readFileSync(new URL('pages/client/GrowthWorkspacePage.tsx', root), 'utf8')
  assert.match(app, /path="\/workspace\/growth" element=\{<AdminRoute/)
  for (const state of ['expired', 'denied', 'origin', 'business', 'conflict', 'owner-required']) {
    assert.match(screen, new RegExp(state))
  }
  assert.match(screen, /action="\/studio\/sso\/create"/)
  assert.doesNotMatch(screen, /searchParams\.get\('(c|ticket|t)'\)/)
})


test('admin mail item requires server-confirmed platform owner permission', () => {
  const admin = readFileSync(new URL('components/AdminShell.tsx', root), 'utf8')
  assert.match(admin, /fetch\('\/api\/owner-mail\/permission'/)
  assert.match(admin, /res\.ok && \(await res\.json\(\)\)\.allowed === true/)
  assert.match(admin, /mailAllowed && <OwnerMailEntry/)
  assert.match(admin, /to: '\/workspace\/mail', label: 'Почта ServGo'/)
  assert.match(admin, /MORE\.filter\(item => mailAllowed \|\| item\.to !== '\/workspace\/mail'\)/)
  assert.doesNotMatch(admin, /localStorage\.getItem\('userRole'\).*mailAllowed/)
})
