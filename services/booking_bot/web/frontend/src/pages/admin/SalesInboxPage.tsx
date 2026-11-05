import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type SalesInboxThread } from '../../api/client'
import { useAdminBusiness } from '../../hooks/useAdminBusiness'
import { AdminShell } from '../../components/AdminShell'
import { BusinessSelector } from '../../components/BusinessSelector'

function BusinessInbox({ businessId }: { businessId: number }) {
  const [threads, setThreads] = useState<SalesInboxThread[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const thread = threads.find(t => t.id === selected) || threads[0]
  const reload = useCallback(async () => { setThreads(await api.salesInbox(businessId)) }, [businessId])
  useEffect(() => {
    let active = true
    const load = () => api.salesInbox(businessId).then(rows => { if (active) setThreads(rows) })
      .catch(() => { if (active) setError('Не удалось загрузить диалоги') })
    void load()
    const timer = window.setInterval(load, 8000)
    return () => { active = false; window.clearInterval(timer) }
  }, [businessId])
  const act = async (resume: boolean) => {
    if (!thread || busy || (!resume && !input.trim())) return
    setBusy(true); setError('')
    try {
      if (resume) { await api.salesResume(thread.id); setInput('') }
      else { await api.salesReply(thread.id, input.trim()); setInput('') }
      await reload()
    } catch { setError('Не удалось выполнить действие. Обновите диалоги и попробуйте снова.') }
    finally { setBusy(false) }
  }
  return <>
    <p className="ds-sub" style={{ margin: '12px 0' }}>Диалоги, переданные ИИ оператору. Клиент увидит ваш ответ в чате.</p>
    {error && <p role="alert" className="ds-sub">{error}</p>}
    {!threads.length && <p className="ds-text">Диалогов в очереди нет.</p>}
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
      {threads.map(t => <button className="ds-chip" disabled={busy} key={t.id} aria-pressed={thread?.id === t.id} onClick={() => { setSelected(t.id); setInput('') }} style={{ textAlign: 'left', maxWidth: 320 }}>
        <strong>Диалог {t.id.slice(0, 8)}</strong><br />
        <small>{new Date(t.updated_at + 'Z').toLocaleString('ru-RU')}</small><br />
        {(t.messages.filter(m => m.role === 'user').at(-1)?.text || t.reason || '').slice(0, 90)}
      </button>)}
    </div>
    {thread && <div className="ds-card" style={{ padding: 14 }}>
      <p className="ds-sub">Причина: {thread.reason || 'Клиент попросил оператора'}</p>
      <div style={{ maxHeight: '50vh', overflowY: 'auto', margin: '12px 0' }}>
        {thread.messages.map(m => <div key={m.id} style={{ marginBottom: 12, whiteSpace: 'pre-wrap' }}><strong>{m.role === 'user' ? 'Клиент' : 'Консультант'}: </strong>{m.text}</div>)}
      </div>
      <label className="ds-sub">Ответ клиенту<textarea className="ds-input" rows={3} maxLength={4000} value={input} disabled={busy} onChange={e => setInput(e.target.value)} /></label>
      <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <button className="ds-btn ds-btn--primary" disabled={busy || !input.trim()} onClick={() => act(false)}>Отправить</button>
        <button className="ds-btn ds-btn--ghost" disabled={busy} onClick={() => act(true)}>Вернуть ИИ</button>
      </div>
    </div>}
  </>
}

export function SalesInboxPage() {
  const { businesses, selectedId, selectBusiness } = useAdminBusiness()
  return <AdminShell title="Диалоги ИИ" headerExtra={<BusinessSelector businesses={businesses} selectedId={selectedId} onSelect={selectBusiness} />}>
    <Link to="/admin/sales/settings">Настройки ИИ-продавца</Link>
    {selectedId && <BusinessInbox key={selectedId} businessId={selectedId} />}
  </AdminShell>
}
