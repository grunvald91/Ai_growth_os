import { readFileSync } from 'node:fs'
import test from 'node:test'
import assert from 'node:assert/strict'

const logo = readFileSync(new URL('../src/components/Logo.tsx', import.meta.url), 'utf8')

test('ServGo wordmark and calendar ink follow shared Air tokens on dark background', () => {
  assert.ok(logo.includes("const ink = light ? '#FFFFFF' : 'var(--s-text, #0D1B2A)'"))
  assert.ok(logo.includes("const goCol = light ? '#7FA0FF' : 'var(--s-accent, #2F5BEB)'"))
  assert.ok(logo.includes('color: ink'))
  assert.ok(logo.includes('<span style={{ color: goCol }}>Go</span>'))
  assert.ok(!logo.includes("const ink = light ? '#FFFFFF' : '#0D1B2A'"))
})

test('Explicit light logo keeps existing bright palette, legacy fallback remains for missing tokens', () => {
  assert.ok(logo.includes("light ? '#FFFFFF'"))
  assert.ok(logo.includes("light ? '#7FA0FF'"))
  assert.ok(logo.includes('var(--s-text, #0D1B2A)'))
  assert.ok(logo.includes('var(--s-accent, #2F5BEB)'))
})
