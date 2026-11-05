import { useState, useEffect, useCallback, useMemo, useRef, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { Link, useNavigate } from 'react-router-dom'
import {
  ChevronLeft,
  ChevronRight,
  Plus,
  Minus,
  X,
  Phone,
  Loader2,
  Search,
  Moon,
  Sun,
  LogOut,
  CalendarClock,
  SlidersHorizontal,
  Maximize2,
  Minimize2,
  RotateCcw,
  AlertCircle,
} from 'lucide-react'
import { api, type AdminBooking, type Service, type Employee } from '../../api/client'
import { useAdminBusiness } from '../../hooks/useAdminBusiness'
import { clearLogin } from '../../authUtils'
import { useTheme } from '../../hooks/useTheme'
import { useConnectivity } from '../../utils/useConnectivity'
import { AdminBottomNav } from '../../components/AdminShell'
import { BusinessSelector } from '../../components/BusinessSelector'
import './schedule.css'

// ── Constants ──
const DAYS_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
const DAY_START_H = 8
const DAY_END_H = 22
const TOTAL_MIN = (DAY_END_H - DAY_START_H) * 60
const GUTTER = 36
const DEFAULT_HOUR_PX = 55
const MIN_HOUR_PX = 26
const MAX_HOUR_PX = 130

// Цвет мастера — основа графика: каждая запись окрашена цветом специалиста.
const MASTER_PALETTE = ['#3279d2', '#925dd1', '#149985', '#d68132', '#dc638f', '#4f8f2f', '#c4544a', '#2f8fb0', '#8a6d3b', '#5d6fd6']

const STATUS_LABELS: Record<string, string> = {
  pending: 'Ожидает подтверждения',
  confirmed: 'Подтверждена',
  completed: 'Завершена',
  cancelled: 'Отменена',
  pending_prepayment: 'Ждёт предоплату',
  prepayment_sent: 'Предоплата отправлена',
}


type ToolKey = 'search' | 'filters' | 'details' | 'legend' | 'context'
const TOOL_LABELS: Record<ToolKey, string> = {
  search: 'Поиск',
  filters: 'Мастера',
  details: 'Сводка',
  legend: 'Цвета',
  context: 'Информация',
}

// ── Helpers ──
function formatDateISO(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function getMonday(d: Date): Date {
  const date = new Date(d)
  const day = date.getDay()
  date.setDate(date.getDate() + (day === 0 ? -6 : 1 - day))
  date.setHours(0, 0, 0, 0)
  return date
}
function formatDateShort(d: Date) {
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })
}
function dateFromISO(s: string) {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}
function timeToMin(t: string) {
  const [h, m] = t.split(':').map(Number)
  return h * 60 + m
}
function minToTime(min: number) {
  const h = Math.floor(min / 60)
  const m = Math.round(min % 60)
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}
function durationOf(b: AdminBooking) {
  const planned = b.service_duration || 60
  // Завершили раньше плана: показываем фактическое время, чтобы остаток был виден свободным.
  if (b.status === 'completed' && b.actual_end_time) {
    return Math.min(planned, Math.max(10, timeToMin(b.actual_end_time) - timeToMin(b.time)))
  }
  return planned
}
function endOf(b: AdminBooking) {
  return minToTime((timeToMin(b.time) + durationOf(b)) % (24 * 60))
}
function money(n?: number | null) {
  return `${new Intl.NumberFormat('ru-RU').format(n || 0)} ₽`
}
function firstName(name?: string) {
  return (name || '').split(' ')[0]
}
function isActive(b: AdminBooking) {
  return b.status !== 'cancelled'
}
function parseError(e: unknown, fallback: string) {
  let msg = e instanceof Error ? e.message : fallback
  try {
    const parsed = JSON.parse(msg)
    if (parsed?.detail) msg = typeof parsed.detail === 'string' ? parsed.detail : fallback
  } catch {
    // не JSON — оставляем исходный текст
  }
  return msg || fallback
}

type Lane = { booking: AdminBooking; startMin: number; endMin: number; lane: number; lanes: number }
type Cluster = { startMin: number; endMin: number; entries: AdminBooking[] }

// Пересекающиеся записи — независимые полосы; дорожки считаются внутри каждой группы пересечений.
function layoutLanes(list: AdminBooking[]): Lane[] {
  const items = list
    .map((b) => ({ booking: b, startMin: timeToMin(b.time), endMin: timeToMin(b.time) + durationOf(b) }))
    .sort((a, b) => a.startMin - b.startMin || b.endMin - a.endMin)
  const groups: (typeof items)[] = []
  let group: typeof items = []
  let until = -Infinity
  for (const it of items) {
    if (group.length && it.startMin >= until) {
      groups.push(group)
      group = []
      until = -Infinity
    }
    group.push(it)
    until = Math.max(until, it.endMin)
  }
  if (group.length) groups.push(group)
  return groups.flatMap((arr) => {
    const laneEnds: number[] = []
    const placed = arr.map((it) => {
      let lane = laneEnds.findIndex((end) => end <= it.startMin)
      if (lane < 0) {
        lane = laneEnds.length
        laneEnds.push(it.endMin)
      } else {
        laneEnds[lane] = it.endMin
      }
      return { ...it, lane }
    })
    return placed.map((p) => ({ ...p, lanes: laneEnds.length }))
  })
}

function clustersOf(list: AdminBooking[]): Cluster[] {
  const sorted = [...list].sort((a, b) => timeToMin(a.time) - timeToMin(b.time))
  const out: Cluster[] = []
  for (const b of sorted) {
    const s = timeToMin(b.time)
    const e = s + durationOf(b)
    const prev = out[out.length - 1]
    if (prev && s < prev.endMin) {
      prev.endMin = Math.max(prev.endMin, e)
      prev.entries.push(b)
    } else {
      out.push({ startMin: s, endMin: e, entries: [b] })
    }
  }
  return out
}

// ── Sheet wrapper ──
function Sheet({ title, sub, onClose, children, z }: { title: string; sub?: string; onClose: () => void; children: React.ReactNode; z?: number }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="ds-sheet-ov" style={z ? { zIndex: z } : undefined} onClick={onClose}>
      <div className="ds-sheet" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <button type="button" className="sched-sheet-grab" onClick={onClose} aria-label="Свернуть панель"><span /></button>
        <div className="ds-sheet-head">
          <div style={{ minWidth: 0 }}>
            <h3>{title}</h3>
            {sub && <p>{sub}</p>}
          </div>
          <button type="button" className="ds-icon-btn" onClick={onClose} aria-label="Закрыть"><X size={18} /></button>
        </div>
        {children}
      </div>
    </div>
  )
}

// ── Карточка записи ──
function BookingDetailSheet({
  booking, color, services, inProgress, onClose, onStatusChange, onChangeService, onReschedule, onQuote, error, updating,
}: {
  booking: AdminBooking
  color: string
  services: Service[]
  inProgress: boolean
  onClose: () => void
  onStatusChange: (id: number, status: string) => void
  onChangeService: (id: number, serviceId: number) => void
  onReschedule: (b: AdminBooking) => void
  onQuote: (id: number, price: number, prepayment: number) => void
  error: string
  updating: boolean
}) {
  const [rentalPrice, setRentalPrice] = useState(String(booking.price ?? ''))
  const [deposit, setDeposit] = useState(String(booking.prepayment_amount ?? 0))
  const [changing, setChanging] = useState(false)
  const [newServiceId, setNewServiceId] = useState('')
  const open = booking.status !== 'cancelled' && booking.status !== 'completed'
  const awaitingPayment = booking.status === 'pending_prepayment' || booking.status === 'prepayment_sent'
  const price = booking.price ?? 0
  const prepay = booking.prepayment_amount ?? 0
  const dateLabel = dateFromISO(booking.date).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', weekday: 'short' })
  const quoteInvalid = rentalPrice === '' || !Number.isInteger(Number(rentalPrice)) || !Number.isInteger(Number(deposit)) ||
    Number(rentalPrice) < 0 || Number(deposit) < 0 || Number(deposit) > Number(rentalPrice) * 0.2
  return (
    <Sheet title="Карточка записи" onClose={onClose}>
      <div className="sched-detail">
        <div className="sched-detail-head">
          <span className="sched-detail-dot" style={{ '--master': color } as CSSProperties} />
          <div style={{ minWidth: 0 }}>
            <div className="sched-detail-name">{booking.client_name}</div>
            <div className="sched-detail-sub">
              <span className={`sched-status st-${booking.status}`}>{STATUS_LABELS[booking.status] || booking.status}</span>
            </div>
          </div>
        </div>
        {['pending_prepayment', 'prepayment_sent'].includes(booking.status) && (
          <Link to="/admin/prepayments" className="sched-detail-link">Проверить предоплату и открыть чек</Link>
        )}
        <div className="sched-detail-grid">
          <div><small>Специалист</small><strong>{booking.employee_name || '—'}</strong></div>
          <div><small>Дата</small><strong>{dateLabel}</strong></div>
          <div><small>Время и длительность</small><strong>{booking.time}–{endOf(booking)} · {durationOf(booking)} мин</strong></div>
          <div><small>Услуга</small><strong>{booking.service_name}</strong></div>
          <div>
            <small>Телефон клиента</small>
            {booking.client_phone ? <a href={`tel:${booking.client_phone}`} className="sched-phone"><Phone size={12} />{booking.client_phone}</a> : <strong>—</strong>}
          </div>
          {booking.client_email && <div><small>Email</small><strong>{booking.client_email}</strong></div>}
        </div>
        {booking.notes && <div className="sched-detail-notes"><small>Комментарий</small>{booking.notes}</div>}
        {(price > 0 || prepay > 0) && (
          <div className="sched-detail-pay">
            <div className="ds-eyebrow" style={{ margin: 0 }}>Оплата</div>
            <div className="row"><span>Стоимость</span><strong>{money(price)}</strong></div>
            {prepay > 0 && (
              <>
                <div className="row">
                  <span>Предоплата</span>
                  <strong>{money(prepay)}</strong>
                </div>
                {!awaitingPayment && <div className="sched-progress"><span style={{ width: `${Math.round(Math.min(1, price > 0 ? prepay / price : 1) * 100)}%` }} /></div>}
                <small>
                  {booking.status === 'prepayment_sent'
                    ? 'Клиент сообщил об оплате — проверьте поступление и подтвердите запись.'
                    : booking.status === 'pending_prepayment'
                      ? 'Клиент ещё не сообщил об оплате.'
                      : `Остаток при визите: ${money(Math.max(0, price - prepay))}`}
                </small>
              </>
            )}
          </div>
        )}
        {booking.is_rental && open && (
          <div className="sched-detail-pay">
            <div className="ds-eyebrow" style={{ margin: 0 }}>Стоимость аренды и предоплата</div>
            {booking.price_requires_confirmation && <small>Требуется подтверждение цены менеджером.</small>}
            <div className="ds-field-row" style={{ marginTop: 6 }}>
              <label className="ds-field" style={{ marginBottom: 0 }}><span className="ds-label">Итого, ₽</span>
                <input type="number" min="0" step="1" value={rentalPrice} onChange={(e) => setRentalPrice(e.target.value)} className="ds-input" />
              </label>
              <label className="ds-field" style={{ marginBottom: 0 }}><span className="ds-label">Предоплата ≤ 20%, ₽</span>
                <input type="number" min="0" step="1" max={rentalPrice ? Math.floor(Number(rentalPrice) * 0.2) : undefined} value={deposit} onChange={(e) => setDeposit(e.target.value)} className="ds-input" />
              </label>
            </div>
            <button className="ds-btn ds-btn--ghost ds-btn--sm" style={{ marginTop: 8 }} disabled={updating || quoteInvalid}
              onClick={() => onQuote(booking.id, Number(rentalPrice), Number(deposit))}>
              Сохранить цену и предоплату
            </button>
          </div>
        )}
        {error && <div className="ds-error" role="alert"><AlertCircle size={14} />{error}</div>}
        {open && (
          <>
            <div className="ds-eyebrow" style={{ margin: '2px 0 0' }}>Управление записью</div>
            <div className="sched-actions">
              {(booking.status === 'pending' || booking.status === 'prepayment_sent') && (
                <button className="primary" onClick={() => onStatusChange(booking.id, 'confirmed')}
                  disabled={updating || !!booking.price_requires_confirmation || (booking.is_rental && booking.prepayment_amount == null)}>
                  {booking.status === 'prepayment_sent' ? 'Оплата получена · подтвердить' : 'Подтвердить запись'}
                </button>
              )}
              <button onClick={() => onReschedule(booking)} disabled={updating}><CalendarClock size={14} />Перенести</button>
              <button onClick={() => onStatusChange(booking.id, 'completed')} disabled={updating || awaitingPayment}
                title="Время мастера освободится с этого момента">
                {inProgress ? 'Завершить сейчас' : 'Завершить'}
              </button>
              {!booking.is_rental && (
                <button onClick={() => setChanging((v) => !v)} disabled={updating}>Сменить услугу</button>
              )}
              <button className="danger" onClick={() => onStatusChange(booking.id, 'cancelled')} disabled={updating}>Отменить</button>
            </div>
            {changing && (
              <div className="sched-detail-pay">
                <div className="ds-eyebrow" style={{ margin: 0 }}>На какую услугу заменить</div>
                <small>Используйте, если клиент выбрал не ту. Внесённая предоплата сохраняется, остаток считается от новой цены. Клиенту сообщение не уходит.</small>
                <select className="ds-input" value={newServiceId} onChange={(e) => setNewServiceId(e.target.value)} style={{ marginTop: 6 }}>
                  <option value="">Выберите услугу</option>
                  {services
                    .filter((s) => s.is_active && s.id !== booking.service_id && !s.is_additional)
                    .map((s) => <option key={s.id} value={s.id}>{s.name} · {s.duration_minutes} мин · {money(s.price)}</option>)}
                </select>
                <button className="ds-btn ds-btn--ghost ds-btn--sm" style={{ marginTop: 8 }} disabled={updating || !newServiceId}
                  onClick={() => onChangeService(booking.id, Number(newServiceId))}>
                  Заменить услугу
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </Sheet>
  )
}

// ── Несколько записей в одно время ──
function GroupSheet({ entries, color, onClose, onPick }: {
  entries: AdminBooking[]
  color: (id?: number) => string
  onClose: () => void
  onPick: (b: AdminBooking) => void
}) {
  const sorted = [...entries].sort((a, b) => timeToMin(a.time) - timeToMin(b.time) || (a.employee_id || 0) - (b.employee_id || 0))
  return (
    <Sheet title={`${entries.length} ${entries.length < 5 ? 'записи' : 'записей'} в одно время`} onClose={onClose}>
      <p className="ds-sub" style={{ margin: '0 0 12px' }}>Выберите запись для просмотра и управления</p>
      <div className="sched-group">
        {sorted.map((b) => (
          <button key={b.id} type="button" className="sched-group-item" onClick={() => onPick(b)}>
            <span className="sched-detail-dot" style={{ '--master': color(b.employee_id) } as CSSProperties} />
            <span className="meta">
              <span className="master">{b.employee_name || 'Без специалиста'}</span>
              <strong>{b.client_name}</strong>
              <small>{b.service_name} · {durationOf(b)} мин · {STATUS_LABELS[b.status] || b.status}</small>
            </span>
            <span className="end">
              <span className="time">{b.time}–{endOf(b)}</span>
              <span className="arrow"><ChevronRight size={15} /></span>
            </span>
          </button>
        ))}
      </div>
    </Sheet>
  )
}

// ── Перенос ──
function RescheduleSheet({ booking, employees, onClose, onSubmit, submitting, error }: {
  booking: AdminBooking
  employees: Employee[]
  onClose: () => void
  onSubmit: (date: string, time: string, employeeId?: number) => void
  submitting: boolean
  error: string
}) {
  const [date, setDate] = useState(booking.date)
  const [time, setTime] = useState(booking.time)
  const [employeeId, setEmployeeId] = useState<number | undefined>(booking.employee_id)
  return (
    <Sheet title="Перенести запись" sub={`${booking.client_name} · ${booking.service_name}`} onClose={onClose} z={75}>
      <div className="ds-field"><label>Дата</label><input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="ds-input" /></div>
      <div className="ds-field-row">
        <div className="ds-field"><label>Время</label><input type="time" value={time} onChange={(e) => setTime(e.target.value)} className="ds-input" /></div>
        {employees.length > 0 && (
          <div className="ds-field">
            <label>Специалист / зал</label>
            <select value={employeeId ?? ''} onChange={(e) => setEmployeeId(Number(e.target.value) || undefined)} className="ds-input">
              <option value="">Без изменения</option>
              {employees.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
            </select>
          </div>
        )}
      </div>
      {error && <div className="ds-error"><AlertCircle size={14} />{error}</div>}
      <div className="ds-sheet-actions">
        <button className="ds-btn ds-btn--ghost" onClick={onClose}>Отмена</button>
        <button className="ds-btn ds-btn--primary" onClick={() => onSubmit(date, time, employeeId)} disabled={submitting}>
          {submitting ? <Loader2 size={15} className="animate-spin" /> : <CalendarClock size={15} />}
          {submitting ? 'Переносим…' : 'Перенести'}
        </button>
      </div>
    </Sheet>
  )
}

// ── Новая запись (повтор по неделям) ──
function ManualBookingSheet({
  businessId, services, employees, selectedDate, selectedTime, selectedEmployeeId, onClose, onCreated,
}: {
  businessId: number
  services: Service[]
  employees: Employee[]
  selectedDate: Date
  selectedTime?: string
  selectedEmployeeId?: number | null
  onClose: () => void
  onCreated: (toastMessage?: string) => void
}) {
  const [serviceId, setServiceId] = useState<number | null>(null)
  const [employeeId, setEmployeeId] = useState<number | null>(selectedEmployeeId ?? null)
  const [date, setDate] = useState(formatDateISO(selectedDate))
  const [time, setTime] = useState(selectedTime || '10:00')
  const [clientName, setClientName] = useState('')
  const [clientPhone, setClientPhone] = useState('')
  const [notes, setNotes] = useState('')
  const [repeatEnabled, setRepeatEnabled] = useState(false)
  const [repeatWeeks, setRepeatWeeks] = useState(4)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const dateLabel = dateFromISO(date).toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' })

  const handleSubmit = async () => {
    if (!serviceId || !clientName) return
    setSubmitting(true)
    setError('')
    try {
      const created = await api.createManualBooking({
        business_id: businessId,
        service_id: serviceId,
        employee_id: employeeId ?? undefined,
        date,
        time,
        client_name: clientName,
        client_phone: clientPhone || undefined,
        notes: notes || undefined,
        repeat_weeks: repeatEnabled ? repeatWeeks : undefined,
      })
      onCreated(Array.isArray(created) && created.length > 1 ? `Создано ${created.length} записей` : 'Запись создана')
    } catch (e) {
      setError(parseError(e, 'Ошибка'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Sheet title="Новая запись" sub={dateLabel} onClose={onClose}>
      <div className="ds-field">
        <label>Услуга</label>
        <select value={serviceId ?? ''} onChange={(e) => setServiceId(Number(e.target.value) || null)} className="ds-input">
          <option value="">Выберите услугу</option>
          {services.filter((s) => s.is_active && !s.is_additional).map((s) => (
            <option key={s.id} value={s.id}>{s.name} {s.duration_minutes ? `(${s.duration_minutes} мин)` : ''} {s.price ? `— ${s.price}₽` : ''}</option>
          ))}
        </select>
      </div>
      <div className="ds-field">
        <label>Специалист / зал</label>
        <select value={employeeId ?? ''} onChange={(e) => setEmployeeId(Number(e.target.value) || null)} className="ds-input">
          <option value="">Любой</option>
          {employees.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
        </select>
      </div>
      <div className="ds-field-row">
        <div className="ds-field"><label>Дата</label><input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="ds-input" /></div>
        <div className="ds-field"><label>Время</label><input type="time" value={time} onChange={(e) => setTime(e.target.value)} className="ds-input" /></div>
      </div>
      <div className="ds-field-row">
        <div className="ds-field"><label>Имя клиента</label><input type="text" value={clientName} onChange={(e) => setClientName(e.target.value)} placeholder="Введите имя" className="ds-input" /></div>
        <div className="ds-field"><label>Телефон</label><input type="tel" inputMode="tel" value={clientPhone} onChange={(e) => setClientPhone(e.target.value)} placeholder="+7" className="ds-input" /></div>
      </div>
      <div className="ds-field"><label>Комментарий</label><textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} className="ds-input" /></div>
      <div className="ds-toggle-row">
        <div><div className="t1">Повторять еженедельно</div><div className="t2">То же время и день недели</div></div>
        <button type="button" role="switch" aria-checked={repeatEnabled} aria-label="Повторять еженедельно" className={`ds-toggle ${repeatEnabled ? 'on' : ''}`} onClick={() => setRepeatEnabled((v) => !v)} />
      </div>
      {repeatEnabled && (
        <div className="sched-stepper-row">
          <button type="button" className="ds-icon-btn" onClick={() => setRepeatWeeks((w) => Math.max(2, w - 1))} aria-label="Меньше недель"><Minus size={16} /></button>
          <span>{repeatWeeks} нед</span>
          <button type="button" className="ds-icon-btn" onClick={() => setRepeatWeeks((w) => Math.min(8, w + 1))} aria-label="Больше недель"><Plus size={16} /></button>
          <span className="ds-hint" style={{ margin: 0 }}>Будет создано {repeatWeeks} записей</span>
        </div>
      )}
      {error && <div className="ds-error"><AlertCircle size={14} />{error}</div>}
      <div className="ds-sheet-actions">
        <button className="ds-btn ds-btn--ghost" onClick={onClose}>Отмена</button>
        <button onClick={handleSubmit} disabled={submitting || !serviceId || !clientName} className="ds-btn ds-btn--primary">
          {submitting ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
          {submitting ? 'Записываем…' : repeatEnabled ? `Записать · ${repeatWeeks}` : 'Записать'}
        </button>
      </div>
    </Sheet>
  )
}

// ── Подтверждение переноса (после перетаскивания) ──
function ConfirmMoveDialog({ clientName, fromLabel, toLabel, submitting, onCancel, onConfirm }: {
  clientName: string
  fromLabel: string
  toLabel: string
  submitting: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <div className="ds-center-ov" onClick={onCancel}>
      <div className="ds-dialog" onClick={(e) => e.stopPropagation()}>
        <h3>Перенести запись?</h3>
        <div className="sched-move">
          <strong>{clientName}</strong>
          <span className="old">Было: {fromLabel}</span>
          <span className="new">Станет: {toLabel}</span>
        </div>
        <div className="ds-dialog-actions">
          <button className="ds-btn ds-btn--ghost" onClick={onCancel} disabled={submitting}>Отмена</button>
          <button className="ds-btn ds-btn--primary" onClick={onConfirm} disabled={submitting}>
            {submitting ? <Loader2 size={15} className="animate-spin" /> : <CalendarClock size={15} />}Перенести
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main ──
export function SchedulePage() {
  const { businesses, selectedId: businessId, selectBusiness } = useAdminBusiness()
  const [theme, toggleTheme] = useTheme()
  const online = useConnectivity()
  const navigate = useNavigate()
  const [viewMode, setViewMode] = useState<'week' | 'day'>('day')
  const [weekOffset, setWeekOffset] = useState(0)
  const [dayOffset, setDayOffset] = useState(0)
  const [bookings, setBookings] = useState<AdminBooking[]>([])
  const [loadedQuery, setLoadedQuery] = useState('')
  const [scheduleError, setScheduleError] = useState('')
  const requestSequence = useRef(0)
  const [loading, setLoading] = useState(false)
  const [selectedBooking, setSelectedBooking] = useState<AdminBooking | null>(null)
  const [groupEntries, setGroupEntries] = useState<AdminBooking[] | null>(null)
  const [bookingError, setBookingError] = useState('')
  const [rescheduleBooking, setRescheduleBooking] = useState<AdminBooking | null>(null)
  const [rescheduleSubmitting, setRescheduleSubmitting] = useState(false)
  const [rescheduleError, setRescheduleError] = useState('')
  const [updating, setUpdating] = useState(false)
  const [manual, setManual] = useState<null | { date: Date; time?: string; employeeId?: number | null }>(null)
  const [services, setServices] = useState<Service[]>([])
  const [allEmployees, setAllEmployees] = useState<Employee[]>([])
  const [selectedEmployeeId, setSelectedEmployeeId] = useState<number | null>(null)
  const [hourPx, setHourPx] = useState(DEFAULT_HOUR_PX)
  const [search, setSearch] = useState('')
  const [tools, setTools] = useState<Record<ToolKey, boolean>>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem('schedTools') || 'null')
      if (saved && typeof saved === 'object') return { search: false, filters: false, details: false, legend: true, context: false, ...saved }
    } catch { /* noop */ }
    return { search: false, filters: false, details: false, legend: true, context: false }
  })
  const [toolsOpen, setToolsOpen] = useState(false)
  const [focus, setFocus] = useState(false)
  const [toast, setToast] = useState('')
  const [pull, setPull] = useState<null | { dist: number; refreshing: boolean }>(null)
  const [drag, setDrag] = useState<null | { id: number; clientName: string; color: string; x: number; y: number; newMin: number; durMin: number; colIndex: number }>(null)
  const [pendingMove, setPendingMove] = useState<null | {
    id: number; clientName: string; date: string; time: string; employeeId?: number; fromLabel: string; toLabel: string
  }>(null)
  const [movingSubmit, setMovingSubmit] = useState(false)
  const colsRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const suppressClickUntil = useRef(0)
  const pxPerMin = hourPx / 60

  useEffect(() => { try { localStorage.setItem('schedTools', JSON.stringify(tools)) } catch { /* noop */ } }, [tools])
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(''), 2400)
    return () => clearTimeout(t)
  }, [toast])

  const handleLogout = async () => {
    sessionStorage.setItem('servgoExplicitLogout', '1')
    try { await clearLogin() }
    catch { alert('Не удалось удалить офлайн-данные. Закройте другие вкладки ServGo и повторите выход.'); return }
    navigate('/login', { replace: true })
  }

  // ── Масштаб щипком + Ctrl+колесо ──
  const hourPxRef = useRef(hourPx)
  useEffect(() => { hourPxRef.current = hourPx }, [hourPx])
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const clamp = (v: number) => Math.min(MAX_HOUR_PX, Math.max(MIN_HOUR_PX, Math.round(v)))
    const dist = (t: TouchList) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY)
    let base: { d: number; px: number } | null = null
    const onStart = (e: TouchEvent) => { if (e.touches.length === 2) base = { d: dist(e.touches), px: hourPxRef.current } }
    const onMove = (e: TouchEvent) => {
      if (e.touches.length === 2 && base) {
        e.preventDefault()
        suppressClickUntil.current = Date.now() + 650
        setHourPx(clamp(base.px * (dist(e.touches) / base.d)))
      }
    }
    const onEnd = (e: TouchEvent) => { if (e.touches.length < 2) base = null }
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return
      e.preventDefault()
      setHourPx((p) => clamp(p - e.deltaY * 0.15))
    }
    el.addEventListener('touchstart', onStart, { passive: false })
    el.addEventListener('touchmove', onMove, { passive: false })
    el.addEventListener('touchend', onEnd)
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      el.removeEventListener('touchstart', onStart)
      el.removeEventListener('touchmove', onMove)
      el.removeEventListener('touchend', onEnd)
      el.removeEventListener('wheel', onWheel)
    }
  }, [])

  const employees = useMemo(
    () => allEmployees.filter((e) => e.is_active !== false && (!e.role || e.role === 'master' || e.role === 'master_and_admin')),
    [allEmployees]
  )

  const empColor = useCallback(
    (id?: number) => {
      if (id == null) return '#7f94ad'
      const i = employees.findIndex((e) => e.id === id)
      return i < 0 ? '#7f94ad' : MASTER_PALETTE[i % MASTER_PALETTE.length]
    },
    [employees]
  )

  const weekDates = useMemo(() => {
    const monday = getMonday(new Date())
    monday.setDate(monday.getDate() + weekOffset * 7)
    return Array.from({ length: 7 }, (_, i) => {
      const d = new Date(monday)
      d.setDate(d.getDate() + i)
      return d
    })
  }, [weekOffset])

  const dayDate = useMemo(() => {
    const d = new Date()
    d.setDate(d.getDate() + dayOffset)
    d.setHours(0, 0, 0, 0)
    return d
  }, [dayOffset])

  const periodLabel = useMemo(() => {
    if (viewMode === 'week') {
      if (weekOffset === 0) return 'Эта неделя'
      if (weekOffset === 1) return 'След. неделя'
      if (weekOffset === -1) return 'Прош. неделя'
      return `${formatDateShort(weekDates[0])} — ${formatDateShort(weekDates[6])}`
    }
    if (dayOffset === 0) return 'Сегодня'
    if (dayOffset === 1) return 'Завтра'
    if (dayOffset === -1) return 'Вчера'
    return dayDate.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', weekday: 'short' })
  }, [viewMode, weekOffset, dayOffset, weekDates, dayDate])

  // The displayed data must belong to the selected business and date range.
  const scheduleQuery = useMemo(() => {
    if (!businessId) return ''
    const period = viewMode === 'week'
      ? `&start_date=${formatDateISO(weekDates[0])}&end_date=${formatDateISO(weekDates[6])}`
      : `&date=${formatDateISO(dayDate)}`
    return `business_id=${businessId}${period}`
  }, [businessId, viewMode, weekDates, dayDate])

  const fetchBookings = useCallback(async () => {
    if (!scheduleQuery) return
    const sequence = ++requestSequence.current
    setLoading(true)
    try {
      const result = await api.getAdminBookings(scheduleQuery)
      if (sequence !== requestSequence.current) return
      setBookings(result)
      setLoadedQuery(scheduleQuery)
      setScheduleError('')
    } catch {
      if (sequence !== requestSequence.current) return
      // Never show a different date/business's cached list as this one.
      if (loadedQuery !== scheduleQuery) {
        setBookings([])
        setLoadedQuery('')
      }
      setScheduleError('Не удалось загрузить записи за выбранный период. Если интернета нет, этот период ещё не сохранён.')
    } finally {
      if (sequence === requestSequence.current) setLoading(false)
    }
  }, [scheduleQuery, loadedQuery])

  useEffect(() => {
    // Invalidate all previous responses immediately when selection changes.
    requestSequence.current += 1
    window.dispatchEvent(new Event('servgo-offline-context-reset'))
    setSelectedBooking(null)
    setGroupEntries(null)
    setManual(null)
    setPendingMove(null)
    setScheduleError('')
    if (!scheduleQuery) { setBookings([]); setLoadedQuery(''); setLoading(false); return }
    void fetchBookings()
    // Invalidation belongs to the selected key, not to periodic refreshes.
  }, [scheduleQuery]) // eslint-disable-line react-hooks/exhaustive-deps


  // Keep the current week's days available offline, even if the user only opened
  // today's schedule. The API snapshot store splits this weekly response by day.
  useEffect(() => {
    if (!businessId || !online) return
    const monday = getMonday(new Date())
    const sunday = new Date(monday)
    sunday.setDate(sunday.getDate() + 6)
    const period = `business_id=${businessId}&start_date=${formatDateISO(monday)}&end_date=${formatDateISO(sunday)}`
    void api.getAdminBookings(period).catch(() => { /* background prefetch is optional */ })
  }, [businessId, online])

  useEffect(() => {
    const timer = setInterval(fetchBookings, 15000)
    return () => clearInterval(timer)
  }, [fetchBookings])

  const catalogueSequence = useRef(0)
  useEffect(() => {
    const sequence = ++catalogueSequence.current
    setSelectedEmployeeId(null)
    setServices([])
    setAllEmployees([])
    if (!businessId) return
    Promise.all([api.getServices(businessId), api.getEmployees(businessId)])
      .then(([svc, emp]) => {
        if (sequence !== catalogueSequence.current) return
        setServices(svc)
        setAllEmployees(emp)
      })
      .catch(() => { /* records remain readable with the cached booking details */ })
    return () => { catalogueSequence.current += 1 }
  }, [businessId])

  const replaceBooking = (updated: AdminBooking) => setBookings((prev) => prev.map((b) => (b.id === updated.id ? updated : b)))

  const handleStatusChange = async (bookingId: number, status: string) => {
    setUpdating(true)
    setBookingError('')
    try {
      replaceBooking(await api.updateBookingStatus(bookingId, status))
      setSelectedBooking(null)
      setToast(status === 'confirmed' ? 'Запись подтверждена' : status === 'completed' ? 'Запись завершена' : status === 'cancelled' ? 'Запись отменена' : 'Статус изменён')
      fetchBookings()
    } catch (e) {
      setBookingError(parseError(e, 'Не удалось изменить статус'))
    } finally {
      setUpdating(false)
    }
  }

  const handleChangeService = async (bookingId: number, serviceId: number) => {
    setUpdating(true)
    setBookingError('')
    try {
      replaceBooking(await api.changeBookingService(bookingId, serviceId))
      setSelectedBooking(null)
      setToast('Услуга заменена')
      fetchBookings()
    } catch (e) {
      setBookingError(parseError(e, 'Не удалось заменить услугу'))
    } finally {
      setUpdating(false)
    }
  }

  const handleRentalQuote = async (bookingId: number, price: number, prepayment: number) => {
    setUpdating(true)
    setBookingError('')
    try {
      const updated = await api.updateRentalQuote(bookingId, price, prepayment)
      replaceBooking(updated)
      setSelectedBooking(updated)
    } catch (e) {
      setBookingError(parseError(e, 'Не удалось сохранить стоимость'))
    } finally {
      setUpdating(false)
    }
  }

  const doReschedule = async (bookingId: number, date: string, time: string, employeeId?: number, optimistic = false) => {
    const prev = bookings
    if (optimistic) setBookings((bs) => bs.map((b) => (b.id === bookingId ? { ...b, date, time, employee_id: employeeId ?? b.employee_id } : b)))
    try {
      replaceBooking(await api.adminReschedule(bookingId, date, time, employeeId))
    } catch (e) {
      if (optimistic) setBookings(prev)
      throw e
    }
  }

  const submitReschedule = async (date: string, time: string, employeeId?: number) => {
    if (!rescheduleBooking) return
    setRescheduleSubmitting(true)
    setRescheduleError('')
    try {
      await doReschedule(rescheduleBooking.id, date, time, employeeId)
      setRescheduleBooking(null)
      setSelectedBooking(null)
      setToast('Запись перенесена')
      fetchBookings()
    } catch (e) {
      setRescheduleError(parseError(e, 'Не удалось перенести'))
    } finally {
      setRescheduleSubmitting(false)
    }
  }

  const confirmMove = async () => {
    if (!pendingMove) return
    setMovingSubmit(true)
    try {
      await doReschedule(pendingMove.id, pendingMove.date, pendingMove.time, pendingMove.employeeId, true)
      setToast('Запись перенесена')
    } catch (e) {
      setToast(parseError(e, 'Не удалось перенести'))
    } finally {
      setMovingSubmit(false)
      setPendingMove(null)
    }
  }

  // Filtering: by employee + search
  const visibleBookings = useMemo(() => {
    let list = (loadedQuery === scheduleQuery ? bookings : []).filter(isActive)
    if (selectedEmployeeId) list = list.filter((b) => b.employee_id === selectedEmployeeId)
    const q = search.trim().toLowerCase()
    if (q) {
      list = list.filter((b) =>
        [b.client_name, b.service_name, b.client_phone, b.employee_name].some((v) => (v || '').toLowerCase().includes(q))
      )
    }
    return list
  }, [bookings, loadedQuery, scheduleQuery, selectedEmployeeId, search])

  // Day columns = masters (or single column)
  const dayColumns = useMemo(() => {
    const base = selectedEmployeeId ? employees.filter((e) => e.id === selectedEmployeeId) : employees
    return base.length > 0 ? base : [{ id: 0, name: 'Все записи' } as Employee]
  }, [employees, selectedEmployeeId])

  const colCount = viewMode === 'day' ? dayColumns.length : 7

  const summary = useMemo(() => {
    const revenue = visibleBookings
      .filter((b) => b.status === 'confirmed' || b.status === 'completed')
      .reduce((sum, b) => sum + (b.price || 0), 0)
    const busyMin = visibleBookings.reduce((sum, b) => sum + durationOf(b), 0)
    const load = Math.min(100, Math.round((busyMin / (TOTAL_MIN * colCount)) * 100))
    return { count: visibleBookings.length, revenue, load }
  }, [visibleBookings, colCount])

  const [nowMin, setNowMin] = useState(() => new Date().getHours() * 60 + new Date().getMinutes())
  useEffect(() => {
    const t = setInterval(() => setNowMin(new Date().getHours() * 60 + new Date().getMinutes()), 60000)
    return () => clearInterval(t)
  }, [])

  // Прокрутить к текущему времени один раз при открытии (не на каждом опросе раз в 15 с).
  const didAutoScroll = useRef(false)
  useEffect(() => {
    const el = scrollRef.current
    if (!el || loading || didAutoScroll.current) return
    didAutoScroll.current = true
    const target = (Math.max(DAY_START_H * 60, nowMin - 60) - DAY_START_H * 60) * pxPerMin
    if (el.scrollTop === 0) el.scrollTop = Math.max(0, target)
  }, [loading]) // eslint-disable-line react-hooks/exhaustive-deps

  const colHeight = TOTAL_MIN * pxPerMin
  const currentBizName = businesses.find((b) => b.id === businessId)?.name
  const todayISO = formatDateISO(new Date())
  const hourMarks = Array.from({ length: DAY_END_H - DAY_START_H - 1 }, (_, i) => DAY_START_H + i + 1)
  const showNow =
    (viewMode === 'day' ? formatDateISO(dayDate) === todayISO : weekOffset === 0) &&
    nowMin >= DAY_START_H * 60 && nowMin <= DAY_END_H * 60

  const openBookings = (entries: AdminBooking[]) => {
    if (Date.now() < suppressClickUntil.current) return
    setBookingError('')
    if (entries.length === 1) setSelectedBooking(entries[0])
    else setGroupEntries(entries)
  }

  // ── Перетаскивание одиночной записи (день): long-press на тач, сразу — мышью ──
  const beginDrag = (e: React.PointerEvent, b: AdminBooking) => {
    if (!online || viewMode !== 'day') return
    if (e.pointerType === 'mouse' && e.button !== 0) return
    const startX = e.clientX
    const startY = e.clientY
    const ptrId = e.pointerId
    const el = e.currentTarget as HTMLElement
    const startMin = timeToMin(b.time)
    const durMin = durationOf(b)
    const grabDY = (startY - el.getBoundingClientRect().top)
    let dragging = false
    let holdTimer: number | undefined

    const target = (cx: number, cy: number) => {
      const cont = colsRef.current
      if (!cont) return { colIndex: 0, newMin: startMin }
      const cr = cont.getBoundingClientRect()
      const colW = cr.width / Math.max(1, dayColumns.length)
      const colIndex = Math.max(0, Math.min(dayColumns.length - 1, Math.floor((cx - cr.left) / colW)))
      const rawMin = DAY_START_H * 60 + (cy - grabDY - cr.top) / pxPerMin
      const newMin = Math.max(DAY_START_H * 60, Math.min(DAY_END_H * 60 - durMin, Math.round(rawMin / 5) * 5))
      return { colIndex, newMin }
    }
    const begin = (cx: number, cy: number) => {
      dragging = true
      try { el.setPointerCapture?.(ptrId) } catch { /* noop */ }
      navigator.vibrate?.(12)
      const t = target(cx, cy)
      setDrag({ id: b.id, clientName: b.client_name, color: empColor(b.employee_id), x: cx, y: cy, newMin: t.newMin, durMin, colIndex: t.colIndex })
    }
    const onMove = (ev: PointerEvent) => {
      if (!dragging) {
        const moved = Math.hypot(ev.clientX - startX, ev.clientY - startY)
        if (e.pointerType === 'touch') {
          if (moved > 10) cleanup()
          return
        }
        if (moved > 5) begin(ev.clientX, ev.clientY)
        else return
      }
      ev.preventDefault()
      const t = target(ev.clientX, ev.clientY)
      setDrag((d) => (d ? { ...d, x: ev.clientX, y: ev.clientY, newMin: t.newMin, colIndex: t.colIndex } : d))
    }
    const onUp = (ev: PointerEvent) => {
      const wasDragging = dragging
      cleanup()
      if (!wasDragging) return
      suppressClickUntil.current = Date.now() + 400
      const t = target(ev.clientX, ev.clientY)
      const emp = dayColumns[t.colIndex]
      const empChanged = !!emp && emp.id !== 0 && emp.id !== (b.employee_id || 0)
      setDrag(null)
      if (t.newMin !== startMin || empChanged) {
        const wd = dayDate.toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'short' })
        setPendingMove({
          id: b.id,
          clientName: b.client_name,
          date: formatDateISO(dayDate),
          time: minToTime(t.newMin),
          employeeId: emp?.id || undefined,
          fromLabel: `${b.time}${b.employee_name ? `, ${b.employee_name}` : ''}`,
          toLabel: `${wd}, ${minToTime(t.newMin)}${empChanged ? `, ${emp!.name}` : ''}`,
        })
      }
    }
    function cleanup() {
      dragging = false
      if (holdTimer) { clearTimeout(holdTimer); holdTimer = undefined }
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onCancel)
    }
    // Прерванный жест (браузер забрал его под прокрутку) — не перенос.
    const onCancel = () => {
      const wasDragging = dragging
      cleanup()
      if (wasDragging) { suppressClickUntil.current = Date.now() + 400; setDrag(null) }
    }
    window.addEventListener('pointermove', onMove, { passive: false })
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onCancel)
    if (e.pointerType === 'touch') holdTimer = window.setTimeout(() => begin(startX, startY), 260)
  }

  // ── Потянуть вниз для обновления ──
  const pullStart = useRef<number | null>(null)
  const onTouchStartPull = (e: React.TouchEvent) => {
    pullStart.current = e.touches.length === 1 && (scrollRef.current?.scrollTop ?? 1) <= 0 ? e.touches[0].clientY : null
  }
  const onTouchMovePull = (e: React.TouchEvent) => {
    if (pullStart.current == null || drag) return
    const dist = e.touches[0].clientY - pullStart.current
    if (dist > 8) setPull({ dist, refreshing: false })
    else if (pull) setPull(null)
  }
  const onTouchEndPull = async () => {
    const current = pull
    pullStart.current = null
    if (!current) return
    if (current.dist > 70) {
      setPull({ dist: current.dist, refreshing: true })
      await fetchBookings()
      setToast('Расписание обновлено')
    }
    setPull(null)
  }

  const tapFreeSlot = (e: React.MouseEvent<HTMLDivElement>, date: Date, employeeId: number | null) => {
    if (!online) return
    if (e.target !== e.currentTarget || Date.now() < suppressClickUntil.current) return
    const rect = e.currentTarget.getBoundingClientRect()
    const min = DAY_START_H * 60 + Math.round(((e.clientY - rect.top) / pxPerMin) / 15) * 15
    setManual({ date, time: minToTime(Math.max(DAY_START_H * 60, Math.min(DAY_END_H * 60 - 15, min))), employeeId })
  }

  const renderColumn = (list: AdminBooking[], key: string | number, date: Date, employeeId: number | null, week: boolean, isToday: boolean) => {
    const colWidth = Math.max(140, (scrollRef.current?.clientWidth ?? 340) - GUTTER) / colCount
    return (
      <div key={key} className={`sched-col ${isToday && week ? 'today' : ''}`} onClick={(e) => tapFreeSlot(e, date, employeeId)}>
        {layoutLanes(list).map((l) => {
          const available = colWidth / l.lanes - 2
          const dur = l.endMin - l.startMin
          const hasTime = !week && available >= 55 && dur * pxPerMin >= 34
          const hasName = !week && available >= 70 && dur * pxPerMin >= 56
          const b = l.booking
          const awaiting = b.status === 'pending' || b.status === 'pending_prepayment' || b.status === 'prepayment_sent'
          return (
            <div
              key={b.id}
              className={`sched-block ${week ? 'weekly' : ''} ${awaiting ? 'is-awaiting' : ''} ${b.status === 'completed' ? 'is-done' : ''} ${drag?.id === b.id ? 'is-dragging' : ''}`}
              aria-hidden="true"
              style={{
                '--master': empColor(b.employee_id),
                '--lane': l.lane,
                '--lanes': l.lanes,
                top: (l.startMin - DAY_START_H * 60) * pxPerMin + 1,
                height: Math.max(6, dur * pxPerMin - 2),
              } as CSSProperties}
            >
              {hasTime && <div className="t"><span>{b.time}</span><span>{endOf(b)}</span></div>}
              {hasName && <div className="n">{firstName(b.client_name)}</div>}
            </div>
          )
        })}
        {clustersOf(list).map((c) => {
          const names = c.entries.map((b) => b.client_name).join(', ')
          const label = c.entries.length === 1
            ? `Открыть запись: ${c.entries[0].client_name}, ${c.entries[0].time}`
            : `${c.entries.length} пересекающихся записей: ${names}`
          return (
            <button
              key={`${c.startMin}-${c.entries[0].id}`}
              type="button"
              className="sched-hit"
              aria-label={label}
              title={label}
              style={{ top: (c.startMin - DAY_START_H * 60) * pxPerMin, height: Math.max(8, (c.endMin - c.startMin) * pxPerMin) }}
              onPointerDown={c.entries.length === 1 && !week ? (e) => beginDrag(e, c.entries[0]) : undefined}
              onClick={(e) => { e.stopPropagation(); openBookings(c.entries) }}
            />
          )
        })}
        {drag && !week && dayColumns[drag.colIndex]?.id === (employeeId ?? 0) && (
          <div className="sched-drop" style={{ '--master': drag.color, top: (drag.newMin - DAY_START_H * 60) * pxPerMin, height: drag.durMin * pxPerMin } as CSSProperties}>
            {minToTime(drag.newMin)}–{minToTime(drag.newMin + drag.durMin)}
          </div>
        )}
      </div>
    )
  }

  const nextUp = visibleBookings
    .filter((b) => viewMode === 'week' || b.date === formatDateISO(dayDate))
    .sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time))
    .filter((b) => b.date > todayISO || (b.date === todayISO && timeToMin(b.time) + durationOf(b) >= nowMin) || b.date < todayISO)
    .slice(0, 3)

  const toggleTool = (k: ToolKey) => setTools((t) => ({ ...t, [k]: !t[k] }))
  const shiftPeriod = (dir: number) => (viewMode === 'week' ? setWeekOffset((w) => w + dir) : setDayOffset((d) => d + dir))

  return createPortal(
    <div className={`ds-screen sched-screen ${focus ? 'is-focus' : ''}`} data-theme={theme}>
      {!focus && (
        <header className="ds-shell-header" style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '5px 13px 5px 16px', flexShrink: 0 }}>
          {/* Заголовок не сжимается: длинное название заведения обрезает селектор, а не «Расписание». */}
          <div className="ds-shell-title" style={{ minWidth: 0, maxWidth: '45%' }}>
            <h1 className="ds-h1" style={{ whiteSpace: 'nowrap' }}>Расписание</h1>
            {currentBizName && <div className="sched-hsub"><span className="dot" /><span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{currentBizName}</span></div>}
          </div>
          <div className="ds-shell-header-extra">
            <BusinessSelector businesses={businesses} selectedId={businessId} onSelect={selectBusiness} />
          </div>
          <button className="ds-icon-btn" onClick={toggleTheme} aria-label="Тема">{theme === 'dark' ? <Sun size={19} /> : <Moon size={19} />}</button>
          <button className="ds-icon-btn" onClick={handleLogout} aria-label="Выйти" title="Выйти"><LogOut size={19} /></button>
        </header>
      )}

      <div className="sched-page">
        {tools.context && !focus && (
          <div className="sched-context">
            <div className="ds-context-eyebrow">Расписание · {periodLabel}</div>
            <div className="ds-context-headline">{viewMode === 'week' ? 'Неделя · записи по специалистам' : dayOffset === 0 ? 'Кто записан сегодня' : 'Кто записан в этот день'}</div>
            <div className="sched-context-strip">
              {nextUp.length === 0 ? (
                <div><b>—</b><span>Нет записей</span></div>
              ) : nextUp.map((b) => (
                <div key={b.id}><b>{b.time}–{endOf(b)}</b><span>{firstName(b.employee_name) || b.client_name} · {b.service_name}</span></div>
              ))}
            </div>
          </div>
        )}

        {/* В дне цвет и имя уже в заголовке каждой колонки — второй ряд был дублем. Нужен в неделе, а в дне —
            только пока выбран один специалист, чтобы этот выбор можно было снять. */}
        {tools.legend && (viewMode === 'week' || selectedEmployeeId !== null) && employees.length > 0 && (
          <div className="sched-legend" role="group" aria-label="Цвета специалистов">
            {employees.map((e) => (
              <button
                key={e.id}
                type="button"
                className="sched-legend-tag choice-accent"
                aria-pressed={selectedEmployeeId === e.id}
                title={`Показать только: ${e.name}`}
                onClick={() => setSelectedEmployeeId(selectedEmployeeId === e.id ? null : e.id)}
              >
                <span className="sched-swatch" style={{ '--master': empColor(e.id) } as CSSProperties} />{firstName(e.name)}
              </button>
            ))}
          </div>
        )}

        <div className="sched-colhead" style={{ gridTemplateColumns: `${GUTTER}px repeat(${colCount}, minmax(0, 1fr))` }}>
          <div />
          {viewMode === 'day'
            ? dayColumns.map((emp) => (
                <div key={emp.id} className="sched-dayhead" title={emp.name}>
                  {emp.id !== 0 && <span className="sched-swatch" style={{ '--master': empColor(emp.id) } as CSSProperties} />}
                  <strong>{emp.id === 0 ? dayDate.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }) : firstName(emp.name)}</strong>
                </div>
              ))
            : weekDates.map((d, i) => {
                const n = visibleBookings.filter((b) => b.date === formatDateISO(d)).length
                const today = formatDateISO(d) === todayISO
                return (
                  <button
                    key={i}
                    type="button"
                    className={`sched-dayhead week ${today ? 'today' : ''}`}
                    title={`Открыть ${d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })} по мастерам`}
                    onClick={() => {
                      const t = new Date(); t.setHours(0, 0, 0, 0)
                      setDayOffset(Math.round((d.getTime() - t.getTime()) / 86400000))
                      setViewMode('day')
                    }}
                  >
                    <span>{DAYS_SHORT[i]}</span><strong>{d.getDate()}</strong>{n > 0 && <small>{n}</small>}
                  </button>
                )
              })}
        </div>

        <div
          className="sched-scroll"
          ref={scrollRef}
          aria-label="Календарный график, масштаб — двумя пальцами"
          onTouchStart={onTouchStartPull}
          onTouchMove={onTouchMovePull}
          onTouchEnd={onTouchEndPull}
        >
          {pull && (
            <div className="sched-pull" role="status">
              {pull.refreshing ? <Loader2 size={15} className="animate-spin" /> : <span>{pull.dist > 70 ? '↻' : '↓'}</span>}
              {pull.refreshing ? 'Обновляем…' : pull.dist > 70 ? 'Отпустите для обновления' : 'Потяните вниз для обновления'}
            </div>
          )}
          {scheduleError && loadedQuery !== scheduleQuery ? (
            <div className="sched-empty" role="status">{scheduleError}</div>
          ) : loading && loadedQuery !== scheduleQuery ? (
            <div className="sched-empty"><Loader2 size={20} className="animate-spin" /> Загрузка…</div>
          ) : (
            <div className="sched-timeline" style={{ height: colHeight, '--hour': `${hourPx}px` } as CSSProperties}>
              <div className="sched-gutter">
                {hourMarks.map((h) => (
                  <span key={h} className="sched-hour" style={{ top: (h - DAY_START_H) * hourPx }}>{h}:00</span>
                ))}
              </div>
              <div className="sched-cols" ref={colsRef} style={{ gridTemplateColumns: `repeat(${colCount}, minmax(0, 1fr))` }}>
                {viewMode === 'day'
                  ? dayColumns.map((emp) => {
                      const dateStr = formatDateISO(dayDate)
                      const list = visibleBookings.filter((b) => b.date === dateStr && (emp.id === 0 || b.employee_id === emp.id))
                      return renderColumn(list, emp.id, dayDate, emp.id || null, false, false)
                    })
                  : weekDates.map((d, i) => {
                      const dateStr = formatDateISO(d)
                      return renderColumn(visibleBookings.filter((b) => b.date === dateStr), i, d, null, true, dateStr === todayISO)
                    })}
              </div>
              {showNow && (
                <div className="sched-now" style={{ top: (nowMin - DAY_START_H * 60) * pxPerMin }}>
                  <span className="knob" />
                  {viewMode === 'day' && <span className="lbl">{minToTime(nowMin)}</span>}
                </div>
              )}
            </div>
          )}

        </div>

        <div className="sched-floating">
          {hourPx !== DEFAULT_HOUR_PX && (
            <button className="sched-zoom-reset" onClick={() => setHourPx(DEFAULT_HOUR_PX)} aria-label="Вернуть масштаб 100%" title="Сбросить масштаб"><RotateCcw size={18} /></button>
          )}
          {businessId && (
            <button className="sched-fab" disabled={!online} title={online ? 'Записать клиента' : 'Изменения доступны только при подключении'} onClick={() => {
              // Сегодня — сразу подставляем ближайшее время от текущей минуты (округляем вверх до 5 минут).
              const date = viewMode === 'week' ? new Date() : dayDate
              const isToday = formatDateISO(date) === todayISO
              const rounded = Math.ceil(nowMin / 5) * 5
              setManual({ date, time: isToday ? minToTime(Math.min(rounded, 23 * 60 + 55)) : undefined, employeeId: selectedEmployeeId })
            }} aria-label="Записать клиента">
              <Plus size={22} />
            </button>
          )}
        </div>

        <div className="sched-dock">
          <div className="ds-seg">
            <button onClick={() => setViewMode('day')} className={`choice-accent ${viewMode === 'day' ? 'active' : ''}`}>День</button>
            <button onClick={() => setViewMode('week')} className={`choice-accent ${viewMode === 'week' ? 'active' : ''}`}>Неделя</button>
          </div>
          <div className="sched-stepper">
            <button onClick={() => shiftPeriod(-1)} aria-label="Предыдущая дата"><ChevronLeft size={17} /></button>
            <button className="label" onClick={() => { setWeekOffset(0); setDayOffset(0) }} aria-label="Вернуться к текущей дате">{periodLabel}</button>
            <button onClick={() => shiftPeriod(1)} aria-label="Следующая дата"><ChevronRight size={17} /></button>
          </div>
          <button className="sched-mini" aria-expanded={toolsOpen} aria-label="Настроить вид" title="Показать инструменты" onClick={() => setToolsOpen((v) => !v)}>
            <SlidersHorizontal size={16} />
          </button>
          <button className="sched-mini" aria-pressed={focus} aria-label={focus ? 'Свернуть график' : 'Развернуть график'} title={focus ? 'Вернуть панель' : 'Развернуть график'} onClick={() => setFocus((v) => !v)}>
            {focus ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
          </button>
        </div>

        {toolsOpen && (
          <div className="sched-tools">
            {(Object.keys(TOOL_LABELS) as ToolKey[]).map((k) => (
              <button key={k} type="button" aria-pressed={tools[k]} onClick={() => toggleTool(k)}>{TOOL_LABELS[k]}</button>
            ))}
          </div>
        )}

        {tools.search && (
          <div className="ds-search sched-search">
            <Search size={16} />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по клиенту, услуге, телефону" />
            {search && <button className="ds-icon-btn" style={{ width: 24, height: 24 }} onClick={() => setSearch('')} aria-label="Очистить">×</button>}
          </div>
        )}

        {tools.details && (
          <div className="sched-summary">
            <div className="ds-tile"><div className="num">{summary.count}</div><div className="lbl">записей</div></div>
            <div className="ds-tile"><div className="num">{summary.revenue.toLocaleString('ru-RU')}</div><div className="lbl">выручка ₽</div></div>
            <div className="ds-tile"><div className="num">{summary.load}%</div><div className="lbl">загрузка</div></div>
          </div>
        )}

        {tools.filters && employees.length > 0 && (
          <div className="sched-filters">
            <button onClick={() => setSelectedEmployeeId(null)} className={`ds-chip choice-accent ${selectedEmployeeId === null ? 'active' : ''}`}>Все</button>
            {employees.map((emp) => (
              <button key={emp.id} onClick={() => setSelectedEmployeeId(selectedEmployeeId === emp.id ? null : emp.id)} className={`ds-chip choice-accent ${selectedEmployeeId === emp.id ? 'active' : ''}`}>
                <span className="sched-swatch" style={{ '--master': empColor(emp.id) } as CSSProperties} />{emp.name}
              </button>
            ))}
          </div>
        )}
      </div>

      {!focus && (
        <AdminBottomNav theme={theme} toggleTheme={toggleTheme} onLogout={handleLogout} />
      )}

      {drag && (
        <div className="sched-ghost" style={{ left: drag.x, top: drag.y, '--master': drag.color } as CSSProperties}>
          <strong>{drag.clientName}</strong>
          <small>{minToTime(drag.newMin)}–{minToTime(drag.newMin + drag.durMin)}{dayColumns[drag.colIndex]?.id ? ` · ${dayColumns[drag.colIndex].name}` : ''}</small>
        </div>
      )}

      {toast && <div className="ds-toast" role="status">{toast}</div>}

      {groupEntries && (
        <GroupSheet
          entries={groupEntries}
          color={empColor}
          onClose={() => setGroupEntries(null)}
          onPick={(b) => { setGroupEntries(null); setBookingError(''); setSelectedBooking(b) }}
        />
      )}

      {pendingMove && (
        <ConfirmMoveDialog
          clientName={pendingMove.clientName}
          fromLabel={pendingMove.fromLabel}
          toLabel={pendingMove.toLabel}
          submitting={movingSubmit}
          onCancel={() => setPendingMove(null)}
          onConfirm={confirmMove}
        />
      )}

      {selectedBooking && (
        <BookingDetailSheet
          booking={selectedBooking}
          color={empColor(selectedBooking.employee_id)}
          onClose={() => setSelectedBooking(null)}
          services={services}
          inProgress={selectedBooking.date === todayISO && ['pending', 'confirmed', 'prepayment_confirmed'].includes(selectedBooking.status) &&
            timeToMin(selectedBooking.time) <= nowMin && nowMin < timeToMin(selectedBooking.time) + durationOf(selectedBooking)}
          onStatusChange={handleStatusChange}
          onChangeService={handleChangeService}
          onQuote={handleRentalQuote}
          error={bookingError}
          onReschedule={(b) => { setRescheduleError(''); setRescheduleBooking(b) }}
          updating={updating || !online}
        />
      )}

      {rescheduleBooking && (
        <RescheduleSheet
          booking={rescheduleBooking}
          employees={employees}
          onClose={() => setRescheduleBooking(null)}
          onSubmit={submitReschedule}
          submitting={rescheduleSubmitting}
          error={rescheduleError}
        />
      )}

      {manual && businessId && (
        <ManualBookingSheet
          businessId={businessId}
          services={services}
          employees={employees}
          selectedDate={manual.date}
          selectedTime={manual.time}
          selectedEmployeeId={manual.employeeId}
          onClose={() => setManual(null)}
          onCreated={(message) => {
            setManual(null)
            fetchBookings()
            if (message) setToast(message)
          }}
        />
      )}
    </div>,
    document.body
  )
}
