import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const css = readFileSync(new URL('../src/pages/client/booking-flow.css', import.meta.url), 'utf8')
const login = readFileSync(new URL('../src/pages/client/LoginPage.tsx', import.meta.url), 'utf8')
const email = readFileSync(new URL('../src/components/EmailAuthForm.tsx', import.meta.url), 'utf8')

test('login/account shell overrides light-only card and account panels for dark mode', () => {
  const dark = css.slice(css.indexOf("html[data-theme='dark'] .bf-root"))
  assert.ok(dark.includes("html[data-theme='dark'] .bf-shell-page .ds-card"))
  assert.ok(dark.includes("html[data-theme='dark'] .bf-account-card"))
  assert.ok(dark.includes("html[data-theme='dark'] .bf-account-row"))
  assert.match(dark, /\.bf-shell-page \.ds-card,[\s\S]*?background: var\(--s-surface-2\)/)
  assert.match(dark, /\.bf-shell-page \.ds-card \.ds-input[\s\S]*?background: var\(--s-surface\)/)
  assert.match(dark, /color-scheme: dark;/)
})

test('sign-in components use theme classes rather than static white fields', () => {
  assert.match(login, /<YandexLoginButton \/>/)
  assert.match(login, /<VkLoginButton \/>/)
  assert.match(login, /<EmailAuthForm \/>/)
  assert.match(email, /className="ds-card"/)
  assert.match(email, /className="ds-input"/)
  assert.doesNotMatch(email, /background:\s*['\"](?:white|#fff)/i)
})

test('VK sign-in and explicit identity linking are exposed in the current account experience', () => {
  const account = readFileSync(new URL('../src/pages/client/AccountPage.tsx', import.meta.url), 'utf8')
  const routes = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
  const vkLink = readFileSync(new URL('../src/components/VkAccountLink.tsx', import.meta.url), 'utf8')
  assert.match(account, /<VkAccountLink \/>/)
  assert.match(routes, /path="\/login\/vk"/)
  assert.match(vkLink, /vkLinkStart/)
  assert.doesNotMatch(vkLink, /background:\s*['\"](?:white|#fff)/i)
})
