import { useState, useEffect, useCallback } from 'react'
import {
  Ticket,
  Clock,
  CheckCircle,
  XCircle,
  AlertCircle,
  CreditCard,
  Package,
  ChevronRight,
  X,
  LogIn,
} from 'lucide-react'
import { hapticSelection, hapticSuccess } from '../../utils/telegram'
import { api } from '../../api/client'
import type { SubscriptionPlan, ClientSubscription, BusinessDetail } from '../../api/client'
import { ClientShell } from '../../components/ClientShell'

// ── Status config ──

const statusMap: Record<
  string,
  { label: string; icon: typeof CheckCircle; badgeClass: string }
> = {
  active: {
    label: 'Активен',
    icon: CheckCircle,
    badgeClass: 'ds-badge--completed',
  },
  expired: {
    label: 'Истёк',
    icon: XCircle,
    badgeClass: 'ds-badge--cancelled',
  },
  waiting_confirmation: {
    label: 'Ожидает подтверждения',
    icon: AlertCircle,
    badgeClass: 'ds-badge--pending',
  },
  pending_payment: {
    label: 'Ожидает оплаты',
    icon: AlertCircle,
    badgeClass: 'ds-badge--pending',
  },
}

const defaultStatus = {
  label: 'Неизвестно',
  icon: AlertCircle,
  badgeClass: 'ds-badge--cancelled',
}

// ── Helpers ──

function formatPrice(price: number) {
  return new Intl.NumberFormat('ru-RU').format(price)
}

function formatDays(days: number) {
  if (days % 10 === 1 && days % 100 !== 11) return `${days} день`
  if ([2, 3, 4].includes(days % 10) && ![12, 13, 14].includes(days % 100)) return `${days} дня`
  return `${days} дней`
}

function formatSessions(count: number) {
  if (count % 10 === 1 && count % 100 !== 11) return `${count} занятие`
  if ([2, 3, 4].includes(count % 10) && ![12, 13, 14].includes(count % 100))
    return `${count} занятия`
  return `${count} занятий`
}

function formatDateRu(dateStr: string) {
  const d = new Date(dateStr)
  const months = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
  ]
  return `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`
}

function daysUntil(dateStr: string) {
  const now = new Date()
  now.setHours(0, 0, 0, 0)
  const d = new Date(dateStr)
  d.setHours(0, 0, 0, 0)
  return Math.ceil((d.getTime() - now.getTime()) / (1000 * 60 * 60 * 24))
}

function getTotalSessions(plan: SubscriptionPlan): number {
  if (plan.services && plan.services.length > 0) {
    return plan.services.reduce((sum, s) => sum + s.quantity, 0)
  }
  return plan.sessions_count ?? 0
}

// ── Purchase Modal ──

function PurchaseModal({
  subscription,
  businessDetail,
  onClose,
  onPurchased,
}: {
  subscription: SubscriptionPlan
  businessDetail: BusinessDetail | null
  onClose: () => void
  onPurchased: () => void
}) {
  const [step, setStep] = useState<'confirm' | 'payment' | 'sent'>('confirm')
  const [buying, setBuying] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [clientSub, setClientSub] = useState<ClientSubscription | null>(null)

  const handleBuy = async () => {
    setBuying(true)
    setError(null)
    try {
      const result = await api.buySubscription(subscription.id)
      setClientSub(result)
      setStep('payment')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Ошибка при покупке')
    } finally {
      setBuying(false)
    }
  }

  const handlePaymentSent = async () => {
    if (!clientSub) return
    setSending(true)
    setError(null)
    try {
      await api.subscriptionPaymentSent(clientSub.id)
      hapticSuccess()
      setStep('sent')
      onPurchased()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Ошибка при отправке уведомления')
    } finally {
      setSending(false)
    }
  }

  const totalSessions = getTotalSessions(subscription)

  return (
    <div className="ds-sheet-ov" onClick={onClose}>
      <div className="ds-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="ds-sheet-grip" />
        <div className="ds-sheet-head">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div
              style={{
                width: 48, height: 48, borderRadius: 14, flexShrink: 0,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: 'var(--s-accent-soft)',
              }}
            >
              <CreditCard size={24} style={{ color: 'var(--s-accent)' }} />
            </div>
            <div>
              <h3>{subscription.name}</h3>
              <p style={{ fontSize: 20, fontWeight: 750, color: 'var(--s-text)', margin: '2px 0 0' }}>
                {formatPrice(subscription.price)} ₽
              </p>
              {totalSessions > 0 && (
                <p className="ds-sub" style={{ marginTop: 2 }}>
                  {totalSessions >= 999 ? 'Безлимит' : formatSessions(totalSessions)} / {formatDays(subscription.validity_days)}
                </p>
              )}
            </div>
          </div>
          <button className="ds-icon-btn" onClick={onClose} aria-label="Закрыть">
            <X size={20} />
          </button>
        </div>

        {error && (
          <div className="ds-error">
            <AlertCircle size={14} />
            {error}
          </div>
        )}

        {step === 'confirm' && (
          <>
            {subscription.services && subscription.services.length > 0 && (
              <div style={{ background: 'var(--s-surface-2)', borderRadius: 'var(--r-lg)', padding: 14, marginBottom: 16 }}>
                <p className="ds-text" style={{ fontWeight: 650, marginBottom: 8 }}>Включено:</p>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {subscription.services.map((s) => (
                    <div key={s.id} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, color: 'var(--s-muted)' }}>
                      <span>{s.name}</span>
                      <span style={{ fontWeight: 650, color: 'var(--s-text)' }}>{s.quantity} шт.</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <button
              onClick={handleBuy}
              disabled={buying}
              className="ds-btn ds-btn--primary ds-btn--block"
            >
              {buying ? 'Оформление...' : 'Купить абонемент'}
            </button>
          </>
        )}

        {step === 'payment' && (
          <>
            <div style={{ background: 'var(--s-surface-2)', borderRadius: 'var(--r-lg)', padding: 14, marginBottom: 16 }}>
              <p className="ds-text" style={{ fontWeight: 650, marginBottom: 8 }}>Реквизиты для оплаты:</p>
              <div className="ds-sub">
                {businessDetail?.payment_phone ? (
                  <>
                    <p style={{ margin: 0 }}>Перевод по номеру телефона:</p>
                    <p style={{ fontWeight: 650, color: 'var(--s-text)', marginTop: 4 }}>{businessDetail.payment_phone}</p>
                  </>
                ) : null}
                {businessDetail?.payment_details ? (
                  <>
                    <p style={{ margin: businessDetail?.payment_phone ? '8px 0 0' : 0 }}>
                      {businessDetail?.payment_phone ? 'Или реквизиты:' : 'Реквизиты для оплаты:'}
                    </p>
                    <p style={{ fontWeight: 650, color: 'var(--s-text)', marginTop: 4 }}>{businessDetail.payment_details}</p>
                  </>
                ) : null}
                {!businessDetail?.payment_phone && !businessDetail?.payment_details && (
                  <p style={{ margin: 0 }}>Свяжитесь с компанией для уточнения реквизитов оплаты.</p>
                )}
              </div>
              <p className="ds-caption" style={{ marginTop: 10 }}>
                В комментарии к переводу укажите ваше имя и название абонемента
              </p>
            </div>

            <button
              onClick={handlePaymentSent}
              disabled={sending}
              className="ds-btn ds-btn--primary ds-btn--block"
            >
              {sending ? 'Отправка...' : 'Оплата отправлена'}
            </button>
          </>
        )}

        {step === 'sent' && (
          <div style={{ textAlign: 'center', padding: '12px 0' }}>
            <CheckCircle size={28} style={{ color: 'var(--s-success)', margin: '0 auto 8px' }} />
            <p className="ds-text" style={{ fontWeight: 650 }}>
              Запрос отправлен! Ожидайте подтверждения.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main Component ──

export function SubscriptionsPage() {
  const [activeTab, setActiveTab] = useState<'available' | 'my'>('available')
  const [subscriptions, setSubscriptions] = useState<SubscriptionPlan[]>([])
  const [mySubscriptions, setMySubscriptions] = useState<ClientSubscription[]>([])
  const [loading, setLoading] = useState(true)
  const [myLoading, setMyLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [myError, setMyError] = useState<string | null>(null)
  const [purchaseModal, setPurchaseModal] = useState<SubscriptionPlan | null>(null)
  const [businessDetail, setBusinessDetail] = useState<BusinessDetail | null>(null)
  const [, setBusinessId] = useState<number | null>(null)

  const isLoggedIn = !!localStorage.getItem('token')

  // Load available subscriptions
  useEffect(() => {
    const load = async () => {
      setLoading(true)
      setError(null)
      try {
        // Get businessId from URL or first business
        const params = new URLSearchParams(window.location.search)
        const bizIdParam = params.get('business_id')
        let bizId: number

        if (bizIdParam) {
          bizId = parseInt(bizIdParam, 10)
        } else {
          const businesses = await api.getBusinesses()
          if (businesses.length === 0) {
            setSubscriptions([])
            setLoading(false)
            return
          }
          bizId = businesses[0].id
        }

        setBusinessId(bizId)

        // Load subscriptions and business detail in parallel
        const [subs, detail] = await Promise.all([
          api.getSubscriptions(bizId),
          api.getBusiness(bizId),
        ])

        setSubscriptions(subs)
        setBusinessDetail(detail)
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Ошибка загрузки абонементов')
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  // Load my subscriptions
  const loadMySubscriptions = useCallback(async () => {
    if (!isLoggedIn) return
    setMyLoading(true)
    setMyError(null)
    try {
      const subs = await api.getMySubscriptions()
      setMySubscriptions(subs)
    } catch (e: unknown) {
      setMyError(e instanceof Error ? e.message : 'Ошибка загрузки ваших абонементов')
    } finally {
      setMyLoading(false)
    }
  }, [isLoggedIn])

  useEffect(() => {
    if (isLoggedIn) {
      loadMySubscriptions()
    }
  }, [isLoggedIn, loadMySubscriptions])

  const handlePurchased = () => {
    // Reload my subscriptions after purchase
    loadMySubscriptions()
  }

  // ── Loading ──
  if (loading) {
    return (
      <ClientShell title="Абонементы">
        <div style={{ marginTop: 4 }}>
          <div className="skeleton" style={{ height: 40, borderRadius: 10, marginBottom: 16 }} />
          {[1, 2, 3].map((i) => (
            <div key={i} className="skeleton" style={{ height: 180, borderRadius: 14, marginBottom: 10 }} />
          ))}
        </div>
      </ClientShell>
    )
  }

  return (
    <ClientShell title="Абонементы">
      {/* Tabs */}
      <div className="ds-seg ds-seg--full" style={{ marginTop: 4, marginBottom: 16 }}>
        <button
          className={activeTab === 'available' ? 'on' : ''}
          onClick={() => {
            hapticSelection()
            setActiveTab('available')
          }}
        >
          Доступные
        </button>
        <button
          className={activeTab === 'my' ? 'on' : ''}
          onClick={() => {
            hapticSelection()
            setActiveTab('my')
          }}
        >
          Мои
          {mySubscriptions.length > 0 && (
            <span
              className="ds-badge"
              style={{ marginLeft: 6, background: 'var(--s-accent)', color: 'var(--s-accent-ink)' }}
            >
              {mySubscriptions.length}
            </span>
          )}
        </button>
      </div>

      {/* Error state */}
      {error && activeTab === 'available' && (
        <div className="ds-error">
          <AlertCircle size={14} />
          {error}
        </div>
      )}

      {/* Available subscriptions tab */}
      {activeTab === 'available' && !error && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {subscriptions.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '64px 0' }}>
              <div
                style={{
                  width: 56, height: 56, borderRadius: 16, margin: '0 auto 14px',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: 'var(--s-surface-2)',
                }}
              >
                <Package size={26} style={{ color: 'var(--s-muted)' }} />
              </div>
              <h2 className="ds-h2" style={{ marginBottom: 6 }}>
                Нет доступных абонементов
              </h2>
              <p className="ds-sub">
                Абонементы пока не добавлены
              </p>
            </div>
          ) : (
            subscriptions.map((sub) => {
              const totalSessions = getTotalSessions(sub)
              return (
                <div key={sub.id} className="ds-card" style={{ padding: 16 }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 10 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <h3 className="ds-h3">{sub.name}</h3>
                      {businessDetail?.name && (
                        <p className="ds-sub" style={{ marginTop: 2 }}>{businessDetail.name}</p>
                      )}
                    </div>
                    <div style={{ textAlign: 'right', flexShrink: 0, marginLeft: 12 }}>
                      <p style={{ fontSize: 17, fontWeight: 750, color: 'var(--s-text)', margin: 0 }}>
                        {formatPrice(sub.price)} ₽
                      </p>
                    </div>
                  </div>

                  {sub.description && (
                    <p className="ds-sub" style={{ marginBottom: 12 }}>{sub.description}</p>
                  )}

                  <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 12 }}>
                    <span className="ds-sub" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <Ticket size={14} />
                      {totalSessions >= 999 ? 'Безлимит' : formatSessions(totalSessions)}
                    </span>
                    <span className="ds-sub" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <Clock size={14} />
                      {formatDays(sub.validity_days)}
                    </span>
                  </div>

                  {sub.services && sub.services.length > 0 && (
                    <div style={{ background: 'var(--s-surface-2)', borderRadius: 'var(--r-md)', padding: 10, marginBottom: 12 }}>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                        {sub.services.map((s) => (
                          <div key={s.id} className="ds-caption" style={{ display: 'flex', justifyContent: 'space-between' }}>
                            <span>{s.name}</span>
                            <span style={{ fontWeight: 650 }}>{s.quantity} шт.</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  <button
                    onClick={() => {
                      hapticSelection()
                      setPurchaseModal(sub)
                    }}
                    className="ds-btn ds-btn--primary ds-btn--block"
                  >
                    Купить
                    <ChevronRight size={16} />
                  </button>
                </div>
              )
            })
          )}
        </div>
      )}

      {/* My subscriptions tab */}
      {activeTab === 'my' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {!isLoggedIn ? (
            <div style={{ textAlign: 'center', padding: '64px 0' }}>
              <div
                style={{
                  width: 56, height: 56, borderRadius: 16, margin: '0 auto 14px',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: 'var(--s-surface-2)',
                }}
              >
                <LogIn size={26} style={{ color: 'var(--s-muted)' }} />
              </div>
              <h2 className="ds-h2" style={{ marginBottom: 6 }}>
                Войдите в аккаунт
              </h2>
              <p className="ds-sub">
                Для просмотра ваших абонементов необходимо авторизоваться
              </p>
            </div>
          ) : myLoading ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {[1, 2].map((i) => (
                <div key={i} className="skeleton" style={{ height: 150, borderRadius: 14 }} />
              ))}
            </div>
          ) : myError ? (
            <div className="ds-error">
              <AlertCircle size={14} />
              {myError}
            </div>
          ) : mySubscriptions.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '64px 0' }}>
              <div
                style={{
                  width: 56, height: 56, borderRadius: 16, margin: '0 auto 14px',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: 'var(--s-surface-2)',
                }}
              >
                <Ticket size={26} style={{ color: 'var(--s-muted)' }} />
              </div>
              <h2 className="ds-h2" style={{ marginBottom: 6 }}>
                У вас нет абонементов
              </h2>
              <p className="ds-sub" style={{ marginBottom: 20 }}>
                Выберите подходящий абонемент на вкладке «Доступные»
              </p>
              <button
                onClick={() => setActiveTab('available')}
                className="ds-btn ds-btn--primary"
              >
                Посмотреть абонементы
              </button>
            </div>
          ) : (
            mySubscriptions.map((sub) => {
              const validUntil = sub.valid_until
              const remaining = validUntil ? daysUntil(validUntil) : null
              const effectiveStatus = (sub.status === 'active' && remaining !== null && remaining < 0) ? 'expired' : sub.status
              const status = statusMap[effectiveStatus] || defaultStatus
              const StatusIcon = status.icon
              const sessionsPercent =
                sub.sessions_total > 0
                  ? Math.round((sub.sessions_remaining / sub.sessions_total) * 100)
                  : 0
              const isExpiringSoon = remaining !== null && remaining <= 7 && remaining >= 0
              const isExpired = sub.status === 'expired' || (remaining !== null && remaining < 0)
              const fillColor = isExpired
                ? 'var(--s-danger)'
                : sessionsPercent <= 25
                  ? 'var(--s-warning)'
                  : 'var(--s-accent)'

              return (
                <div key={sub.id} className="ds-card" style={{ padding: 16 }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 10 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <h3 className="ds-h3">
                        {sub.subscription_name}
                      </h3>
                      {sub.business_name && (
                        <p className="ds-sub" style={{ marginTop: 2 }}>{sub.business_name}</p>
                      )}
                    </div>
                    <span className={`ds-badge ${status.badgeClass}`} style={{ flexShrink: 0, marginLeft: 8 }}>
                      <StatusIcon size={12} />
                      {status.label}
                    </span>
                  </div>

                  {/* Sessions progress */}
                  {sub.sessions_total > 0 && sub.sessions_total < 999 && (
                    <div style={{ marginBottom: 12 }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                        <span className="ds-sub">Осталось посещений</span>
                        <span style={{ fontSize: 13, fontWeight: 650, color: 'var(--s-text)' }}>
                          {sub.sessions_remaining} из {sub.sessions_total}
                        </span>
                      </div>
                      <div style={{ height: 8, background: 'var(--s-surface-2)', borderRadius: 'var(--r-pill)', overflow: 'hidden' }}>
                        <div
                          style={{
                            height: '100%',
                            width: `${sessionsPercent}%`,
                            background: fillColor,
                            borderRadius: 'var(--r-pill)',
                            transition: 'width 0.5s ease',
                          }}
                        />
                      </div>
                    </div>
                  )}

                  {/* Expiry info */}
                  {validUntil && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                      <Clock size={14} style={{ color: 'var(--s-muted)' }} />
                      {isExpired ? (
                        <span style={{ color: 'var(--s-danger)' }}>
                          Истёк {formatDateRu(validUntil)}
                        </span>
                      ) : isExpiringSoon ? (
                        <span style={{ color: 'var(--s-warning)', fontWeight: 650 }}>
                          Осталось {formatDays(remaining!)}
                        </span>
                      ) : (
                        <span className="ds-sub">
                          Действует до {formatDateRu(validUntil)}
                        </span>
                      )}
                    </div>
                  )}

                  {/* Payment status */}
                  {sub.payment_status && sub.payment_status !== 'confirmed' && (
                    <div className="ds-caption" style={{ marginTop: 8 }}>
                      {sub.payment_status === 'pending' && 'Ожидается оплата'}
                      {sub.payment_status === 'sent' && 'Оплата отправлена, ожидает подтверждения'}
                    </div>
                  )}
                </div>
              )
            })
          )}
        </div>
      )}

      {/* Purchase modal */}
      {purchaseModal && (
        <PurchaseModal
          subscription={purchaseModal}
          businessDetail={businessDetail}
          onClose={() => setPurchaseModal(null)}
          onPurchased={handlePurchased}
        />
      )}
    </ClientShell>
  )
}
