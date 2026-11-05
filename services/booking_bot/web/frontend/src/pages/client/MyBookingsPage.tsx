import { useState, useEffect, useMemo } from 'react'
import {
  ClipboardList,
  RefreshCw,
  Calendar,
  ChevronLeft,
  ChevronRight,
  User,
} from 'lucide-react'
import { api, ApiError, type Booking, type SlotAvailability } from '../../api/client'
import { clearLogin } from '../../authUtils'
import { hapticSelection, hapticSuccess, hapticError } from '../../utils/telegram'
import { ClientShell } from '../../components/ClientShell'
import { NotificationSetup } from '../../components/NotificationSetup'
import { downloadBookingIcs } from '../../utils/calendar'
import { useNavigate } from 'react-router-dom'
import { useConnectivity } from '../../utils/useConnectivity'

// ── Status config ──

const STATUS_LABELS: Record<string, string> = {
  pending: 'Ожидает',
  confirmed: 'Подтверждено',
  completed: 'Завершено',
  cancelled: 'Отменено',
  pending_prepayment: 'Ждёт оплату',
  prepayment_sent: 'Оплата отправлена',
}

const STATUS_BADGE: Record<string, string> = {
  pending: 'ds-badge--pending',
  confirmed: 'ds-badge--confirmed',
  completed: 'ds-badge--completed',
  cancelled: 'ds-badge--cancelled',
  pending_prepayment: 'ds-badge--prepay',
  prepayment_sent: 'ds-badge--prepay',
}

function statusBadgeClass(status: string) {
  return STATUS_BADGE[status] || 'ds-badge--pending'
}

function statusLabel(status: string) {
  return STATUS_LABELS[status] || status
}

// ── Helpers ──

const MONTHS_RU = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]

function formatDateRu(dateStr: string) {
  const d = new Date(dateStr)
  const months = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
  ]
  const days = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота']
  return `${d.getDate()} ${months[d.getMonth()]}, ${days[d.getDay()]}`
}

function formatDateShort(dateStr: string) {
  const d = new Date(dateStr)
  const months = [
    'янв', 'фев', 'мар', 'апр', 'мая', 'июн',
    'июл', 'авг', 'сен', 'окт', 'ноя', 'дек',
  ]
  return `${d.getDate()} ${months[d.getMonth()]}`
}

function isToday(dateStr: string) {
  const today = new Date()
  const d = new Date(dateStr)
  return (
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate()
  )
}

function isPast(dateStr: string) {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const d = new Date(dateStr)
  d.setHours(0, 0, 0, 0)
  return d < today
}

function pad2(n: number) {
  return n.toString().padStart(2, '0')
}

function dateToString(d: Date) {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

// ── Inline Calendar ──

function InlineCalendar({
  selected,
  onSelect,
}: {
  selected: string | null
  onSelect: (date: string) => void
}) {
  const today = new Date()
  const [currentMonth, setCurrentMonth] = useState(
    new Date(today.getFullYear(), today.getMonth(), 1)
  )

  const days = useMemo(() => {
    const year = currentMonth.getFullYear()
    const month = currentMonth.getMonth()
    const firstDay = new Date(year, month, 1).getDay()
    const daysInMonth = new Date(year, month + 1, 0).getDate()
    const result: (Date | null)[] = []
    const startIdx = firstDay === 0 ? 6 : firstDay - 1
    for (let i = 0; i < startIdx; i++) result.push(null)
    for (let d = 1; d <= daysInMonth; d++) result.push(new Date(year, month, d))
    return result
  }, [currentMonth])

  const prevMonth = () =>
    setCurrentMonth(new Date(currentMonth.getFullYear(), currentMonth.getMonth() - 1, 1))
  const nextMonth = () =>
    setCurrentMonth(new Date(currentMonth.getFullYear(), currentMonth.getMonth() + 1, 1))

  return (
    <div style={{ background: 'var(--s-surface-2)', borderRadius: 'var(--r-lg)', padding: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <button
          onClick={prevMonth}
          className="ds-icon-btn"
          aria-label="Предыдущий месяц"
        >
          <ChevronLeft size={18} />
        </button>
        <h4 className="ds-h3">
          {MONTHS_RU[currentMonth.getMonth()]} {currentMonth.getFullYear()}
        </h4>
        <button
          onClick={nextMonth}
          className="ds-icon-btn"
          aria-label="Следующий месяц"
        >
          <ChevronRight size={18} />
        </button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 4, marginBottom: 4 }}>
        {['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].map((d) => (
          <div key={d} className="ds-caption" style={{ textAlign: 'center', padding: '4px 0' }}>
            {d}
          </div>
        ))}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 4 }}>
        {days.map((day, i) => {
          if (!day) return <div key={`e-${i}`} />
          const ds = dateToString(day)
          const isSelected = ds === selected
          const isPastDay = isPast(ds) && !isToday(ds)
          return (
            <button
              key={ds}
              disabled={isPastDay}
              onClick={() => {
                hapticSelection()
                onSelect(ds)
              }}
              style={{
                fontSize: 13,
                borderRadius: 'var(--r-sm)',
                padding: '7px 0',
                minHeight: 34,
                border: 'none',
                fontFamily: 'inherit',
                cursor: isPastDay ? 'not-allowed' : 'pointer',
                color: isPastDay
                  ? 'var(--s-muted-2)'
                  : isSelected
                    ? 'var(--s-accent-ink)'
                    : isToday(ds)
                      ? 'var(--s-accent)'
                      : 'var(--s-text)',
                fontWeight: isToday(ds) || isSelected ? 700 : 500,
                background: isSelected ? 'var(--s-accent)' : 'transparent',
                opacity: isPastDay ? 0.5 : 1,
              }}
            >
              {day.getDate()}
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ── Time Slot Picker ──

function TimeSlotPicker({
  slots,
  selected,
  onSelect,
  loading,
}: {
  slots: SlotAvailability[]
  selected: string | null
  onSelect: (time: string) => void
  loading: boolean
}) {
  if (loading) {
    return (
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="skeleton" style={{ height: 36, width: 64, borderRadius: 'var(--r-md)' }} />
        ))}
      </div>
    )
  }
  if (slots.length === 0) {
    return (
      <p className="ds-sub" style={{ marginTop: 12 }}>Нет времени в рабочем расписании на эту дату</p>
    )
  }
  return (
    <>
      <div className="ds-sub" style={{ display: 'flex', gap: 14, marginTop: 10, fontSize: 12 }}>
        <span>● свободно</span>
        <span style={{ opacity: 0.55 }}>● занято</span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
        {slots.map((slot) => (
          <button
            key={slot.time}
            disabled={!slot.available}
            title={slot.available ? 'Свободно' : 'Занято'}
            onClick={() => {
              if (!slot.available) return
              hapticSelection()
              onSelect(slot.time)
            }}
            className={`ds-btn ds-btn--sm${selected === slot.time ? ' ds-btn--primary' : ' ds-btn--ghost'}`}
            style={!slot.available ? {
              opacity: 0.45,
              cursor: 'not-allowed',
              textDecoration: 'line-through',
            } : undefined}
          >
            {slot.time}
          </button>
        ))}
      </div>
    </>
  )
}

// ── Cancel Confirmation Modal ──

function CancelModal({
  booking,
  onConfirm,
  onClose,
  loading,
}: {
  booking: Booking
  onConfirm: () => void
  onClose: () => void
  loading: boolean
}) {
  return (
    <div className="ds-center-ov" onClick={onClose}>
      <div className="ds-dialog" onClick={(e) => e.stopPropagation()}>
        <h3>Отменить запись?</h3>
        <p>
          {booking.service_name} — {formatDateRu(booking.date)}, {booking.time}
        </p>
        <div className="ds-dialog-actions">
          <button onClick={onClose} className="ds-btn ds-btn--ghost">
            Нет, оставить
          </button>
          <button onClick={onConfirm} disabled={loading} className="ds-btn ds-btn--danger">
            {loading ? 'Отменяем...' : 'Да, отменить'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Reschedule Panel ──

function ReschedulePanel({
  booking,
  onConfirm,
  onClose,
  loading,
}: {
  booking: Booking
  onConfirm: (date: string, time: string) => void
  onClose: () => void
  loading: boolean
}) {
  const [newDate, setNewDate] = useState<string | null>(null)
  const [newTime, setNewTime] = useState<string | null>(null)
  const [slots, setSlots] = useState<SlotAvailability[]>([])
  const [slotsLoading, setSlotsLoading] = useState(false)

  useEffect(() => {
    if (!newDate) {
      setSlots([])
      setNewTime(null)
      return
    }
    setSlotsLoading(true)
    setNewTime(null)
    api
      .getSlotAvailability(
        booking.business_id,
        booking.service_id,
        newDate,
        booking.employee_id,
        booking.duration_minutes || 60,
        booking.id
      )
      .then((result) => {
        setSlots(result)
      })
      .catch(() => {
        setSlots([])
      })
      .finally(() => {
        setSlotsLoading(false)
      })
  }, [newDate, booking.business_id, booking.service_id, booking.employee_id, booking.duration_minutes, booking.id])

  return (
    <div style={{ marginTop: 14, borderTop: '1px solid var(--s-line)', paddingTop: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <h4 className="ds-h3">Перенести запись</h4>
        <button onClick={onClose} className="ds-sub" style={{ background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit' }}>
          Отмена
        </button>
      </div>

      {/* Old vs New comparison */}
      {newDate && newTime && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14, padding: 12, background: 'var(--s-surface-2)', borderRadius: 'var(--r-lg)' }}>
          <div style={{ flex: 1, textAlign: 'center' }}>
            <p className="ds-caption" style={{ textTransform: 'uppercase', marginBottom: 4 }}>Сейчас</p>
            <p className="ds-text" style={{ textDecoration: 'line-through', opacity: 0.6 }}>
              {formatDateShort(booking.date)}, {booking.time}
            </p>
          </div>
          <ChevronRight size={16} style={{ color: 'var(--s-muted)' }} />
          <div style={{ flex: 1, textAlign: 'center' }}>
            <p className="ds-caption ds-accent" style={{ textTransform: 'uppercase', marginBottom: 4 }}>Новое</p>
            <p className="ds-text ds-accent" style={{ fontWeight: 700 }}>
              {formatDateShort(newDate)}, {newTime}
            </p>
          </div>
        </div>
      )}

      <InlineCalendar selected={newDate} onSelect={setNewDate} />

      {newDate && (
        <div style={{ marginTop: 12 }}>
          <p className="ds-text" style={{ fontWeight: 650, marginBottom: 4 }}>Выберите время:</p>
          <TimeSlotPicker
            slots={slots}
            selected={newTime}
            onSelect={setNewTime}
            loading={slotsLoading}
          />
        </div>
      )}

      {newDate && newTime && (
        <button
          onClick={() => onConfirm(newDate, newTime)}
          disabled={loading}
          className="ds-btn ds-btn--primary ds-btn--block"
          style={{ marginTop: 14 }}
        >
          {loading ? 'Переносим...' : 'Подтвердить перенос'}
        </button>
      )}
    </div>
  )
}

// ── Main Component ──

export function MyBookingsPage() {
  const navigate = useNavigate()
  const online = useConnectivity()
  const [bookings, setBookings] = useState<Booking[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [authExpired, setAuthExpired] = useState(false)
  const [reload, setReload] = useState(0)
  useEffect(() => {
    const refresh = () => { if (navigator.onLine) setReload(value => value + 1) }
    window.addEventListener('online', refresh)
    window.addEventListener('focus', refresh)
    return () => { window.removeEventListener('online', refresh); window.removeEventListener('focus', refresh) }
  }, [])
  const [cancellingId, setCancellingId] = useState<number | null>(null)
  const [reschedulingId, setReschedulingId] = useState<number | null>(null)
  const [rescheduleLoading, setRescheduleLoading] = useState(false)
  const [cancelModalBooking, setCancelModalBooking] = useState<Booking | null>(null)

  useEffect(() => {
    let active = true
    api.getMyBookings()
      .then(result => {
        if (active) {
          setBookings(result)
          setLoadError('')
        }
      })
      .catch(error => {
        if (!active) return
        // Never present a failed request as an empty booking history.
        const expired = error instanceof ApiError && error.status === 401
        setAuthExpired(expired)
        setLoadError(expired
          ? 'Срок действия входа истёк. Войдите в прежний аккаунт, чтобы увидеть свои записи.'
          : 'Не удалось загрузить записи. Попробуйте ещё раз.')
        if (expired) void clearLogin().catch(error => console.warn('Offline cleanup after expired login:', error))
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [reload])

  // Group bookings by date category
  const groupedBookings = useMemo(() => {
    const today: Booking[] = []
    const upcoming: Booking[] = []
    const past: Booking[] = []

    const sorted = [...bookings].sort((a, b) => {
      const dateA = `${a.date}T${a.time}`
      const dateB = `${b.date}T${b.time}`
      return dateA.localeCompare(dateB)
    })

    for (const b of sorted) {
      if (b.status === 'cancelled') {
        past.push(b)
      } else if (isToday(b.date)) {
        today.push(b)
      } else if (isPast(b.date)) {
        past.push(b)
      } else {
        upcoming.push(b)
      }
    }

    return { today, upcoming, past }
  }, [bookings])

  const handleCancel = async (booking: Booking) => {
    setCancelModalBooking(booking)
  }

  const confirmCancel = async () => {
    if (!cancelModalBooking) return
    const id = cancelModalBooking.id
    setCancellingId(id)
    try {
      await api.cancelBooking(id)
      hapticSuccess()
      setBookings((prev) =>
        prev.map((b) => (b.id === id ? { ...b, status: 'cancelled' as const } : b))
      )
    } catch {
      hapticError()
      alert('Не удалось отменить запись. Попробуйте снова.')
    } finally {
      setCancellingId(null)
      setCancelModalBooking(null)
    }
  }

  const handleReschedule = async (bookingId: number, date: string, time: string) => {
    setRescheduleLoading(true)
    try {
      await api.rescheduleBooking(bookingId, date, time)
      hapticSuccess()
      setBookings((prev) =>
        prev.map((b) => (b.id === bookingId ? { ...b, date, time } : b))
      )
      setReschedulingId(null)
    } catch {
      hapticError()
      alert('Не удалось перенести запись. Попробуйте снова.')
      setReschedulingId(null)
    } finally {
      setRescheduleLoading(false)
    }
  }

  // ── Loading state ──
  if (loading) {
    return (
      <ClientShell title="Мои записи" studioEntry>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, paddingTop: 14 }}>
          {[1, 2, 3].map((i) => (
            <div key={i} className="skeleton" style={{ height: 84, borderRadius: 'var(--r-lg)' }} />
          ))}
        </div>
      </ClientShell>
    )
  }

  if (loadError) {
    return (
      <ClientShell title="Мои записи" studioEntry>
        <div role="alert" className="ds-card" style={{ marginTop: 24, padding: 20 }}>
          <h2 className="ds-h2">Записи временно недоступны</h2>
          <p className="ds-sub" style={{ margin: '12px 0' }}>{loadError}</p>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            {authExpired && <button className="ds-btn ds-btn--primary" onClick={() => navigate('/login')}>Войти снова</button>}
            {!authExpired && <button className="ds-btn ds-btn--primary" onClick={() => { setLoading(true); setReload(value => value + 1) }}>Повторить загрузку</button>}
          </div>
        </div>
      </ClientShell>
    )
  }

  // ── Empty state ──
  if (bookings.length === 0) {
    return (
      <ClientShell title="Мои записи" studioEntry>
        <div style={{ textAlign: 'center', padding: '64px 16px' }}>
          <div
            style={{
              width: 64, height: 64, background: 'var(--s-surface-2)', borderRadius: 'var(--r-lg)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px',
            }}
          >
            <ClipboardList size={28} style={{ color: 'var(--s-muted)' }} />
          </div>
          <h2 className="ds-h2" style={{ marginBottom: 8 }}>
            У вас пока нет записей
          </h2>
          <p className="ds-sub" style={{ marginBottom: 20 }}>
            Выберите салон и запишитесь на удобное время
          </p>
          <button onClick={() => navigate('/')} className="ds-btn ds-btn--primary">
            Найти салон
          </button>
        </div>
      </ClientShell>
    )
  }

  // ── Booking card renderer ──
  const renderBookingCard = (booking: Booking) => {
    const statusAllowsModify = booking.status === 'confirmed' || booking.status === 'pending'
    const canCancel = online && (booking.can_cancel !== undefined ? booking.can_cancel : statusAllowsModify)
    const canReschedule = online && (booking.can_reschedule !== undefined ? booking.can_reschedule : statusAllowsModify)
    const canModify = canCancel || canReschedule
    const canAddCalendar = booking.status !== 'cancelled' && booking.status !== 'completed' && !isPast(booking.date)
    const isRescheduling = reschedulingId === booking.id

    return (
      <div key={booking.id} className="ds-card" style={{ padding: 12 }}>
        <div className="ds-row" style={{ cursor: 'default' }}>
          <span className="row-lead">
            <span className="t1">{booking.time}</span>
            <span className="t2">{formatDateRu(booking.date)}</span>
          </span>
          <span className="row-main">
            <span className="row-title">{booking.service_name}</span>
            <span className="row-sub">
              {booking.business_name}
              {booking.employee_name ? ` · ${booking.employee_name}` : ''}
            </span>
          </span>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6, flexShrink: 0 }}>
            <span className={`ds-badge ${statusBadgeClass(booking.status)}`}>
              {statusLabel(booking.status)}
            </span>
            {!!booking.price && (
              <span className="ds-text" style={{ fontWeight: 700 }}>
                {new Intl.NumberFormat('ru-RU').format(booking.price)} ₽
              </span>
            )}
          </div>
        </div>

        {booking.employee_name && (
          <p className="ds-sub" style={{ display: 'flex', alignItems: 'center', gap: 6, margin: '10px 0 0' }}>
            <User size={13} style={{ flexShrink: 0 }} />
            {booking.employee_name}
          </p>
        )}

        {(canModify || canAddCalendar) && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--s-line)', flexWrap: 'wrap' }}>
            {canAddCalendar && (
              <button
                onClick={() => downloadBookingIcs({
                  businessName: booking.business_name,
                  serviceName: booking.service_name,
                  date: booking.date,
                  time: booking.time,
                  durationMinutes: booking.duration_minutes || 60,
                  address: booking.business_address,
                  description: booking.employee_name ? `${booking.service_name} · ${booking.employee_name}` : booking.service_name,
                })}
                className="ds-sub ds-accent"
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 650, display: 'flex', alignItems: 'center', gap: 5 }}
              >
                <Calendar size={14} />
                В календарь
              </button>
            )}
            {canReschedule && (
              <button
                onClick={() => {
                  hapticSelection()
                  setReschedulingId(isRescheduling ? null : booking.id)
                }}
                className="ds-sub ds-accent"
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 650, display: 'flex', alignItems: 'center', gap: 5 }}
              >
                <RefreshCw size={14} />
                Перенести
              </button>
            )}
            {canCancel && (
              <button
                onClick={() => handleCancel(booking)}
                className="ds-sub"
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 650, color: 'var(--s-danger)' }}
              >
                Отменить
              </button>
            )}
          </div>
        )}

        {/* Inline reschedule panel */}
        {isRescheduling && (
          <ReschedulePanel
            booking={booking}
            onConfirm={(date, time) => handleReschedule(booking.id, date, time)}
            onClose={() => setReschedulingId(null)}
            loading={rescheduleLoading}
          />
        )}
      </div>
    )
  }

  // ── Section renderer ──
  const renderSection = (title: string, items: Booking[]) => {
    if (items.length === 0) return null
    return (
      <div style={{ marginBottom: 20 }}>
        <div className="ds-caption" style={{ textTransform: 'uppercase', marginBottom: 10, marginTop: 14 }}>
          {title}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {items.map(renderBookingCard)}
        </div>
      </div>
    )
  }

  return (
    <ClientShell title="Мои записи" studioEntry>
      <NotificationSetup />
      {renderSection('Сегодня', groupedBookings.today)}
      {renderSection('Предстоящие', groupedBookings.upcoming)}
      {renderSection('Прошедшие', groupedBookings.past)}

      {/* Cancel confirmation modal */}
      {cancelModalBooking && (
        <CancelModal
          booking={cancelModalBooking}
          onConfirm={confirmCancel}
          onClose={() => setCancelModalBooking(null)}
          loading={cancellingId === cancelModalBooking.id}
        />
      )}
    </ClientShell>
  )
}
