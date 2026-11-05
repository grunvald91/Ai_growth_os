import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { postLoginPath } from '../utils/postLogin'
import { api } from '../api/client'
import { errorMessage, saveLogin } from '../authUtils'

export function EmailAuthForm({ link = false }: { link?: boolean }) {
  const navigate = useNavigate()
  const [mode, setMode] = useState<'login' | 'register' | 'reset' | 'link'>(link ? 'link' : 'login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [challenge, setChallenge] = useState('')
  const [code, setCode] = useState('')
  const [available, setAvailable] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { api.emailCapabilities().then(r => setAvailable(r.available)).catch(() => setAvailable(false)) }, [])

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true); setError('')
    try {
      if (challenge || mode === 'login') {
        const result = challenge ? await api.emailVerify(challenge, code) : await api.emailLogin(email, password)
        saveLogin(result)
        navigate(await postLoginPath(result.role), { replace: true })
      } else {
        const result = await api.emailStart({ email, password, full_name: name, purpose: mode })
        setChallenge(result.challenge_id)
        setPassword('')
      }
    } catch (err) { setError(errorMessage(err)) } finally { setBusy(false) }
  }
  function changeMode(next: typeof mode) { setMode(next); setChallenge(''); setCode(''); setPassword(''); setError('') }

  return <div className="ds-card" style={{ padding: 16 }}>
    <h2 className="ds-h3">{mode === 'link' ? 'Привязать вход по email' : mode === 'register' ? 'Создать аккаунт' : mode === 'reset' ? 'Восстановить пароль' : 'Войти по email'}</h2>
    {available === false && <p className="ds-hint" role="status">Вход по email пока не настроен. Обратитесь в поддержку.</p>}
    <form onSubmit={submit} style={{ display: 'grid', gap: 12, marginTop: 12 }}>
      {!challenge ? <>
        {mode === 'register' && <label className="ds-field">Имя<input className="ds-input" required maxLength={255} value={name} onChange={e => setName(e.target.value)} autoComplete="name" /></label>}
        <label className="ds-field">Email<input className="ds-input" type="email" required maxLength={320} value={email} onChange={e => setEmail(e.target.value)} autoComplete="email" /></label>
        <label className="ds-field">{mode === 'reset' ? 'Новый пароль' : 'Пароль'}<input className="ds-input" type="password" required minLength={12} maxLength={128} value={password} onChange={e => setPassword(e.target.value)} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} /></label>
        <p className="ds-hint">Не менее 12 символов. Telegram не требуется.</p>
      </> : <>
        <p>Если действие доступно для {email}, на почту отправлен код. Он действует 15 минут.</p>
        <label className="ds-field">Код из письма<input className="ds-input" required inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" value={code} onChange={e => setCode(e.target.value)} /></label>
        <button type="button" className="ds-btn ds-btn--ghost" disabled={busy} onClick={() => changeMode(mode)}>Запросить новый код или изменить данные</button>
      </>}
      {error && <p role="alert" style={{ color: 'var(--s-danger)' }}>{error}</p>}
      <button className="ds-btn ds-btn--primary" disabled={busy || !available}>{busy ? 'Подождите…' : challenge ? 'Подтвердить' : mode === 'login' ? 'Войти' : 'Отправить код на почту'}</button>
    </form>
    {!link && !challenge && <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
      {mode !== 'login' && <button className="ds-btn ds-btn--ghost" disabled={busy} onClick={() => changeMode('login')}>Уже есть аккаунт</button>}
      {mode !== 'register' && <button className="ds-btn ds-btn--ghost" disabled={busy} onClick={() => changeMode('register')}>Регистрация</button>}
      {mode !== 'reset' && <button className="ds-btn ds-btn--ghost" disabled={busy} onClick={() => changeMode('reset')}>Забыли пароль?</button>}
    </div>}
  </div>
}
