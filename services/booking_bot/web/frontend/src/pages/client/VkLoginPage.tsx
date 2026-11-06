import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { errorMessage, saveLogin } from '../../authUtils'
import { ClientShell } from '../../components/ClientShell'
import { postLoginPath } from '../../utils/postLogin'

type Step = 'working' | 'merge' | 'error'

export function VkLoginPage() {
  const navigate = useNavigate()
  const ticket = useRef('')
  const started = useRef(false)
  const [step, setStep] = useState<Step>('working')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const finish = async (merge = false) => {
    setBusy(true); setError('')
    try {
      const result = await api.vkExchange({ ticket: ticket.current, merge })
      if (result.status === 'ok') {
        saveLogin(result)
        navigate(await postLoginPath(result.role), { replace: true })
      } else if (result.status === 'linked') {
        navigate('/account', { replace: true })
      } else {
        setStep('merge')
      }
    } catch (err) {
      setError(errorMessage(err))
      setStep('error')
    } finally { setBusy(false) }
  }

  useEffect(() => {
    if (started.current) return
    started.current = true
    ticket.current = new URLSearchParams(window.location.hash.slice(1)).get('ticket') || ''
    window.history.replaceState(null, '', window.location.pathname)
    if (!ticket.current) {
      setError('Ссылка VK ID устарела. Начните вход заново.')
      setStep('error')
      return
    }
    void finish()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return <ClientShell title="Вход через VK ID" backTo="/login">
    <div className="ds-form-narrow" style={{ paddingTop: 4 }}>
      {step === 'working' && <p className="ds-hint" role="status">Подтверждаем вход через VK ID…</p>}
      {step === 'merge' && <div className="ds-card" style={{ padding: 16, display: 'grid', gap: 12 }}>
        <h2 className="ds-h3">Объединить аккаунты ServGo?</h2>
        <p>Вы подтвердили VK ID, который ранее создал отдельный аккаунт. Вы также вошли в основной аккаунт
          ServGo. Можно объединить их, чтобы сохранить организации и записи в одном профиле.</p>
        <p className="ds-hint">Это действие требует вашего подтверждения. Если у обоих аккаунтов есть
          оплаченные подписки, объединение будет остановлено для проверки.</p>
        <button type="button" className="ds-btn ds-btn--primary" disabled={busy}
          onClick={() => void finish(true)}>{busy ? 'Объединяем…' : 'Объединить и привязать VK'}</button>
        <Link to="/account" className="ds-btn ds-btn--ghost">Не объединять</Link>
      </div>}
      {step === 'error' && <div role="alert">
        <p>{error}</p>
        <Link to="/login" className="ds-btn ds-btn--primary" style={{ marginTop: 12 }}>
          Вернуться ко входу
        </Link>
      </div>}
    </div>
  </ClientShell>
}
