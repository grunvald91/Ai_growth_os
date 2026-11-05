import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { errorMessage, saveLogin } from '../authUtils'

type Status = Awaited<ReturnType<typeof api.telegramLinkStatus>>
const KEY = 'servgoTelegramLink'
export function TelegramAccountLink() {
  const [id, setId] = useState(() => sessionStorage.getItem(KEY) || '')
  const [url, setUrl] = useState('')
  const [status, setStatus] = useState<Status | null>(null)
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (!id) return
    let alive = true
    async function poll() {
      try { const result = await api.telegramLinkStatus(id); if (alive) { setStatus(result); setError('') } }
      catch (e) { if (alive) setError(errorMessage(e)) }
    }
    void poll()
    const timer = setInterval(() => void poll(), 3000)
    return () => { alive = false; clearInterval(timer) }
  }, [id])
  async function start() {
    setBusy(true); setError('')
    try {
      const result = await api.startTelegramLink(password)
      sessionStorage.setItem(KEY, result.challenge_id)
      setStatus(null); setId(result.challenge_id); setUrl(result.url); setPassword('')
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  async function cancel() {
    setBusy(true)
    try { await api.cancelTelegramLink(id); sessionStorage.removeItem(KEY); setId(''); setUrl(''); setStatus(null); setPassword(''); setError('') }
    catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  async function finish() {
    if (!status?.telegram || !window.confirm(`Объединить с Telegram ${status.telegram.name} (${status.telegram.id})? Ваши записи и бизнесы будут доступны через оба способа входа.`)) return
    setBusy(true); setError('')
    try {
      const result = await api.completeTelegramLink(id, password)
      saveLogin(result); sessionStorage.removeItem(KEY); localStorage.removeItem('adminBusinessId')
      window.location.assign(result.role === 'client' ? '/my' : '/admin')
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  const expired = status && ['expired', 'cancelled'].includes(status.state)
  const confirmed = status?.state === 'telegram_confirmed'
  return <section className="ds-card" style={{ padding: 16, marginTop: 16 }}>
    <h2 className="ds-h3">Связать Telegram</h2>
    <p>Подтвердите свой Telegram в боте, затем завершите объединение здесь. Бизнесы и записи обоих аккаунтов сохранятся.</p>
    {error && <p role="alert">{error}</p>}
    {(!id || confirmed) && <form onSubmit={e => { e.preventDefault(); void (confirmed ? finish() : start()) }}>
      <label className="ds-field">Пароль email-аккаунта<input className="ds-input" type="password" autoComplete="current-password" minLength={12} maxLength={128} required value={password} onChange={e => setPassword(e.target.value)} /></label>
      {confirmed && <p>Telegram: <strong>{status.telegram?.name}</strong> · {status.telegram?.username ? `@${status.telegram.username}` : status.telegram?.id}. Бизнесов: {status.business_count}. Проверьте, что это ваш аккаунт.</p>}
      <button className="ds-btn ds-btn--primary" disabled={busy}>{confirmed ? 'Объединить аккаунты' : 'Получить ссылку для привязки'}</button>
    </form>}
    {id && !confirmed && !expired && <><p>Откройте бота и нажмите «Подтвердить мой Telegram». Ссылка действует 10 минут. Не пересылайте её другим людям.</p>
      {url ? <a className="ds-btn ds-btn--primary" href={url} target="_blank" rel="noopener noreferrer">Открыть Telegram</a> : <p>Продолжите в уже открытом боте или отмените запрос и получите новую ссылку.</p>}
    </>}
    {expired && <p>Запрос истёк или отменён. Начните привязку заново.</p>}
    {id && <button className="ds-btn ds-btn--ghost" disabled={busy} onClick={() => void cancel()}>Отменить запрос</button>}
  </section>
}
