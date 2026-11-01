import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

// Изолированная проверка фактического TS-хелпера без браузера и React.
const source = readFileSync(new URL('../src/pages/client/bookingEntry.ts', import.meta.url), 'utf8')
const javascript = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText
const { resolveBookingEntry, getAvailableServices, getAvailableResources } = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`)

const s1 = { id: 11, name: 'Диагностика', is_active: true }
const s2 = { id: 12, name: 'Ремонт', is_active: true }
const m1 = { id: 21, name: 'Анна', service_ids: [11] }
const m2 = { id: 22, name: 'Борис', service_ids: [11, 12] }
const business = (services = [s1, s2], employees = [m1, m2], options = {}) => ({
  id: 1, name: 'Тест', services, employees, ...options,
})

test('обычная ссылка на запись сохраняет начальную страницу', () => {
  const result = resolveBookingEntry(business(), null, null)
  assert.equal(result.step, 'home')
  assert.deepEqual(result.services, [])
  assert.equal(result.resource, null)
})

test('выбранная услуга сразу ведёт к выбору подходящего мастера', () => {
  const result = resolveBookingEntry(business(), 11, null)
  assert.equal(result.step, 'resource')
  assert.equal(result.services[0].id, 11)
  assert.equal(result.resource, null)
})

test('выбранная услуга и единственный подходящий мастер ведут к календарю', () => {
  const result = resolveBookingEntry(business(), 12, null)
  assert.equal(result.step, 'calendar')
  assert.equal(result.services[0].id, 12)
  assert.equal(result.resource.id, 22)
})

test('выбранный мастер ведёт к списку его услуг', () => {
  const result = resolveBookingEntry(business(), null, 22)
  assert.equal(result.step, 'services')
  assert.equal(result.resource.id, 22)
  assert.deepEqual(result.services, [])
})

test('для мастера с единственной доступной услугой сразу показываем календарь', () => {
  const result = resolveBookingEntry(business(), null, 21)
  assert.equal(result.step, 'calendar')
  assert.equal(result.services[0].id, 11)
  assert.equal(result.resource.id, 21)
})

test('готовая совместимая пара услуга и мастер переходит к календарю', () => {
  const result = resolveBookingEntry(business(), 11, 21)
  assert.equal(result.step, 'calendar')
  assert.equal(result.services[0].id, 11)
  assert.equal(result.resource.id, 21)
})

test('несовместимую пару нельзя сохранить: явную услугу оставляем', () => {
  const result = resolveBookingEntry(business(), 12, 21)
  assert.equal(result.step, 'calendar')
  assert.equal(result.services[0].id, 12)
  assert.equal(result.resource.id, 22)
})

test('устаревшая или дополнительная услуга не становится выбранной', () => {
  const list = [s1, { ...s2, is_active: false }, { id: 13, is_active: true, is_additional: true }]
  for (const id of [12, 13, 999]) {
    const result = resolveBookingEntry(business(list), id, null)
    assert.equal(result.step, 'services')
    assert.deepEqual(result.services, [])
  }
})

test('при отсутствии мастеров после выбранной услуги открываем календарь', () => {
  assert.equal(resolveBookingEntry(business([s1], []), 11, null).step, 'calendar')
})

test('аренда зала не пропускает выбор зала', () => {
  const room = { id: 31, name: 'Зал', service_ids: [] }
  const result = resolveBookingEntry(business([s1], [room], { business_type: 'venue_rental' }), 11, null)
  assert.equal(result.step, 'resource')
  assert.equal(result.resource, null)
})

test('настройка resource_first не теряет уже выбранную услугу', () => {
  const result = resolveBookingEntry(business([s1, s2], [m1, m2], { booking_flow_order: 'resource_first' }), 11, null)
  assert.equal(result.step, 'resource')
  assert.equal(result.services[0].id, 11)
})

test('услуга из ссылки важнее настройки resource_first, но порядок бизнеса не меняется', () => {
  const original = business([s1, s2], [m1, m2], { booking_flow_order: 'resource_first' })
  const result = resolveBookingEntry(original, 11, null)
  assert.equal(result.order, 'service_first')
  assert.equal(original.booking_flow_order, 'resource_first')
  assert.equal(result.step, 'resource')
})

test('мастер из ссылки важнее настройки service_first', () => {
  const result = resolveBookingEntry(business(), null, 22)
  assert.equal(result.order, 'resource_first')
  assert.equal(result.step, 'services')
})

test('при обычной записи настройки порядка остаются нетронутыми', () => {
  assert.equal(resolveBookingEntry(business(), null, null).order, 'service_first')
  assert.equal(resolveBookingEntry(business([s1], [m1], { booking_flow_order: 'resource_first' }), null, null).order, 'resource_first')
})

test('при невалидном идентификаторе сохраняется настроенный порядок', () => {
  assert.equal(resolveBookingEntry(business([], [m1], { booking_flow_order: 'resource_first' }), 999, null).order, 'resource_first')
  assert.equal(resolveBookingEntry(business(), null, 999).order, 'service_first')
})

test('смена услуги в обычном service_first не блокируется текущим мастером', () => {
  // Мастер 21 умеет услугу 11, но пользователь должен видеть услугу 12
  // и при её выборе сменить мастера.
  assert.deepEqual(getAvailableServices([s1, s2], m1, [m1, m2], 'service_first').map((s) => s.id), [11, 12])
  assert.deepEqual(getAvailableResources([s2], [m1, m2], 'service_first').map((m) => m.id), [22])
})

test('смена мастера в обычном resource_first не блокируется прежней услугой', () => {
  assert.deepEqual(getAvailableResources([s2], [m1, m2], 'resource_first').map((m) => m.id), [21, 22])
  assert.deepEqual(getAvailableServices([s1, s2], m1, [m1, m2], 'resource_first').map((s) => s.id), [11])
})

test('вход с выбранным мастером ограничивает выбор только его услугами', () => {
  const entry = resolveBookingEntry(business(), null, 21)
  assert.equal(entry.order, 'resource_first')
  assert.deepEqual(getAvailableServices([s1, s2], entry.resource, [m1, m2], entry.order).map((s) => s.id), [11])
})

test('вход с выбранной услугой показывает только подходящих мастеров', () => {
  const entry = resolveBookingEntry(business([s1, s2], [m1, m2], { booking_flow_order: 'resource_first' }), 12, null)
  assert.equal(entry.order, 'service_first')
  assert.deepEqual(getAvailableResources(entry.services, [m1, m2], entry.order).map((m) => m.id), [22])
})

test('мастер без доступных услуг не оставляет человека на пустом экране', () => {
  const result = resolveBookingEntry(business([s2], [m1, m2]), null, 21)
  assert.equal(result.resource, null)
  assert.equal(result.step, 'services')
  assert.equal(result.order, 'service_first')
})

test('несовместимая пара в resource_first сохраняет приоритет явной услуги', () => {
  const result = resolveBookingEntry(business([s1, s2], [m1, m2], { booking_flow_order: 'resource_first' }), 12, 21)
  assert.equal(result.order, 'service_first')
  assert.equal(result.services[0].id, 12)
  assert.equal(result.resource.id, 22)
  assert.equal(result.step, 'calendar')
})
