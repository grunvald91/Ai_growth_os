import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type SalesPolicy, type SalesSettings } from '../../api/client'
import { AdminShell } from '../../components/AdminShell'
import { BusinessSelector } from '../../components/BusinessSelector'
import { useAdminBusiness } from '../../hooks/useAdminBusiness'

const toggles = [
  ['enabled', 'Включить ИИ-продавца'], ['show_prices', 'Показывать цены'],
  ['recommend_specialists', 'Подбирать специалистов и ресурсы'],
  ['check_availability', 'Проверять свободные окна'],
  ['view_client_bookings', 'Показывать клиенту его записи после входа'],
] as const

const ruleNames: Record<string, string> = {
  opening: 'Начало работы', closing: 'Конец работы', slot_interval_minutes: 'Шаг записи, мин',
  break_start: 'Начало перерыва', break_end: 'Конец перерыва', booking_flow_order: 'Порядок выбора',
  prepayment_enabled: 'Предоплата включена', prepayment_percent: 'Предоплата, %',
  receipt_required: 'Требуется чек', legal_consent_required: 'Юридические согласия',
  cancellation_policy: 'Условия отмены', rental_deposit_policy: 'Депозит аренды',
}

function BusinessSettings({ businessId }: { businessId: number }) {
  const [settings, setSettings] = useState<SalesSettings | null>(null)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  useEffect(() => {
    let active = true
    api.salesSettings(businessId).then(data => { if (active) setSettings(data) })
      .catch(() => { if (active) setStatus('Не удалось загрузить настройки') })
    return () => { active = false }
  }, [businessId])
  const change = <K extends keyof SalesPolicy>(key: K, value: SalesPolicy[K]) =>
    setSettings(current => current && ({ ...current, policy: { ...current.policy, [key]: value } }))
  const save = async () => {
    if (!settings || busy) return
    setBusy(true); setStatus('')
    try { setSettings(await api.saveSalesSettings(businessId, settings)); setStatus('Настройки применены') }
    catch { setStatus('Не удалось сохранить. Настройки могли измениться — обновите страницу перед повторной попыткой.') }
    finally { setBusy(false) }
  }
  return <>
    <p role="status" aria-live="polite">{status}</p>
    {settings && <>
      <div className="ds-card" style={{ padding: 16 }}>
        {toggles.map(([key, label]) => <label key={key} style={{ display: 'block', marginBottom: 12 }}>
          <input type="checkbox" checked={settings.policy[key]} disabled={busy} onChange={e => change(key, e.target.checked)} /> {label}
        </label>)}
        <label className="ds-text">Создание записи<select className="ds-input" disabled={busy} value={settings.policy.booking_mode} onChange={e => change('booking_mode', e.target.value as SalesPolicy['booking_mode'])}>
          <option value="client_confirmation">ИИ создаёт запись после подтверждения клиента</option>
          <option value="operator_only">Записывает только оператор</option>
        </select></label>
        <p className="ds-sub">Клиент подтверждает карточку кнопкой. При отключённых ценах или проверке окон запись через ИИ недоступна.</p>
        <label className="ds-text">Инструкции компании<textarea className="ds-input" rows={5} maxLength={6000} value={settings.policy.instructions} disabled={busy} onChange={e => change('instructions', e.target.value)} /></label>
        <label className="ds-text">Темы, которых агент должен избегать<textarea className="ds-input" rows={3} maxLength={2000} value={settings.policy.prohibited_topics} disabled={busy} onChange={e => change('prohibited_topics', e.target.value)} /></label>
        <button className="ds-btn ds-btn--primary" disabled={busy} onClick={save}>Сохранить и применить</button>
      </div>
      <div className="ds-card" style={{ padding: 16, marginTop: 16 }}>
        <h2>Обязательные ограничения</h2>
        <ul>{settings.always_forbidden.map(rule => <li key={rule}>{rule}</li>)}</ul>
        <h2>Правила бизнеса</h2>
        <dl>{Object.entries(settings.business_rules).map(([key, value]) => <div key={key} style={{ marginBottom: 8 }}>
          <dt className="ds-sub">{ruleNames[key] || key}</dt><dd style={{ margin: 0 }}>{value === null ? 'Не задано' : typeof value === 'boolean' ? value ? 'Да' : 'Нет' : value === 'resource_first' ? 'Сначала ресурс' : value === 'service_first' ? 'Сначала услуга' : String(value)}</dd>
        </div>)}</dl>
        <h2>Покрытие сценариев</h2>
        <ul>{settings.coverage.map(row => <li key={row.rule}>{row.rule}: <strong>{row.status === 'supported' ? 'поддерживается' : 'форма записи / оператор'}</strong></li>)}</ul>
      </div>
    </>}
  </>
}

export function SalesSettingsPage() {
  const { businesses, selectedId, selectBusiness } = useAdminBusiness()
  return <AdminShell title="Настройки ИИ-продавца" headerExtra={<BusinessSelector businesses={businesses} selectedId={selectedId} onSelect={selectBusiness} />}>
    <Link to="/admin/sales">← Диалоги для оператора</Link>
    {selectedId && <BusinessSettings key={selectedId} businessId={selectedId} />}
  </AdminShell>
}
