import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { errorMessage } from '../authUtils'

// Link VK to the ServGo account already authenticated in this browser.
export function VkAccountLink() {
  const [state, setState] = useState<{ available: boolean; linked: boolean; name: string | null } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { api.vkAccount().then(setState).catch(() => setState(null)) }, [])
  if (!state || (!state.available && !state.linked)) return null
  if (state.linked) {
    return <div className="bf-account-card">
      <span className="bf-kicker">VK ID привязан</span>
      <strong>{state.name || 'ВКонтакте'}</strong>
      <p>Через VK можно входить в тот же аккаунт ServGo, включая Studio и ваши организации.</p>
    </div>
  }
  const link = async () => {
    setBusy(true); setError('')
    try { window.location.assign((await api.vkLinkStart()).url) }
    catch (err) { setError(errorMessage(err)); setBusy(false) }
  }
  return <div className="bf-account-card">
    <span className="bf-kicker">VK ID</span>
    <p>Добавьте вход через ВКонтакте к этому аккаунту ServGo. Ваши организации и права сохранятся.</p>
    {error && <p role="alert" style={{ color: 'var(--s-danger)' }}>{error}</p>}
    <button type="button" className="ds-btn ds-btn--primary" disabled={busy} onClick={() => void link()}>
      {busy ? 'Переходим…' : 'Привязать VK ID'}
    </button>
  </div>
}
