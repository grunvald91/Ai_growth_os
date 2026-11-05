import { useCallback, useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  Bell,
  Calendar,
  CheckCircle2,
  Clock,
  MapPin,
  Phone,
  RefreshCw,
  XCircle,
} from 'lucide-react'
import { ClientShell } from '../../components/ClientShell'
import { NotificationSetup } from '../../components/NotificationSetup'
import { api, type PublicBooking } from '../../api/client'
import { downloadBookingIcs } from '../../utils/calendar'

function formatPrice(value?: number | null) {
  if (!value) return '0 ₽'
  return `${new Intl.NumberFormat('ru-RU').format(value)} ₽`
}

function formatDate(date: string) {
  const value = new Date(`${date}T00:00:00`)
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    weekday: 'short',
  }).format(value)
}

function statusView(booking: PublicBooking) {
  if (booking.status === 'confirmed' || booking.prepayment_confirmed) {
    return {
      icon: <CheckCircle2 size={36} />,
      title: booking.prepayment_enabled ? 'Предоплата подтверждена' : 'Запись подтверждена',
      text: 'Владелец подтвердил запись. Всё готово — ждём вас в назначенное время.',
      tone: 'var(--s-success)',
    }
  }
  if (booking.status === 'prepayment_sent') {
    return {
      icon: <Clock size={36} />,
      title: 'Оплата отправлена',
      text: 'Мы сообщили владельцу. Статус обновится здесь автоматически, как только предоплата будет подтверждена.',
      tone: 'var(--s-warning)',
    }
  }
  if (booking.status === 'pending_prepayment') {
    return {
      icon: <Clock size={36} />,
      title: 'Предоплата пока не подтверждена',
      text: 'Проверьте реквизиты и оплату. После отправки оплаты владелец должен подтвердить её получение.',
      tone: 'var(--s-warning)',
    }
  }
  if (booking.status === 'cancelled') {
    return {
      icon: <XCircle size={36} />,
      title: 'Запись отменена',
      text: 'Эта запись больше не активна.',
      tone: 'var(--s-danger)',
    }
  }
  if (booking.status === 'completed') {
    return {
      icon: <CheckCircle2 size={36} />,
      title: 'Визит завершён',
      text: 'Спасибо, что воспользовались ServGo.',
      tone: 'var(--s-success)',
    }
  }
  return {
    icon: <Clock size={36} />,
    title: 'Запись создана',
    text: 'Статус записи обновится здесь автоматически.',
    tone: 'var(--s-accent)',
  }
}

export function BookingStatusPage() {
  const { token } = useParams()
  const [booking, setBooking] = useState<PublicBooking | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [paymentBusy, setPaymentBusy] = useState(false)
  const [receipt, setReceipt] = useState<File | null>(null)
  const bookingStatus = booking?.status

  const load = useCallback(async (silent = false) => {
    if (!token) {
      setError('Некорректная ссылка на запись')
      setLoading(false)
      return
    }
    if (!silent) setRefreshing(true)
    try {
      const result = await api.getBookingByToken(token)
      setBooking(result)
      setError(null)
    } catch {
      setError('Не удалось получить статус записи. Проверьте соединение и попробуйте ещё раз.')
    } finally {
      setLoading(false)
      if (!silent) setRefreshing(false)
    }
  }, [token])

  useEffect(() => {
    void load(true)
  }, [load])

  useEffect(() => {
    if (!bookingStatus || !['prepayment_sent', 'pending_prepayment', 'pending'].includes(bookingStatus)) {
      return
    }
    const id = window.setInterval(() => {
      void load(true)
    }, 3000)
    return () => window.clearInterval(id)
  }, [bookingStatus, load])

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') void load(true)
    }
    const onFocus = () => void load(true)
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onFocus)
    return () => {
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', onFocus)
    }
  }, [load])

  if (loading) {
    return (
      <ClientShell title="Статус записи" backTo="/">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, paddingTop: 18 }}>
          {[1, 2, 3].map((item) => (
            <div key={item} className="skeleton" style={{ height: 92, borderRadius: 'var(--r-lg)' }} />
          ))}
        </div>
      </ClientShell>
    )
  }

  if (!booking) {
    return (
      <ClientShell title="Статус записи" backTo="/">
        <div className="ds-card" style={{ marginTop: 18, padding: 18, textAlign: 'center' }}>
          <XCircle size={34} style={{ marginBottom: 10, color: 'var(--s-danger)' }} />
          <h2 className="ds-h2">Страница записи недоступна</h2>
          <p className="ds-sub" style={{ marginTop: 8 }}>{error || 'Запись не найдена.'}</p>
          <button
            type="button"
            onClick={() => void load()}
            className="ds-btn ds-btn--primary"
            style={{ marginTop: 14 }}
          >
            <RefreshCw size={16} />
            Повторить
          </button>
        </div>
      </ClientShell>
    )
  }

  const status = statusView(booking)
  const serviceName = booking.services
    .filter((service) => !service.is_additional)
    .map((service) => service.name)
    .join(', ') || 'Запись'
  const postPaymentActions =
    booking.status === 'prepayment_sent' ||
    booking.status === 'confirmed' ||
    booking.prepayment_confirmed ||
    !booking.prepayment_enabled

  return (
    <ClientShell title="Статус записи" backTo="/">
      <div style={{ paddingTop: 14, paddingBottom: 26 }}>
        <div className="ds-card" style={{ padding: 18, textAlign: 'center' }}>
          <div style={{
            width: 64,
            height: 64,
            margin: '0 auto 12px',
            borderRadius: 20,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: status.tone,
            background: 'var(--s-surface-2)',
          }}>
            {status.icon}
          </div>
          <h1 className="ds-h1">{status.title}</h1>
          <p className="ds-sub" style={{ marginTop: 8, lineHeight: 1.55 }}>{status.text}</p>
          <button
            type="button"
            onClick={() => void load()}
            disabled={refreshing}
            className="ds-btn ds-btn--ghost ds-btn--sm"
            style={{ marginTop: 12 }}
          >
            <RefreshCw size={14} className={refreshing ? 'bk-spin' : undefined} />
            {refreshing ? 'Проверяем…' : 'Проверить статус'}
          </button>
          {['prepayment_sent', 'pending_prepayment', 'pending'].includes(booking.status) && (
            <p className="ds-caption" style={{ marginTop: 8 }}>
              Страница также обновляется автоматически
            </p>
          )}
        </div>

        {error && <p role="alert" className="ds-sub" style={{ marginTop: 10, color: 'var(--s-danger)' }}>{error}</p>}
        <div className="ds-card" style={{ marginTop: 12, padding: 16 }}>
          <h2 className="ds-h2">{booking.business_name}</h2>
          <div style={{ display: 'grid', gap: 9, marginTop: 12 }}>
            <div className="ds-row" style={{ cursor: 'default', padding: 0 }}>
              <span className="ds-sub">Услуга</span>
              <span className="ds-text" style={{ fontWeight: 700, textAlign: 'right' }}>{serviceName}</span>
            </div>
            {booking.employee_name && (
              <div className="ds-row" style={{ cursor: 'default', padding: 0 }}>
                <span className="ds-sub">{booking.employee_label}</span>
                <span className="ds-text" style={{ fontWeight: 700 }}>{booking.employee_name}</span>
              </div>
            )}
            <div className="ds-row" style={{ cursor: 'default', padding: 0 }}>
              <span className="ds-sub">Дата</span>
              <span className="ds-text" style={{ fontWeight: 700 }}>{formatDate(booking.date)}</span>
            </div>
            <div className="ds-row" style={{ cursor: 'default', padding: 0 }}>
              <span className="ds-sub">Время</span>
              <span className="ds-text" style={{ fontWeight: 700 }}>{booking.time}</span>
            </div>
            {booking.total_price > 0 && (
              <div className="ds-row" style={{ cursor: 'default', padding: 0 }}>
                <span className="ds-sub">Стоимость</span>
                <span className="ds-text" style={{ fontWeight: 700 }}>{formatPrice(booking.total_price)}</span>
              </div>
            )}
            {booking.price_requires_confirmation && <p className="ds-sub">Стоимость согласует компания. Запрос предоплаты появится после согласования.</p>}
            {booking.prepayment_enabled && booking.prepayment_amount > 0 && (
              <div className="ds-row" style={{ cursor: 'default', padding: 0 }}>
                <span className="ds-sub">Предоплата</span>
                <span className="ds-text" style={{ fontWeight: 700 }}>
                  {booking.prepayment_confirmed
                    ? `Получена · ${formatPrice(booking.paid_amount || booking.prepayment_amount)}`
                    : formatPrice(booking.prepayment_amount)}
                </span>
              </div>
            )}
          </div>
        </div>

        {(booking.business_address || booking.business_phone || booking.business_directions) && (
          <div className="ds-card" style={{ marginTop: 12, padding: 16 }}>
            {booking.business_address && (
              <a
                href={`https://yandex.ru/maps/?text=${encodeURIComponent(booking.business_address)}`}
                target="_blank"
                rel="noopener noreferrer"
                className="ds-text"
                style={{ display: 'flex', alignItems: 'center', gap: 8, textDecoration: 'none' }}
              >
                <MapPin size={16} className="ds-accent" />
                {booking.business_address}
              </a>
            )}
            {booking.business_phone && (
              <a
                href={`tel:${booking.business_phone}`}
                className="ds-text"
                style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, textDecoration: 'none' }}
              >
                <Phone size={16} className="ds-accent" />
                {booking.business_phone}
              </a>
            )}
            {booking.business_directions && (
              <p className="ds-sub" style={{ whiteSpace: 'pre-line', lineHeight: 1.55, marginTop: 10 }}>
                {booking.business_directions}
              </p>
            )}
          </div>
        )}

        {booking.status === 'pending_prepayment' && booking.prepayment_enabled && (
          <div className="ds-card" style={{ marginTop: 12, padding: 16 }}>
            <h3 className="ds-h3">Реквизиты для предоплаты</h3>
            {booking.prepayment_expires_at && <p className="ds-sub" style={{ marginTop: 10 }}>
              Отправьте предоплату и сообщите о переводе до {new Date(booking.prepayment_expires_at + (booking.prepayment_expires_at.endsWith('Z') ? '' : 'Z')).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}.
              После истечения срока неоплаченная бронь будет отменена.
            </p>}
            {booking.payment_phone && (
              <p className="ds-text" style={{ marginTop: 10 }}>
                Телефон: <strong>{booking.payment_phone}</strong>
              </p>
            )}
            {booking.payment_details && (
              <p className="ds-sub" style={{ marginTop: 8, whiteSpace: 'pre-line' }}>{booking.payment_details}</p>
            )}
            <label className="ds-sub" style={{ display: 'block', marginTop: 12 }}>
              {booking.require_receipt_for_prepayment ? 'Приложите чек (обязательно)' : 'Чек об оплате (по желанию)'}
              <input type="file" accept="image/jpeg,image/png,image/webp,application/pdf" disabled={paymentBusy} onChange={event => setReceipt(event.target.files?.[0] || null)} />
            </label>
            <button
              type="button"
              className="ds-btn ds-btn--primary ds-btn--block"
              style={{ marginTop: 14 }}
              disabled={paymentBusy || (booking.require_receipt_for_prepayment && !receipt)}
              onClick={async () => {
                if (!token || paymentBusy) return
                setPaymentBusy(true); setError(null)
                try {
                  if (receipt) await api.uploadReceiptByToken(token, receipt)
                  else await api.prepaymentSentByToken(token)
                  await load(true)
                } catch {
                  await load(true)
                  setError('Не удалось сообщить об оплате. Проверьте статус: срок брони мог истечь. Если деньги уже отправлены, свяжитесь с компанией.')
                }
                finally { setPaymentBusy(false) }
              }}
            >
              {paymentBusy ? 'Отправляем…' : receipt ? 'Отправить чек и сообщить об оплате' : 'Оплата отправлена ✓'}
            </button>
          </div>
        )}

        {postPaymentActions && (
          <div style={{ marginTop: 16 }}>
            <div className="ds-caption" style={{ textTransform: 'uppercase', marginBottom: 8 }}>
              После оплаты
            </div>

            <NotificationSetup notifyToken={booking.notify_token} />

            <button
              type="button"
              onClick={() => downloadBookingIcs({
                businessName: booking.business_name,
                serviceName,
                date: booking.date,
                time: booking.time,
                durationMinutes: booking.duration_minutes || 60,
                address: booking.business_address,
                description: booking.employee_name
                  ? `${serviceName} · ${booking.employee_name}`
                  : serviceName,
              })}
              className="ds-btn ds-btn--ghost ds-btn--block"
              style={{ marginTop: 12 }}
            >
              <Calendar size={17} />
              Добавить в календарь
            </button>

            <a
              href={`https://t.me/Sochiservice_bot?start=notify_${booking.notify_token}`}
              target="_blank"
              rel="noopener noreferrer"
              className="ds-btn ds-btn--ghost ds-btn--block"
              style={{ marginTop: 10, textDecoration: 'none' }}
            >
              <Bell size={17} />
              Уведомления в Telegram
            </a>
          </div>
        )}

        <div style={{ marginTop: 18, textAlign: 'center' }}>
          <Link to="/my" className="ds-accent" style={{ fontSize: 13 }}>
            Открыть мои записи
          </Link>
        </div>
      </div>
    </ClientShell>
  )
}
