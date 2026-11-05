import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { errorMessage } from '../authUtils'

// Привязка Яндекс ID к аккаунту, в который человек уже вошёл.
export function YandexAccountLink() {
  const [state, setState] = useState<{ available: boolean; linked: boolean; login: string | null; telegram_join: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { api.yandexAccount().then(setState).catch(() => setState(null)) }, [])
  if (!state || (!state.available && !state.linked)) return null
  if (state.linked) {
    return <>
      <div className="bf-account-card"><span className="bf-kicker">Яндекс привязан</span><strong>{state.login || 'Яндекс ID'}</strong><p>Можно входить через Яндекс.</p></div>
      {state.telegram_join && <div className="bf-account-card">
        <span className="bf-kicker">Telegram</span>
        <p>Если у вашего Telegram тот же номер телефона, что и в Яндексе, войдите через Telegram, пока вы в этом аккаунте, — аккаунты объединятся автоматически. Записи и бизнесы сохранятся, входить можно будет и так, и так.</p>
        <button type="button" className="ds-btn ds-btn--primary" onClick={() => window.location.assign('/login?join=telegram')}>Войти через Telegram</button>
      </div>}
    </>
  }
  async function link() {
    setBusy(true); setError('')
    try { window.location.href = (await api.yandexLinkStart()).url }
    catch (err) { setError(errorMessage(err)); setBusy(false) }
  }
  return <div className="bf-account-card">
    <span className="bf-kicker">Яндекс ID</span>
    <p>Привяжите Яндекс к этому аккаунту, чтобы входить одним нажатием. Ваши записи и права сохранятся.</p>
    {error && <p role="alert" style={{ color: 'var(--s-danger)' }}>{error}</p>}
    <button type="button" className="ds-btn ds-btn--primary" disabled={busy} onClick={link}>{busy ? 'Переходим…' : 'Привязать Яндекс'}</button>
  </div>
}
