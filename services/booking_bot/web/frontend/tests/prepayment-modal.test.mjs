import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(
  new URL('../src/components/AdminPaymentAttention.tsx', import.meta.url), 'utf8'
)

test('payment review stays urgent without trapping the entire offline schedule', () => {
  assert.match(source, /if \(!online\) \{/)
  assert.match(source, /setAttention\(null\)/)
  assert.match(source, /if \(!hasFreshAttention && !error\) return null/)
  assert.match(source, /const hasFreshAttention = !!attention\?\.count && online && !error/)
})

test('an owner may minimize, restore and dismiss the payment modal with Escape', () => {
  assert.match(source, /const \[minimized, setMinimized\] = useState\(false\)/)
  assert.match(source, /hasFreshAttention && !inQueue && ready && !minimized/)
  assert.match(source, />Свернуть и вернуться к расписанию<\/button>/)
  assert.match(source, />Развернуть<\/button>/)
  assert.match(source, /e\.key === 'Escape'.*setMinimized\(true\)/)
  assert.match(source, /setMinimized\(false\) \}, \[id\]/)
})

test('minimizing never changes or auto-confirms a booking or hides the pending alert', () => {
  assert.match(source, /const hasFreshAttention = !!attention\?\.count && online && !error/)
  assert.match(source, /Предоплаты на проверке:/)
  assert.match(source, /await api\.reviewPrepayment\(id, decision, details\.booking\.version\)/)
  assert.doesNotMatch(source, /setMinimized\(true\).*reviewPrepayment/)
})
