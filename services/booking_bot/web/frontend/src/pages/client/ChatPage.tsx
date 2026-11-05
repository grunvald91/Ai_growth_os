import { Fragment, useState, useRef, useEffect, useCallback } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Sparkles, Send, Check, Loader2, RotateCw, ChevronRight, ArrowRight, X } from 'lucide-react'
import { api, type Business, type SalesPendingAction, type SalesConfirmation, type SalesSlotOffer } from '../../api/client'
import { AirFrame } from './AirFrame'

interface ChatMessage {
  id: string
  role: 'user' | 'ai'
  text: string
  booking_url?: string | null
  status?: 'sending' | 'failed'
}

function errorText(error: unknown) {
  if (error instanceof Error) {
    try {
      const detail = JSON.parse(error.message).detail
      if (typeof detail === 'string') return detail
    } catch { /* Non-JSON network failure. */ }
  }
  return 'Не удалось связаться с сервисом. Попробуйте ещё раз.'
}

const money = (n: number) => `${n.toLocaleString('ru-RU')} ₽`

function dayLabel(iso: string) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'long' })
}

function durationLabel(minutes?: number | null) {
  if (!minutes) return ''
  const h = Math.floor(minutes / 60), m = minutes % 60
  return [h ? `${h} ч` : '', m ? `${m} мин` : ''].filter(Boolean).join(' ')
}

// Render a deliberately small, safe Markdown subset. HTML stays literal text.
function RichMessage({ text }: { text: string }) {
  return <>{text.split('\n').map((line, i) => {
    const bullet = /^\s*[-*] /.test(line)
    const content = bullet ? line.replace(/^\s*[-*] /, '') : line
    return <Fragment key={i}>
      {i > 0 && '\n'}
      {bullet && '• '}
      {content.split(/(\*\*[^*\n]+\*\*|\*[^*\n]+\*)/g).map((part, j) =>
        part.startsWith('**') && part.endsWith('**')
          ? <strong key={j}>{part.slice(2, -2)}</strong>
          : part.startsWith('*') && part.endsWith('*') && part.length > 2
            ? <em key={j}>{part.slice(1, -1)}</em>
            : <Fragment key={j}>{part}</Fragment>)}
    </Fragment>
  })}</>
}

function useExpired(expiresAt: string) {
  const [expired, setExpired] = useState(() => new Date(`${expiresAt}Z`).getTime() <= Date.now())
  useEffect(() => {
    const timer = window.setTimeout(() => setExpired(true),
      Math.max(0, new Date(`${expiresAt}Z`).getTime() - Date.now()))
    return () => window.clearTimeout(timer)
  }, [expiresAt])
  return expired
}

const VISIBLE_TIMES = 12

// Компактный выбор времени: фильтр по специалисту/залу, сетка времени, одна кнопка.
function SlotPicker({ offer, busy, onChoose }: {
  offer: SalesSlotOffer
  busy: boolean
  onChoose: (employeeId: number, time: string) => void
}) {
  const expired = useExpired(offer.expires_at)
  const resources = Array.from(new Map(offer.slots.map(s => [s.employee_id, s.employee_name])).entries())
  const [resource, setResource] = useState<number | null>(resources.length === 1 ? resources[0][0] : null)
  const [time, setTime] = useState('')
  const [expanded, setExpanded] = useState(false)
  const pool = offer.slots.filter(s => resource == null || s.employee_id === resource)
  // Одно время — одна кнопка; при «Все» берём первый свободный ресурс на это время.
  const byTime = new Map<string, { employee_id: number; employee_name: string }>()
  for (const s of pool) if (!byTime.has(s.time)) byTime.set(s.time, s)
  const times = Array.from(byTime.keys())
  const shown = expanded ? times : times.slice(0, VISIBLE_TIMES)
  const choice = time ? byTime.get(time) : undefined
  const meta = [offer.service_name, durationLabel(offer.duration_minutes), offer.solo ? 'индивидуально' : ''].filter(Boolean).join(' · ')

  return (
    <div className="bf-chat-card" aria-label="Свободное время">
      <div className="bf-chat-card-head">
        <strong>Свободно · {dayLabel(offer.date)}</strong>
        {meta && <span>{meta}</span>}
      </div>
      {resources.length > 1 && (
        <div className="bf-chat-chips" role="group" aria-label="Кто или где">
          <button type="button" className="bf-chip" aria-pressed={resource == null} onClick={() => { setResource(null); setTime('') }}>Все</button>
          {resources.map(([id, name]) => (
            <button key={id} type="button" className="bf-chip" aria-pressed={resource === id} onClick={() => { setResource(id); setTime('') }}>{name}</button>
          ))}
        </div>
      )}
      <div className="bf-time-grid" role="group" aria-label="Время">
        {shown.map(t => (
          <button key={t} type="button" className={`bf-time${time === t ? ' selected' : ''}`} aria-pressed={time === t}
            disabled={busy || expired} onClick={() => setTime(t)}>{t}</button>
        ))}
      </div>
      {!expanded && times.length > VISIBLE_TIMES && (
        <button type="button" className="bf-about" onClick={() => setExpanded(true)}>Ещё {times.length - VISIBLE_TIMES}</button>
      )}
      {expired && <p className="bf-chat-note" role="status">Варианты устарели — попросите проверить время ещё раз.</p>}
      <button type="button" className="bf-primary bf-chat-cta" disabled={!choice || busy || expired}
        onClick={() => { if (choice) onChoose(choice.employee_id, time) }}>
        {busy ? <><Loader2 className="animate-spin" /> Проверяю время…</>
          : choice ? <>Выбрать {time}{resources.length > 1 ? ` · ${choice.employee_name}` : ''} <ArrowRight /></>
            : 'Выберите время'}
      </button>
    </div>
  )
}

function ConfirmationCard({ action, busy, onConfirm, onReject }: {
  action: SalesPendingAction
  busy: boolean
  onConfirm: (details: SalesConfirmation) => void
  onReject: () => void
}) {
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [consent, setConsent] = useState(false)
  const [terms, setTerms] = useState(false)
  const expired = useExpired(action.expires_at)
  const digits = phone.replace(/\D/g, '')
  const validContact = !action.requires_contact || (name.trim().length >= 2 && /^\+?[0-9\s()-]+$/.test(phone) && digits.length >= 10 && digits.length <= 15)
  const canConfirm = !busy && !expired && validContact && (!action.requires_legal_consent || (consent && terms))
  const s = action.summary
  return (
    <div className="bf-chat-card" aria-label="Подтверждение записи">
      <div className="bf-chat-card-head"><strong>Проверьте запись</strong>{s && <span>{s.business_name}</span>}</div>
      {s ? (
        <div className="bf-receipt">
          <div><span>Услуга</span><strong className="wrap">{s.service_name}{s.solo ? ' · индивидуально' : ''}</strong></div>
          <div><span>{s.resource_label}</span><strong className="wrap">{s.resource_name}</strong></div>
          <div><span>Когда</span><strong>{dayLabel(s.date)}, {s.time}</strong></div>
          {s.duration_minutes > 0 && <div><span>Длительность</span><strong>{durationLabel(s.duration_minutes)}</strong></div>}
          {s.addons.length > 0 && <div><span>Дополнительно</span><strong className="wrap">{s.addons.join(', ')}</strong></div>}
          <div><span>Стоимость</span><strong>{s.amount != null ? money(s.amount) : 'подтвердит менеджер'}</strong></div>
          {s.prepayment_amount != null && (
            <div><span>Предоплата{s.prepayment_percent && s.prepayment_percent < 100 ? ` ${s.prepayment_percent}%` : ''}</span><strong>{money(s.prepayment_amount)}</strong></div>
          )}
        </div>
      ) : (
        <p className="bf-chat-text">{action.description}</p>
      )}
      {s?.prepayment_amount != null && (
        <p className="bf-chat-note">Предоплата переводится напрямую бизнесу; запись подтверждается после проверки владельцем.{s.receipt_required ? ' Понадобится чек.' : ''}</p>
      )}
      {action.cancellation_policy && <p className="bf-chat-note">{action.cancellation_policy}</p>}
      {action.requires_contact && (
        <div className="bf-chat-fields">
          <label><span className="bf-field-label">Ваше имя</span>
            <input className="bf-input" autoComplete="name" maxLength={120} value={name} onChange={e => setName(e.target.value)} />
          </label>
          <label><span className="bf-field-label">Телефон</span>
            <input className="bf-input" type="tel" inputMode="tel" autoComplete="tel" maxLength={30} value={phone} onChange={e => setPhone(e.target.value)} />
          </label>
        </div>
      )}
      {action.requires_legal_consent && (
        <div>
          <label className="bf-legal">
            <input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />
            <span>Даю <Link to={`/legal/consent/${action.business_id}`} target="_blank">согласие на обработку данных</Link></span>
          </label>
          <label className="bf-legal">
            <input type="checkbox" checked={terms} onChange={e => setTerms(e.target.checked)} />
            <span>Принимаю <Link to={`/legal/terms/${action.business_id}`} target="_blank">условия записи</Link></span>
          </label>
        </div>
      )}
      {expired && <p className="bf-chat-note" role="status">Подтверждение истекло — попросите проверить время снова.</p>}
      <div className="bf-chat-actions">
        <button type="button" className="bf-back" disabled={busy} onClick={onReject}>Отмена</button>
        <button type="button" className="bf-primary" disabled={!canConfirm} onClick={() => onConfirm({
          client_name: action.requires_contact ? name.trim() : undefined,
          client_phone: action.requires_contact ? phone.trim() : undefined,
          personal_data_consent: consent, booking_terms_accepted: terms,
        })}>{busy ? <Loader2 className="animate-spin" /> : <Check />} Подтвердить</button>
      </div>
    </div>
  )
}

function welcome(business: Pick<Business, 'id' | 'name'>): ChatMessage {
  return { id: 'welcome', role: 'ai', text: `Здравствуйте! Помогу подобрать услугу в «${business.name}» и найти удобное время. Что вас интересует?` }
}

export function SalesThread({ business, embedded = false, onClose }: {
  business: Pick<Business, 'id' | 'name'>
  /** Окошко чата на странице записи: без ссылки «Обычная запись», есть кнопка закрытия. */
  embedded?: boolean
  onClose?: () => void
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [pending, setPending] = useState<SalesPendingAction | null>(null)
  const [offer, setOffer] = useState<SalesSlotOffer | null>(null)
  const [mode, setMode] = useState<'ai' | 'human'>('ai')
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const operation = useRef(false)

  const applyHistory = useCallback((history: Awaited<ReturnType<typeof api.aiHistory>>) => {
    const restored: ChatMessage[] = history.messages.length ? history.messages : [welcome(business)]
    if (history.booking_url && restored.length) restored[restored.length - 1].booking_url = history.booking_url
    setMessages(restored)
    setPending(history.pending_action || null)
    setOffer(history.available_slots || null)
    setMode(history.mode)
  }, [business])

  const loadHistory = useCallback(async () => applyHistory(await api.aiHistory(business.id)), [applyHistory, business.id])

  useEffect(() => {
    let active = true
    api.aiHistory(business.id).then(history => {
      if (!active) return
      applyHistory(history)
      setReady(true)
    }).catch(err => { if (active) setError(errorText(err)) })
    return () => { active = false }
  }, [business.id, applyHistory])

  useEffect(() => {
    if (mode !== 'human') return
    const timer = window.setInterval(() => {
      if (!operation.current) loadHistory().catch(err => setError(errorText(err)))
    }, 8000)
    return () => window.clearInterval(timer)
  }, [mode, loadHistory])

  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [messages, pending, offer, busy])

  const run = async (task: () => Promise<void>) => {
    if (operation.current || !ready) return
    operation.current = true
    setBusy(true)
    setError('')
    try { await task() } catch (err) { setError(errorText(err)) } finally { operation.current = false; setBusy(false) }
  }

  const send = async (text = input.trim(), retryId?: string) => {
    if (!text || operation.current || !ready) return
    operation.current = true
    const id = retryId || crypto.randomUUID()
    setBusy(true)
    setError('')
    if (retryId) {
      setMessages(previous => previous.map(item => item.id === id ? { ...item, status: 'sending' } : item))
    } else {
      setInput('')
      setMessages(previous => [...previous, { id, role: 'user', text, status: 'sending' } as ChatMessage])
    }
    try {
      const result = await api.aiChat(text, business.id)
      setMessages(previous => [...previous.map(item => item.id === id ? { ...item, status: undefined } : item),
        { id: crypto.randomUUID(), role: 'ai', text: result.reply, booking_url: result.booking_url } as ChatMessage])
      setPending(result.pending_action || null)
      setOffer(result.available_slots || null)
      setMode(result.mode)
    } catch (err) {
      setMessages(previous => previous.map(item => item.id === id ? { ...item, status: 'failed' } : item))
      setError(errorText(err))
    } finally { operation.current = false; setBusy(false) }
  }

  const selectSlot = (employeeId: number, time: string) => run(async () => {
    await api.aiSelectSlot(business.id, employeeId, time)
    await loadHistory()
  })

  const handleAction = (confirm: boolean, details: SalesConfirmation = {}) => {
    if (!pending) return
    return run(async () => {
      if (confirm) await api.aiConfirm(pending.action_id, details)
      else await api.aiReject(pending.action_id)
      await loadHistory()
    })
  }

  const requestOperator = () => run(async () => { await api.aiHandoff(business.id); await loadHistory() })

  return (
    <section className="bf-screen" aria-label={`Чат с ${business.name}`}>
      <div className="bf-chat-head">
        <span className="bf-chat-avatar" aria-hidden="true"><Sparkles /></span>
        <span className="bf-biz-body">
          <span className="bf-option-title">{business.name}</span>
          <span className="bf-biz-line">{mode === 'human' ? 'Диалог у оператора — ответ появится здесь' : 'Помощник по записи'}</span>
        </span>
        {onClose && <button type="button" className="bf-chat-close" onClick={onClose} aria-label="Закрыть чат"><X /></button>}
      </div>
      <div className="bf-inner bf-chat-log" aria-live="polite">
        {messages.map(message => (
          <div key={message.id} className={`bf-msg ${message.role}`}>
            <div className="bf-msg-bubble">
              {message.role === 'ai' ? <RichMessage text={message.text} /> : message.text}
              {message.status === 'sending' && <span className="bf-msg-state" role="status">Отправляется…</span>}
              {message.status === 'failed' && (
                <span className="bf-msg-state is-failed">
                  Не отправлено
                  <button type="button" aria-label="Отправить сообщение повторно" disabled={busy}
                    onClick={() => void send(message.text, message.id)}><RotateCw /></button>
                </span>
              )}
              {message.booking_url && <Link className="bf-msg-link" to={message.booking_url}>Открыть запись <ChevronRight /></Link>}
            </div>
          </div>
        ))}
        {offer && mode === 'ai' && !pending && <SlotPicker key={offer.expires_at + offer.service_id} offer={offer}
          busy={busy} onChoose={(employeeId, time) => void selectSlot(employeeId, time)} />}
        {pending && <ConfirmationCard key={pending.action_id} action={pending} busy={busy}
          onConfirm={details => void handleAction(true, details)} onReject={() => void handleAction(false)} />}
        {busy && <div className="bf-msg ai"><div className="bf-msg-bubble bf-typing" role="status" aria-label="Обрабатываю"><span /><span /><span /></div></div>}
        <div ref={messagesEndRef} />
      </div>
      <div className="bf-chat-bar">
        {error && <p className="bf-error" role="alert">{error}</p>}
        <div className="bf-chat-links">
          {embedded ? <span /> : <Link to={`/book/${business.id}`}>Обычная запись</Link>}
          {mode === 'ai' && <button type="button" disabled={busy || !ready} onClick={() => void requestOperator()}>Позвать оператора</button>}
        </div>
        <form className="bf-chat-input" onSubmit={e => { e.preventDefault(); void send() }}>
          <input aria-label="Сообщение" value={input} maxLength={4000} onChange={e => setInput(e.target.value)}
            placeholder={mode === 'human' ? 'Сообщение оператору…' : 'Напишите сообщение…'} disabled={busy || !ready} />
          <button type="submit" aria-label="Отправить" disabled={!input.trim() || busy || !ready}><Send /></button>
        </form>
      </div>
    </section>
  )
}

export function ChatPage() {
  const [params, setParams] = useSearchParams()
  const [businesses, setBusinesses] = useState<Business[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const selectedId = Number(params.get('business_id'))
  const business = businesses.find(b => b.id === selectedId)
  useEffect(() => {
    api.getBusinesses().then(setBusinesses).catch(err => setError(errorText(err))).finally(() => setLoading(false))
  }, [])
  return (
    <AirFrame label="Чат ServGo" backTo={business ? `/business/${business.id}` : '/'} tabs={!business}>
      {business ? <SalesThread key={business.id} business={business} /> : (
        <section className="bf-screen" aria-label="Выбор компании">
          <div className="bf-inner bf-page">
            <h1 className="bf-h2">Чат</h1>
            <p className="bf-intro">Подберёт услугу, подскажет цену и найдёт свободное время. Выберите место:</p>
            {error && <p className="bf-error" role="alert">{error}</p>}
            <div className="bf-kicker">{loading ? 'Загружаем…' : 'Места'}</div>
            <nav className="bf-list" aria-label="Места">
              {businesses.map(b => (
                <button key={b.id} type="button" className="bf-biz bf-biz--button" onClick={() => setParams({ business_id: String(b.id) })}>
                  <span className="bf-thumb" aria-hidden="true">{b.name.replace(/[«»"']/g, '').trim().charAt(0).toUpperCase()}</span>
                  <span className="bf-biz-body">
                    <span className="bf-option-title">{b.name}</span>
                    {b.address && <span className="bf-biz-line">{b.address}</span>}
                  </span>
                  <ChevronRight className="bf-chevron" />
                </button>
              ))}
            </nav>
          </div>
        </section>
      )}
    </AirFrame>
  )
}
