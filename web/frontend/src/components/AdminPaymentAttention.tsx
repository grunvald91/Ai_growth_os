import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useLocation } from 'react-router-dom'
import { api } from '../api/client'
import { errorMessage } from '../authUtils'
import { useConnectivity } from '../utils/useConnectivity'

type Attention = Awaited<ReturnType<typeof api.prepaymentAttention>>
type Details = Awaited<ReturnType<typeof api.getPaymentDetails>>
export function AdminPaymentAttention() {
  const { pathname } = useLocation()
  const role = localStorage.getItem('userRole')
  const enabled = pathname.startsWith('/admin') && !!localStorage.getItem('token') && (role === 'admin' || role === 'owner')
  return enabled ? <PendingPayments /> : null
}
function PendingPayments() {
  const { pathname } = useLocation()
  const online = useConnectivity()
  const [attention, setAttention] = useState<Attention | null>(null)
  const [details, setDetails] = useState<Details | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [decisionError, setDecisionError] = useState('')
  const modal = useRef<HTMLElement>(null)
  const [proof, setProof] = useState<{ url: string; mime: string; bookingId: number; version: number } | null>(null)
  const alive = useRef(true)
  const request = useRef(0)
  const refresh = useCallback(async () => {
    const seq = ++request.current
    // Payment checking must never trap the owner inside an offline schedule.
    if (!online) {
      setAttention(null)
      setDetails(null)
      setError('Нет соединения: проверка предоплат временно недоступна.')
      return
    }
    try {
      const result = await api.prepaymentAttention()
      if (alive.current && seq === request.current) { setAttention(result); setError('') }
    } catch {
      if (alive.current && seq === request.current) {
        setAttention(null) // A previous successful poll is now stale.
        setDetails(null)
        setError('Не удалось проверить новые предоплаты. Расписание остаётся доступным.')
      }
    }
  }, [online])
  useEffect(() => {
    alive.current = true
    const update = () => { if (!document.hidden) void refresh() }
    const pushed = (e: MessageEvent) => { if (e.data?.type === 'servgo-prepayment-update') update() }
    update()
    const timer = setInterval(update, 5000)
    window.addEventListener('focus', update); window.addEventListener('servgo:payment-reviewed', update)
    document.addEventListener('visibilitychange', update)
    navigator.serviceWorker?.addEventListener('message', pushed)
    return () => {
      alive.current = false; clearInterval(timer)
      window.removeEventListener('focus', update); window.removeEventListener('servgo:payment-reviewed', update)
      document.removeEventListener('visibilitychange', update); navigator.serviceWorker?.removeEventListener('message', pushed)
    }
  }, [refresh])
  const first = attention?.first
  const id = first?.booking.id
  const version = first?.booking.version
  const inQueue = pathname === '/admin/prepayments'
  useEffect(() => {
    if (!id || inQueue) return
    let current = true
    api.getPaymentDetails(id).then(result => { if (current) setDetails(result) }).catch(e => { if (current) { setDetails(null); setError(errorMessage(e)) } })
    return () => { current = false }
  }, [id, version, inQueue])
  useEffect(() => {
    const node = modal.current
    if (!node || inQueue || !id) return
    node.focus()
    const trap = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); return }
      if (e.key !== 'Tab') return
      const elements = Array.from(node.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), [tabindex="0"]'))
      const firstElement = elements[0], lastElement = elements[elements.length - 1]
      if (!firstElement) { e.preventDefault(); node.focus(); return }
      if (e.shiftKey && (document.activeElement === firstElement || document.activeElement === node)) { e.preventDefault(); lastElement.focus() }
      else if (!e.shiftKey && (document.activeElement === lastElement || !node.contains(document.activeElement))) { e.preventDefault(); firstElement.focus() }
    }
    node.addEventListener('keydown', trap)
    return () => node.removeEventListener('keydown', trap)
  }, [id, inQueue])
  useEffect(() => () => { if (proof) URL.revokeObjectURL(proof.url) }, [proof])
  async function decide(decision: 'confirm' | 'reject') {
    if (!details || details.booking.id !== id) return
    if (!window.confirm(decision === 'confirm' ? 'Вы проверили фактическое поступление денег? Подтвердить оплату и бронь?' : 'Поступление не подтверждено? Клиенту будет дано ещё 10 минут для исправления сообщения об оплате.')) return
    setBusy(true); setDecisionError('')
    try { await api.reviewPrepayment(id, decision, details.booking.version); setProof(null); await refresh() }
    catch (e) { setDecisionError(errorMessage(e)); await refresh() }
    finally { setBusy(false) }
  }
  async function receipt(receiptId: number) {
    setBusy(true)
    try { const blob = await api.downloadReceipt(receiptId); setProof({ url: URL.createObjectURL(blob), mime: blob.type, bookingId: id!, version: version! }) }
    catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  function queue() {
    if (first) localStorage.setItem('adminBusinessId', String(first.business_id))
    // Full navigation also resets the shared business cache/selector.
    window.location.assign('/admin/prepayments')
  }
  const hasFreshAttention = !!attention?.count && online && !error
  if (!hasFreshAttention && !error) return null
  const ready = details && details.booking.id === id && details.booking.version === version && details.booking.status === 'prepayment_sent'
  return createPortal(<>
    <div role="status" style={hasFreshAttention
      ? { position: 'fixed', top: 0, left: 0, right: 0, zIndex: 10000, background: '#9a3412', color: 'white', padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }
      : { position: 'fixed', right: 12, bottom: 76, zIndex: 1000, maxWidth: 'min(340px, calc(100vw - 24px))', background: '#9a3412', color: 'white', padding: '10px 14px', borderRadius: 10, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <strong>{hasFreshAttention ? `Предоплаты на проверке: ${attention?.count}` : 'Проверка предоплат недоступна'}</strong>
      {hasFreshAttention
        ? <button onClick={queue} style={{ color: '#9a3412', background: 'white', padding: '6px 10px', borderRadius: 8 }}>Проверить</button>
        : <button onClick={() => setError('')} style={{ color: '#9a3412', background: 'white', padding: '6px 10px', borderRadius: 8 }}>Закрыть</button>}
      {error && <span>{error}</span>}
    </div>
    {hasFreshAttention && !inQueue && ready && <div style={{ position: 'fixed', inset: 0, zIndex: 10001, background: 'rgba(0,0,0,.6)', display: 'grid', placeItems: 'center', padding: 18 }}>
      <section ref={modal} tabIndex={-1} role="alertdialog" aria-modal="true" aria-labelledby="payment-alert-title" style={{ background: 'white', color: '#111827', borderRadius: 16, padding: 22, width: 'min(100%, 560px)', maxHeight: '90vh', overflowY: 'auto', fontSize: 16, lineHeight: 1.5 }}>
        <h2 id="payment-alert-title" style={{ fontSize: 20, fontWeight: 700, marginBottom: 12 }}>Клиент сообщил об отправке предоплаты</h2>
        <p><strong>{first?.business_name}</strong> · {first?.booking.client_name}</p>
        <p>{first?.booking.date} {first?.booking.time} · {first?.booking.service_name}</p>
        <p>Проверьте деньги в банке и примите решение. Бронь ожидает проверки; десятиминутная автоотмена остановлена.</p>
        {ready && <><p>Предоплата за визит: <strong>{details.prepayment_total} ₽</strong></p>
          {details.receipts.length ? details.receipts.map(p => <button key={p.id} disabled={busy} onClick={() => void receipt(p.id)}>Открыть чек №{p.id}</button>) : <p>Сохранённого чека нет. Проверьте поступление в банке.</p>}
          {proof && proof.bookingId === id && proof.version === version && <div>{proof.mime.startsWith('image/') && <img src={proof.url} alt="Чек клиента" style={{ maxWidth: '100%', maxHeight: 250 }} />}<p><a href={proof.url} target="_blank" rel="noopener noreferrer">Открыть файл чека</a></p></div>}
        </>}
        {(error || decisionError) && <p role="alert">{decisionError || error}</p>}
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 14 }}>
          <button style={{ background: '#166534', color: 'white', borderRadius: 9, padding: '10px 14px', fontWeight: 600, opacity: busy || !ready ? .5 : 1 }} disabled={busy || !ready} onClick={() => void decide('confirm')}>Да, деньги поступили</button>
          <button style={{ background: '#fff1f2', color: '#9f1239', border: '1px solid #fda4af', borderRadius: 9, padding: '10px 14px', fontWeight: 600, opacity: busy || !ready ? .5 : 1 }} disabled={busy || !ready} onClick={() => void decide('reject')}>Нет, поступление не подтверждено</button>
          <button style={{ textDecoration: 'underline', padding: '8px 0' }} onClick={queue}>Открыть список предоплат</button>
        </div>
        <p>Ожидают решения: {attention.count}. Уведомление останется до проверки.</p>
      </section>
    </div>}
  </>, document.body)
}
