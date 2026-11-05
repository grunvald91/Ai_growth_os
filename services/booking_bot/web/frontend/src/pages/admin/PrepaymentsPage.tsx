import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw, X } from 'lucide-react'
import { api, type AdminBooking } from '../../api/client'
import { errorMessage } from '../../authUtils'
import { AdminShell } from '../../components/AdminShell'
import { BusinessSelector } from '../../components/BusinessSelector'
import { useAdminBusiness } from '../../hooks/useAdminBusiness'

type Details = Awaited<ReturnType<typeof api.getPaymentDetails>>
const labels: Record<string, string> = { prepayment_sent: 'Оплата отправлена', pending_prepayment: 'Ждёт оплату', confirmed: 'Подтверждено', cancelled: 'Отменено', completed: 'Завершено' }

export function PrepaymentsPage() {
  const { businesses, selectedId, selectBusiness } = useAdminBusiness()
  const [filter, setFilter] = useState('prepayment_sent')
  const [items, setItems] = useState<AdminBooking[]>([])
  const [cursor, setCursor] = useState<number | null>(null)
  const [details, setDetails] = useState<Details | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<number[]>([])
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null)
  const [bulkSummary, setBulkSummary] = useState('')
  const [proof, setProof] = useState<{ url: string; mime: string } | null>(null)
  const activeKey = useRef('')
  const extraPages = useRef(false)
  const load = useCallback(async () => {
    if (!selectedId) { setLoading(false); return }
    try {
      const result = await api.getPrepayments(selectedId, filter)
      if (activeKey.current !== `${selectedId}/${filter}`) return
      setItems(result.items); setCursor(result.next_before_id)
    } catch (err) { setError(errorMessage(err)) } finally { setLoading(false) }
  }, [selectedId, filter])
  useEffect(() => {
    activeKey.current = `${selectedId}/${filter}`
    extraPages.current = false
    setDetails(null); setError(''); setBulkSummary(''); setSelected([]); setLoading(true)
    void load()
    const timer = setInterval(() => { if (!extraPages.current) void load() }, 15000)
    return () => clearInterval(timer)
  }, [load, selectedId, filter])
  useEffect(() => () => { if (proof) URL.revokeObjectURL(proof.url) }, [proof])

  function toggle(id: number) {
    setSelected(current => current.includes(id) ? current.filter(item => item !== id) : [...current, id])
  }

  async function selectAllPending() {
    if (!selectedId || busy || filter !== 'prepayment_sent') return
    setBusy(true); setError(''); setBulkSummary('')
    const key = activeKey.current
    try {
      // Select the entire paginated queue, not only the first 50 visible rows.
      // Bound the action so a huge backlog cannot exhaust the browser.
      let before: number | undefined
      const all: AdminBooking[] = []
      const cap = 500
      let remaining = true
      while (remaining && all.length < cap) {
        const page = await api.getPrepayments(selectedId, 'prepayment_sent', before)
        if (key !== activeKey.current) return
        all.push(...page.items)
        remaining = page.next_before_id !== null
        before = page.next_before_id || undefined
      }
      const unique = [...new Map(all.map(item => [item.id, item])).values()]
      extraPages.current = true
      setItems(unique)
      setCursor(remaining ? before || null : null)
      setSelected(unique.filter(item => item.status === 'prepayment_sent').map(item => item.id))
      if (remaining) setBulkSummary('Выбраны первые 500 заявок. Оставшиеся можно обработать следующим действием.')
    } catch (err) { if (key === activeKey.current) setError(errorMessage(err)) }
    finally { setBusy(false) }
  }

  async function reviewSelected(action: 'confirm' | 'reject') {
    if (busy || filter !== 'prepayment_sent') return
    const candidates = items.filter(item => selected.includes(item.id) && item.status === 'prepayment_sent')
    if (!candidates.length) return
    const message = action === 'confirm'
      ? `Подтвердить фактическое поступление денег по ${candidates.length} заявкам? Убедитесь, что все переводы действительно получены. Одной отметки клиента недостаточно.`
      : `Не подтверждать поступление денег по ${candidates.length} заявкам? Клиентам снова откроется 10-минутный срок для исправления.`
    if (!window.confirm(message)) return
    const key = activeKey.current
    setBusy(true); setError(''); setBulkSummary('')
    setBulkProgress({ done: 0, total: candidates.length })
    let success = 0
    let alreadyUpdated = 0
    let stopped = false
    const failed: number[] = []
    try {
      // Existing endpoint enforces ownership, version and per-visit state rules.
      // Sequential requests avoid competing row locks on multi-service visits.
      for (const booking of candidates) {
        if (key !== activeKey.current) { stopped = true; break }
        try {
          await api.reviewPrepayment(booking.id, action, booking.version)
          success += 1
          setSelected(current => current.filter(id => id !== booking.id))
        } catch (err) {
          // Another item in the same visit may have been updated already.
          // Re-read its actual status rather than counting a 409 as a failed payment.
          let resolved = false
          try {
            const current = await api.getPaymentDetails(booking.id)
            resolved = current.booking.status === (action === 'confirm' ? 'confirmed' : 'pending_prepayment')
          } catch { /* Keep the item as a failure. */ }
          if (resolved) alreadyUpdated += 1
          else failed.push(booking.id)
          if (err instanceof Error && 'status' in err &&
            ((err as Error & { status?: number }).status === 401 ||
             (err as Error & { status?: number }).status === 403)) {
            stopped = true
            break
          }
        }
        setBulkProgress({ done: success + alreadyUpdated + failed.length, total: candidates.length })
      }
      setBulkSummary(`Подтверждено выполненных действий: ${success}; уже переведены в нужный статус: ${alreadyUpdated}; ошибок: ${failed.length}.
        ${stopped ? 'Обработка остановлена до конца списка.' : ''}${failed.length
          ? ' Проверьте оставшиеся заявки и обновите список.' : ''}`)
      if (key === activeKey.current) {
        setDetails(null)
        extraPages.current = false
        setSelected([])
        await load()
      }
    } finally {
      setBusy(false)
      setBulkProgress(null)
    }
  }

  async function open(id: number) {
    setBusy(true); setError(''); setProof(null)
    const key = activeKey.current
    try { const result = await api.getPaymentDetails(id); if (key === activeKey.current) setDetails(result) } catch (err) { setError(errorMessage(err)) } finally { setBusy(false) }
  }
  async function more() {
    if (!selectedId || !cursor) return
    setBusy(true)
    try {
      const result = await api.getPrepayments(selectedId, filter, cursor)
      if (activeKey.current !== `${selectedId}/${filter}`) return
      extraPages.current = true
      setItems(current => [...current, ...result.items.filter(item => !current.some(old => old.id === item.id))]); setCursor(result.next_before_id)
    } catch (err) { setError(errorMessage(err)) } finally { setBusy(false) }
  }
  async function decide(action: 'confirm' | 'reject' | 'cancel') {
    if (!details) return
    const prompts = { confirm: 'Вы проверили поступление денег? Подтвердить оплату и бронь?', reject: 'Отклонить сообщение об оплате? Клиенту будет дано ещё 10 минут для исправления.', cancel: 'Отменить бронь и освободить слот? Деньги автоматически не возвращаются.' }
    if (!window.confirm(prompts[action])) return
    setBusy(true); setError('')
    try {
      const booking = action === 'cancel' ? await api.updateBookingStatus(details.booking.id, 'cancelled') :
        await api.reviewPrepayment(details.booking.id, action, details.booking.version)
      setDetails(current => current ? { ...current, booking } : null)
      await load()
      setDetails(await api.getPaymentDetails(booking.id))
    } catch (err) {
      setError(errorMessage(err))
      try { setDetails(await api.getPaymentDetails(details.booking.id)) } catch { /* Keep the error visible. */ }
    } finally { setBusy(false) }
  }
  async function viewProof(id: number) {
    setBusy(true); setError('')
    try { const blob = await api.downloadReceipt(id); setProof({ url: URL.createObjectURL(blob), mime: blob.type }) }
    catch (err) { setError(errorMessage(err)) } finally { setBusy(false) }
  }

  const filterNames: Record<string, string> = { prepayment_sent: 'на проверке', pending_prepayment: 'ждут отправки', all: 'всего предоплат' }
  const closeDetails = () => { setDetails(null); setProof(null) }

  return <AdminShell title="Предоплаты" headerExtra={<BusinessSelector businesses={businesses} selectedId={selectedId} onSelect={selectBusiness} />}>
    {/* Информация — сверху */}
    <div className="ds-context">
      <div className="ds-context-eyebrow">Предоплаты</div>
      <div className="ds-context-headline">Поступление денег проверяет владелец</div>
      <div className="ds-context-strip">
        <div><strong>{loading ? '…' : `${items.length}${cursor ? '+' : ''}`}</strong><span>{filterNames[filter]}</span></div>
      </div>
      <div className="ds-context-help">Перед подтверждением проверьте реальные поступления в банке.
        Для нескольких услуг одного визита достаточно одного подтверждения — сервер обновит весь визит.</div>
    </div>

    {error && <p role="alert" className="ds-error">{error}</p>}
    {loading && <p role="status" className="ds-sub">Загрузка…</p>}
    {!loading && !items.length && <p className="ds-sub" style={{ textAlign: 'center', padding: '16px 0' }}>Заявок в этом списке нет.</p>}
    <div className="ds-list-scroll" style={{ display: 'grid', gap: 10, alignContent: 'start' }}>
      {items.map(item => <div key={item.id} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        {filter === 'prepayment_sent' && item.status === 'prepayment_sent' && (
          <input type="checkbox" aria-label={`Выбрать заявку №${item.id}`} checked={selected.includes(item.id)}
            disabled={busy} onChange={() => toggle(item.id)}
            style={{ width: 20, height: 20, flexShrink: 0, accentColor: 'var(--s-accent)' }} />
        )}
        <button className="ds-card" disabled={busy} style={{ padding: 14, textAlign: 'left', flex: 1 }}
          onClick={() => void open(item.id)}>
          <strong style={{ fontSize: 14, fontWeight: 550 }}>{item.client_name} · {item.prepayment_amount == null ? 'Сумма не указана' : `${item.prepayment_amount} ₽`}</strong>
          <p className="ds-sub" style={{ margin: '4px 0 2px' }}>{item.service_name} · {item.date.split('-').reverse().join('.')} в {item.time}</p>
          <span className="ds-caption">{labels[item.status] || item.status}</span>
        </button>
      </div>)}
      {cursor && <button className="ds-btn ds-btn--ghost" disabled={busy} onClick={() => void more()}>Загрузить ещё</button>}
    </div>

    {/* Управление — внизу, у большого пальца */}
    <div className="ds-bottom-controls" style={{ marginTop: 'auto' }}>
      {bulkProgress && <p role="status" className="ds-sub">Обработка: {bulkProgress.done} из {bulkProgress.total}…</p>}
      {bulkSummary && <p role="status" className="ds-sub" style={{ margin: '0 0 6px' }}>{bulkSummary}</p>}
      {filter === 'prepayment_sent' && selected.length > 0 && <div className="ds-sheet-actions" style={{ margin: '0 0 8px' }}>
        <button type="button" className="ds-btn ds-btn--primary" disabled={busy}
          onClick={() => void reviewSelected('confirm')}>Подтвердить ({selected.length})</button>
        <button type="button" className="ds-btn ds-btn--ghost" disabled={busy}
          onClick={() => void reviewSelected('reject')}>Не подтверждаю</button>
      </div>}
      {filter === 'prepayment_sent' && <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
        {selected.length > 0
          ? <button type="button" className="ds-btn ds-btn--ghost ds-btn--sm" disabled={busy} onClick={() => setSelected([])}>Снять выделение · {selected.length}</button>
          : <button type="button" className="ds-btn ds-btn--ghost ds-btn--sm" disabled={busy || loading || !items.length}
              onClick={() => void selectAllPending()}>Выбрать все заявки</button>}
      </div>}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <select className="ds-input" aria-label="Показать" style={{ flex: 1, minWidth: 0 }} value={filter} disabled={busy} onChange={e => setFilter(e.target.value)}>
          <option value="prepayment_sent">На проверке</option><option value="pending_prepayment">Ожидают отправки</option><option value="all">Все предоплаты</option>
        </select>
        <button type="button" className="ds-icon-btn bordered" aria-label="Обновить список" title="Обновить список" disabled={busy}
          onClick={() => { extraPages.current = false; void load() }}><RefreshCw size={18} /></button>
      </div>
    </div>

    {/* Детали и действия — шторкой снизу */}
    {details && <div className="ds-sheet-ov" onClick={closeDetails}>
      <section className="ds-sheet" role="dialog" aria-label={`Запись №${details.booking.id}`} onClick={e => e.stopPropagation()}>
        <div className="ds-sheet-grip" />
        <div className="ds-sheet-head">
          <div>
            <h3>{details.booking.client_name}</h3>
            <p>Запись №{details.booking.id} · {details.booking.date} {details.booking.time} · {details.booking.service_name}</p>
          </div>
          <button className="ds-icon-btn" onClick={closeDetails} aria-label="Закрыть"><X size={18} /></button>
        </div>
        <p>Предоплата за визит: {details.prepayment_total} ₽ · {labels[details.booking.status] || details.booking.status}</p>
        {details.visit.length > 1 && <p>Решение применяется ко всему визиту: {details.visit.map(b => b.service_name).join(', ')}.</p>}
        {details.booking.client_phone && <p><a href={`tel:${details.booking.client_phone}`}>{details.booking.client_phone}</a></p>}
        <h3 className="ds-h3">Чеки</h3>
        {!details.receipts.length && <p className="ds-sub">Сохранённого чека нет. Проверьте поступление в банке; старые чеки из Telegram здесь недоступны.</p>}
        {details.receipts.map(receipt => <button key={receipt.id} className="ds-btn ds-btn--ghost" disabled={busy} onClick={() => void viewProof(receipt.id)}>Открыть чек · {new Date(receipt.created_at + 'Z').toLocaleString('ru-RU')}</button>)}
        {proof && <div style={{ margin: '12px 0' }}>
          {proof.mime.startsWith('image/') && <img src={proof.url} alt="Чек клиента" style={{ maxWidth: '100%', maxHeight: 480, objectFit: 'contain' }} />}
          <p><a href={proof.url} target="_blank" rel="noopener noreferrer">Открыть файл</a> · <a href={proof.url} download="receipt">Скачать</a></p>
        </div>}
        {!!details.reviews.length && <div><h3 className="ds-h3">История проверки</h3>{details.reviews.map((review, index) => <p key={index} className="ds-sub">{review.decision === 'confirm' ? 'Оплата подтверждена' : review.decision === 'cancel' ? 'Бронь отменена' : 'Сообщение отклонено'} · {new Date(review.reviewed_at + 'Z').toLocaleString('ru-RU')} · сотрудник №{review.reviewer_id}</p>)}</div>}
        {error && <p role="alert" className="ds-error">{error}</p>}
        {details.booking.status === 'prepayment_sent' && <div className="ds-sheet-actions">
          <button className="ds-btn ds-btn--primary" disabled={busy} onClick={() => void decide('confirm')}>Подтвердить поступление</button>
          <button className="ds-btn ds-btn--ghost" disabled={busy} onClick={() => void decide('reject')}>Отклонить</button>
        </div>}
        {!['cancelled', 'completed'].includes(details.booking.status) && <div className="ds-sheet-actions">
          <button className="ds-btn ds-btn--ghost" disabled={busy} onClick={() => void decide('cancel')}>Отменить бронь</button>
        </div>}
      </section>
    </div>}
  </AdminShell>
}
