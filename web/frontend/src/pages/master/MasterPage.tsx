import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Phone, ChevronLeft, ChevronRight, CalendarOff } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useConnectivity } from '../../utils/useConnectivity'
import { api, ApiError, type MasterBooking, type MasterSchedule } from '../../api/client'
import { ClientShell } from '../../components/ClientShell'

const WINDOW_DAYS = 14

const STATUS_LABELS: Record<string, string> = {
  pending: 'Ожидает подтверждения',
  confirmed: 'Подтверждена',
  completed: 'Завершена',
  cancelled: 'Отменена',
  pending_prepayment: 'Ждёт предоплату',
  prepayment_sent: 'Предоплата отправлена',
  prepayment_confirmed: 'Предоплата подтверждена',
}

// Завершить можно подтверждённую запись (в т.ч. с подтверждённой предоплатой),
// сменить услугу — у любой неотменённой и незавершённой, в т.ч. с предоплатой.
const FINISHABLE = ['pending', 'confirmed', 'prepayment_confirmed']
const CHANGEABLE = [...FINISHABLE, 'pending_prepayment', 'prepayment_sent']

function pad(n: number) { return String(n).padStart(2, '0') }
function toKey(d: Date) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` }
function fromKey(key: string) {
  const [y, m, d] = key.split('-').map(Number)
  return new Date(y, m - 1, d)
}
function addDays(key: string, days: number) {
  const d = fromKey(key)
  d.setDate(d.getDate() + days)
  return toKey(d)
}

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
const WEEKDAYS = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота']

function dayTitle(key: string, today: string) {
  const d = fromKey(key)
  const base = `${d.getDate()} ${MONTHS[d.getMonth()]}, ${WEEKDAYS[d.getDay()]}`
  if (key === today) return `Сегодня · ${base}`
  if (key === addDays(today, 1)) return `Завтра · ${base}`
  return base
}

function shortDate(key: string) {
  const d = fromKey(key)
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`
}

function phoneHref(phone: string) {
  return `tel:${phone.replace(/[^\d+]/g, '')}`
}

/** Личное расписание мастера: только его записи, без цен, аналитики и настроек. */
export function MasterPage() {
  const navigate = useNavigate()
  const online = useConnectivity()
  const today = useMemo(() => toKey(new Date()), [])
  const nowHHMM = `${pad(new Date().getHours())}:${pad(new Date().getMinutes())}`
  const [from, setFrom] = useState(today)
  const [rawData, setRawData] = useState<MasterSchedule | null>(null)
  const [loadedFrom, setLoadedFrom] = useState('')
  const data = loadedFrom === from ? rawData : null
  const latestRequest = useRef(0)
  const lastLoadedFrom = useRef('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [changingId, setChangingId] = useState<number | null>(null)
  const [newServiceId, setNewServiceId] = useState('')
  const [busyId, setBusyId] = useState<number | null>(null)
  const [actionError, setActionError] = useState('')

  const load = useCallback(async (start: string) => {
    const sequence = ++latestRequest.current
    setLoading(true)
    setError('')
    try {
      const result = await api.masterSchedule(start, addDays(start, WINDOW_DAYS - 1))
      if (sequence !== latestRequest.current) return
      setRawData(result)
      lastLoadedFrom.current = start
      setLoadedFrom(start)
    } catch (e) {
      if (sequence !== latestRequest.current) return
      if (e instanceof ApiError && e.status === 401) { navigate('/login', { replace: true }); return }
      if (e instanceof ApiError && e.status === 403) { navigate('/my', { replace: true }); return }
      if (lastLoadedFrom.current !== start) { setRawData(null); setLoadedFrom('') }
      setError('Записи за этот период не сохранены для офлайн-просмотра. Подключитесь к интернету и повторите.')
    } finally {
      if (sequence === latestRequest.current) setLoading(false)
    }
  }, [navigate])

  useEffect(() => {
    window.dispatchEvent(new Event('servgo-offline-context-reset'))
    void load(from)
  }, [from, load])
  useEffect(() => {
    const refresh = () => { if (navigator.onLine) void load(from) }
    window.addEventListener('online', refresh)
    window.addEventListener('focus', refresh)
    return () => { window.removeEventListener('online', refresh); window.removeEventListener('focus', refresh) }
  }, [from, load])

  const runAction = async (bookingId: number, action: () => Promise<unknown>, failText: string) => {
    setBusyId(bookingId)
    setActionError('')
    try {
      await action()
      setChangingId(null)
      setNewServiceId('')
      await load(from)
    } catch (e) {
      let msg = e instanceof Error ? e.message : failText
      try { const parsed = JSON.parse(msg); if (typeof parsed?.detail === 'string') msg = parsed.detail } catch { /* не JSON */ }
      setActionError(msg || failText)
    } finally {
      setBusyId(null)
    }
  }

  const days = useMemo(() => Array.from({ length: WINDOW_DAYS }, (_, i) => addDays(from, i)), [from])
  const byDay = useMemo(() => {
    const map = new Map<string, MasterBooking[]>()
    for (const b of data?.bookings || []) {
      const list = map.get(b.date) || []
      list.push(b)
      map.set(b.date, list)
    }
    return map
  }, [data])
  const offDays = useMemo(() => new Set((data?.days_off || []).map((d) => d.date)), [data])
  const businesses = new Set((data?.employees || []).map((e) => e.business_id))
  const names = (data?.employees || []).map((e) => e.employee_name).join(', ')
  const visibleDays = days.filter((d) => byDay.has(d) || offDays.has(d))

  return (
    <ClientShell
      title="Моё расписание"
      subtitle={data ? `${names}${businesses.size === 1 ? ` · ${data.employees[0].business_name}` : ''}` : undefined}
    >
      {error && (
        <div role="alert" className="ds-card" style={{ padding: 16 }}>
          <p className="ds-sub" style={{ marginBottom: 10 }}>{error}</p>
          <button className="ds-btn ds-btn--primary" onClick={() => void load(from)}>Повторить</button>
        </div>
      )}

      {loading && !data && <p className="ds-sub" role="status">Загружаем…</p>}

      {data && (
        <>
          <p className="ds-hint" style={{ marginTop: 0 }}>
            {shortDate(from)} — {shortDate(addDays(from, WINDOW_DAYS - 1))}
          </p>

          {visibleDays.length === 0 && !loading && (
            <div className="ds-card" style={{ padding: 16 }}>
              <p className="ds-text">В этом периоде записей нет.</p>
            </div>
          )}

          {actionError && <div className="ds-error" role="alert" style={{ marginBottom: 12 }}>{actionError}</div>}

          <div style={{ display: 'grid', gap: 16, opacity: loading ? 0.6 : 1 }}>
            {visibleDays.map((day) => (
              <section key={day} aria-label={dayTitle(day, today)}>
                <h2 className="ds-h3" style={{ marginBottom: 8 }}>{dayTitle(day, today)}</h2>
                {offDays.has(day) && (
                  <p className="ds-sub" style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                    <CalendarOff size={15} /> У вас выходной
                  </p>
                )}
                <div style={{ display: 'grid', gap: 8 }}>
                  {(byDay.get(day) || []).map((b) => (
                    <article
                      key={b.id}
                      className="ds-card"
                      style={{ padding: 14, display: 'grid', gap: 6, opacity: b.status === 'cancelled' ? 0.55 : 1 }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 10 }}>
                        <strong style={{ fontSize: 20 }}>{b.time}{b.status === 'completed' && b.actual_end_time ? ` – ${b.actual_end_time}` : ''}</strong>
                        <span className="ds-sub">{STATUS_LABELS[b.status] || b.status}</span>
                      </div>
                      <div className="ds-text" style={{ fontWeight: 650 }}>{b.service_name}</div>
                      {(businesses.size > 1 || (data.employees.length > 1)) && (
                        <div className="ds-sub">{b.business_name}{data.employees.length > 1 ? ` · ${b.employee_name}` : ''}</div>
                      )}
                      <div className="ds-text">{b.client_name}</div>
                      {b.client_phone && (
                        <a href={phoneHref(b.client_phone)} className="ds-btn ds-btn--sm" style={{ justifySelf: 'start', gap: 6 }}>
                          <Phone size={14} /> {b.client_phone}
                        </a>
                      )}
                      {b.notes && <div className="ds-sub">Комментарий: {b.notes}</div>}
                      {CHANGEABLE.includes(b.status) && (
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
                          {FINISHABLE.includes(b.status) && <button className="ds-btn ds-btn--sm" disabled={!online || busyId === b.id}
                            onClick={() => void runAction(b.id, () => api.masterFinishBooking(b.id), 'Не удалось завершить')}>
                            {b.date === today && b.time <= nowHHMM ? 'Завершить сейчас' : 'Завершить'}
                          </button>}
                          <button className="ds-btn ds-btn--sm" disabled={!online || busyId === b.id}
                            onClick={() => { setChangingId(changingId === b.id ? null : b.id); setNewServiceId(''); setActionError('') }}>
                            Сменить услугу
                          </button>
                        </div>
                      )}
                      {changingId === b.id && (
                        <div style={{ display: 'grid', gap: 8, marginTop: 4 }}>
                          <small className="ds-sub">Если клиент выбрал не ту услугу</small>
                          <select className="ds-input" value={newServiceId} onChange={(e) => setNewServiceId(e.target.value)}>
                            <option value="">Выберите услугу</option>
                            {(data.services || [])
                              .filter((sv) => sv.business_id === b.business_id && sv.id !== b.service_id && !sv.is_additional)
                              .map((sv) => <option key={sv.id} value={sv.id}>{sv.name}{sv.duration_minutes ? ` · ${sv.duration_minutes} мин` : ''}</option>)}
                          </select>
                          <button className="ds-btn ds-btn--primary ds-btn--sm" style={{ justifySelf: 'start' }}
                            disabled={!online || !newServiceId || busyId === b.id}
                            onClick={() => void runAction(b.id, () => api.masterChangeService(b.id, Number(newServiceId)), 'Не удалось заменить услугу')}>
                            Заменить
                          </button>
                        </div>
                      )}
                    </article>
                  ))}
                </div>
              </section>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 8, marginTop: 20, flexWrap: 'wrap' }}>
            <button className="ds-btn" onClick={() => setFrom(addDays(from, -WINDOW_DAYS))} aria-label="Раньше">
              <ChevronLeft size={16} /> Раньше
            </button>
            {from !== today && (
              <button className="ds-btn" onClick={() => setFrom(today)}>Сегодня</button>
            )}
            <button className="ds-btn ds-btn--primary" onClick={() => setFrom(addDays(from, WINDOW_DAYS))} aria-label="Позже">
              Позже <ChevronRight size={16} />
            </button>
          </div>
        </>
      )}
    </ClientShell>
  )
}
