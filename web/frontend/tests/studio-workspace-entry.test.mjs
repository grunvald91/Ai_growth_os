import { readFileSync } from 'node:fs'
import test from 'node:test'
import assert from 'node:assert/strict'

const root = new URL('../src/', import.meta.url)
const urlSource = readFileSync(new URL('utils/studioTicketUrl.ts', root), 'utf8')
const js = urlSource.replace('raw: string', 'raw').replace('): boolean', ')')
const { isTrustedStudioTicketUrl } = await import('data:text/javascript,' + encodeURIComponent(js))

test('accepts only fixed trusted one-time Studio ticket destinations', () => {
  assert.equal(isTrustedStudioTicketUrl('https://growth.servgo.ru/studio/sso?t=a.b_c-9'), true)
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
  assert.match(app, /path="\/workspace\/growth" element=\{<ProtectedRoute/)
  assert.match(screen, /<StudioEntry showUnavailable/)
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
