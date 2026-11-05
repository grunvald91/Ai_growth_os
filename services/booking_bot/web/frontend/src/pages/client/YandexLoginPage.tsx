import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { postLoginPath } from '../../utils/postLogin'
import { api } from '../../api/client'
import { errorMessage, saveLogin } from '../../authUtils'
import { ClientShell } from '../../components/ClientShell'

type Step = 'working' | 'code' | 'unavailable' | 'error'

// Сюда Яндекс возвращает человека через наш сервер; одноразовый билет лежит в адресе после «#».
export function YandexLoginPage() {
  const navigate = useNavigate()
  const ticket = useRef<string>('')
  const started = useRef(false)
  const [step, setStep] = useState<Step>('working')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // existing: у человека уже есть отдельный аккаунт через Яндекс, мы предлагаем объединить его с Telegram.
  const [account, setAccount] = useState<'new' | 'existing'>('new')

  async function run(extra: { code?: string; separate?: boolean } = {}) {
    setBusy(true); setError('')
    try {
      const result = await api.yandexExchange({ ticket: ticket.current, ...extra })
      if (result.status === 'ok') {
        saveLogin(result)
        navigate(await postLoginPath(result.role), { replace: true })
      } else if (result.status === 'linked') {
        navigate('/account', { replace: true })
      } else {
        setAccount(result.account)
        setStep(result.status === 'code_required' ? 'code' : 'unavailable')
      }
    } catch (err) {
      const message = errorMessage(err)
      if (step === 'code' && extra.code) setError(message)
      else { setError(message); setStep('error') }
    } finally { setBusy(false) }
  }

  useEffect(() => {
    if (started.current) return
    started.current = true
    ticket.current = new URLSearchParams(window.location.hash.replace(/^#/, '')).get('ticket') || ''
    window.history.replaceState(null, '', window.location.pathname)
    if (!ticket.current) { setError('Ссылка входа устарела. Нажмите «Войти через Яндекс» ещё раз'); setStep('error'); return }
    void run()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function submit(event: FormEvent) { event.preventDefault(); void run({ code }) }

  return (
    <ClientShell backTo="/login" title="Вход через Яндекс">
      <div className="ds-form-narrow" style={{ paddingTop: 4 }}>
        {step === 'working' && <p className="ds-hint" role="status">Входим…</p>}
        {step === 'error' && (
          <div role="alert">
            <p>{error}</p>
            <Link to="/login" className="ds-btn ds-btn--primary" style={{ marginTop: 12 }}>Вернуться ко входу</Link>
          </div>
        )}
        {step === 'code' && (
          <form onSubmit={submit} className="ds-card" style={{ padding: 16, display: 'grid', gap: 12 }}>
            <h2 className="ds-h3">Подтвердите, что это вы</h2>
            <p>Номер из Яндекса уже есть в ServGo у аккаунта с Telegram. Мы отправили код в этот Telegram. {account === 'existing'
              ? 'После ввода кода ваш аккаунт через Яндекс объединится с аккаунтом Telegram: записи и бизнесы обоих сохранятся.'
              : 'После ввода кода вход через Яндекс присоединится к вашему аккаунту, записи и история сохранятся.'}</p>
            <label className="ds-field">Код из Telegram
              <input className="ds-input" required inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code"
                value={code} onChange={e => setCode(e.target.value.replace(/\D/g, ''))} />
            </label>
            {error && <p role="alert" style={{ color: 'var(--s-danger)' }}>{error}</p>}
            <button className="ds-btn ds-btn--primary" disabled={busy || code.length !== 6}>{busy ? 'Подождите…' : 'Подтвердить'}</button>
            <button type="button" className="ds-btn ds-btn--ghost" disabled={busy} onClick={() => void run()}>Отправить код ещё раз</button>
            <button type="button" className="ds-btn ds-btn--ghost" disabled={busy} onClick={() => void run({ separate: true })}>{account === 'existing' ? 'Не объединять, войти как раньше' : 'Не присоединять, создать отдельный аккаунт'}</button>
          </form>
        )}
        {step === 'unavailable' && (
          <div className="ds-card" style={{ padding: 16, display: 'grid', gap: 12 }} role="status">
            <h2 className="ds-h3">Не удалось отправить код в Telegram</h2>
            <p>{account === 'existing' ? 'Можно войти в ваш аккаунт через Яндекс как раньше, без объединения.' : 'Можно войти с отдельным аккаунтом ServGo через Яндекс.'} Он не будет связан с аккаунтом Telegram, а записи Telegram-аккаунта останутся там.</p>
            {error && <p role="alert" style={{ color: 'var(--s-danger)' }}>{error}</p>}
            <button className="ds-btn ds-btn--primary" disabled={busy} onClick={() => void run({ separate: true })}>{busy ? 'Подождите…' : account === 'existing' ? 'Войти без объединения' : 'Продолжить с отдельным аккаунтом'}</button>
            <button className="ds-btn ds-btn--ghost" disabled={busy} onClick={() => void run()}>Попробовать отправить код ещё раз</button>
          </div>
        )}
      </div>
    </ClientShell>
  )
}
