import { useState, useRef, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Phone, Send } from 'lucide-react'
import { consumeLoginDestination, pendingLoginDestination, postLoginPath, rememberLoginDestination, STUDIO_ENTRY } from '../../utils/postLogin'
import { api, type TelegramLoginPayload } from '../../api/client'
import { errorMessage, saveLogin } from '../../authUtils'
import { ClientShell } from '../../components/ClientShell'
import { EmailAuthForm } from '../../components/EmailAuthForm'
import { YandexLoginButton } from '../../components/YandexLoginButton'

// Кнопка Telegram сначала берётся через собственный прокси ServGo (tg.servgo.ru, сервер relay 89): в РФ
// telegram.org и oauth.telegram.org у многих не открываются. Скрипт кнопки сам берёт адрес рамки и окна
// входа с того хоста, откуда загружен; прокси отдаёт /widget.js, /embed, /auth и статику с переписанными
// ссылками. Если прокси не отвечает, пробуем напрямую telegram.org.
const WIDGET_SOURCES = [
  'https://tg.servgo.ru/widget.js',
  'https://telegram.org/js/telegram-widget.js?22',
]
const WIDGET_WAIT_MS = 10000
const YANDEX_PROBLEMS: Record<string, string> = {
  expired: 'Вход через Яндекс не завершился: ссылка устарела. Нажмите кнопку ещё раз.',
  denied: 'Вход через Яндекс отменён.',
  failed: 'Яндекс не ответил. Попробуйте ещё раз или войдите другим способом.',
  unavailable: 'Вход через Яндекс пока недоступен.',
}

export function LoginPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const yandexProblem = YANDEX_PROBLEMS[searchParams.get('yandex') || '']
  const studioNext = searchParams.get('next')
  // Пришли из профиля Яндекс-аккаунта, чтобы объединить его с Telegram: сразу показываем кнопку Telegram.
  const joinTelegram = searchParams.get('join') === 'telegram'
  const [mode, setMode] = useState<'phone' | 'code'>('phone')
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState(['', '', '', '', '', ''])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [codeSentVia, setCodeSentVia] = useState<'telegram' | 'fallback'>('telegram')
  const codeRefs = useRef<(HTMLInputElement | null)[]>([])
  const telegramWidgetRef = useRef<HTMLDivElement | null>(null)
  // Кнопка Telegram приходит с telegram.org. Если эта сеть там закрыта (блокировка провайдера,
  // расширение браузера), кнопка не появится: тогда честно говорим об этом и показываем другие входы.
  const [widget, setWidget] = useState<'loading' | 'ready' | 'failed'>('loading')
  const [widgetAttempt, setWidgetAttempt] = useState(0)
  const [widgetSource, setWidgetSource] = useState(0)

  useEffect(() => {
    if (studioNext !== STUDIO_ENTRY) return
    rememberLoginDestination(studioNext)
    // Already authenticated users should not be asked to sign in again.
    if (!localStorage.getItem('token') ||
        localStorage.getItem('servgoExplicitLogout') === '1' ||
        sessionStorage.getItem('servgoExplicitLogout') === '1') { /* do not auto-enter yet */ }
    let active = true
    const handleSavedLogin = () => {
      if (!active || !localStorage.getItem('token')) return
      void api.getMe().then(() => {
        if (active && pendingLoginDestination() === STUDIO_ENTRY) {
          consumeLoginDestination()
          navigate(STUDIO_ENTRY, { replace: true })
        }
      }).catch(() => {})
    }
    void api.getMe().then(() => {
      if (active && pendingLoginDestination() === STUDIO_ENTRY) {
        consumeLoginDestination()
        navigate(STUDIO_ENTRY, { replace: true })
      }
    }).catch(() => { /* The normal login page remains available. */ })
    window.addEventListener('servgo-login-saved', handleSavedLogin)
    return () => { active = false; window.removeEventListener('servgo-login-saved', handleSavedLogin) }
  }, [navigate, studioNext])

  useEffect(() => {
    if (joinTelegram) telegramWidgetRef.current?.scrollIntoView({ block: 'center' })
  }, [joinTelegram])

  useEffect(() => {
    if (mode === 'code' && codeRefs.current[0]) {
      codeRefs.current[0]?.focus()
    }
  }, [mode])

  useEffect(() => {
    if (mode !== 'phone' || !telegramWidgetRef.current) return
    const host = telegramWidgetRef.current
    const callbackWindow = window as Window & {
      servgoTelegramWidgetAuth?: (data: TelegramLoginPayload) => void
    }
    let active = true
    callbackWindow.servgoTelegramWidgetAuth = (data) => {
      setLoading(true)
      setError('')
      void api.telegramLogin(data)
        .then(async result => {
          if (!active) return
          saveLogin(result)
          const path = await postLoginPath(result.role)
          if (active) navigate(path, { replace: true })
        })
        .catch(err => { if (active) setError(errorMessage(err)) })
        .finally(() => { if (active) setLoading(false) })
    }
    const script = document.createElement('script')
    script.async = true
    script.src = WIDGET_SOURCES[widgetSource]
    script.setAttribute('data-telegram-login', 'Sochiservice_bot')
    script.setAttribute('data-size', 'large')
    script.setAttribute('data-onauth', 'window.servgoTelegramWidgetAuth(user)')
    // Этот источник не сработал: берём следующий, а если их не осталось, сообщаем об этом.
    const nextSource = () => {
      if (!active) return
      if (widgetSource + 1 < WIDGET_SOURCES.length) setWidgetSource(widgetSource + 1)
      else setWidget('failed')
    }
    script.onerror = nextSource
    host.appendChild(script)
    // Скрипт мог загрузиться, а рамка с самой кнопкой — нет (пустая рамка тоже бывает). Рамка, которая
    // действительно открылась, присылает странице сообщение «ready»: только оно считается успехом.
    const sourceOrigin = new URL(WIDGET_SOURCES[widgetSource]).origin
    const frameOrigins = sourceOrigin === 'https://telegram.org' ? ['https://oauth.telegram.org'] : [sourceOrigin]
    let opened = false
    const onMessage = (event: MessageEvent) => {
      if (!active || !frameOrigins.includes(event.origin) || typeof event.data !== 'string') return
      const frame = host.querySelector('iframe')
      if (!frame || event.source !== frame.contentWindow) return
      try {
        const data = JSON.parse(event.data) as { event?: string }
        if (data.event === 'ready' || data.event === 'resize') { opened = true; setWidget('ready') }
      } catch {
        // Чужие сообщения не про кнопку.
      }
    }
    window.addEventListener('message', onMessage)
    const giveUp = window.setTimeout(() => {
      if (active && !opened) nextSource()
    }, WIDGET_WAIT_MS)
    return () => {
      active = false
      window.removeEventListener('message', onMessage)
      window.clearTimeout(giveUp)
      script.remove()
      host.replaceChildren()
      delete callbackWindow.servgoTelegramWidgetAuth
    }
  }, [mode, navigate, widgetAttempt, widgetSource])

  const handleSendCode = async () => {
    if (!phone) return
    setLoading(true)
    setError('')
    try {
      const res = await api.loginPhone(phone)
      setCodeSentVia(res.fallback ? 'fallback' : 'telegram')
      setMode('code')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  const handleCodeInput = async (index: number, value: string) => {
    if (value.length > 1) value = value.slice(-1)
    if (!/^\d*$/.test(value)) return

    const newCode = [...code]
    newCode[index] = value
    setCode(newCode)

    if (value && index < 5) {
      codeRefs.current[index + 1]?.focus()
    }

    if (value && index === 5) {
      const fullCode = newCode.join('')
      if (fullCode.length === 6) {
        setLoading(true)
        setError('')
        try {
          const res = await api.verifyCode(phone, fullCode)
          saveLogin(res)
          navigate(await postLoginPath(res.role), { replace: true })
        } catch (err) {
          setError(errorMessage(err))
          setCode(['', '', '', '', '', ''])
          codeRefs.current[0]?.focus()
        } finally {
          setLoading(false)
        }
      }
    }
  }

  const handleCodeKeyDown = (index: number, e: React.KeyboardEvent) => {
    if (e.key === 'Backspace' && !code[index] && index > 0) {
      codeRefs.current[index - 1]?.focus()
    }
  }

  const handleTelegramLogin = () => {
    window.open('https://t.me/Sochiservice_bot', '_blank', 'noopener,noreferrer')
  }

  return (
    <ClientShell backTo="/" title="Вход">
      <div className="ds-form-narrow" style={{ paddingTop: 4 }}>
        {studioNext === STUDIO_ENTRY && (
          <p role="status" className="ds-hint" style={{ marginBottom: 16 }}>
            Войдите в ваш аккаунт ServGo. После входа откроется Studio для доступной компании.
          </p>
        )}
        {yandexProblem && <p role="alert" style={{ color: 'var(--s-danger)', marginBottom: 12 }}>{yandexProblem}</p>}
        <div style={{ marginBottom: 16 }}><YandexLoginButton /></div>
        <EmailAuthForm />
        <p className="ds-hint" style={{ margin: '16px 0' }}>Для аккаунтов с привязанным Telegram доступны прежние способы входа:</p>
        {joinTelegram && <p className="ds-hint" role="status" style={{ marginBottom: 12, fontWeight: 600 }}>Войдите через Telegram — если номер телефона в нём тот же, что в Яндексе, аккаунты объединятся автоматически.</p>}
        {mode === 'phone' && (
          <div>
            <div className="ds-card" style={{ padding: 16, marginBottom: 14, textAlign: 'center' }}>
              <p className="ds-text" style={{ marginBottom: 12, fontWeight: 600 }}>Войти через Telegram</p>
              <div ref={telegramWidgetRef} style={{ minHeight: widget === 'failed' ? 0 : 40, display: 'flex', justifyContent: 'center' }} />
              {widget === 'loading' && <p className="ds-hint" style={{ marginTop: 6 }}>Загружаем кнопку Telegram…</p>}
              {widget === 'failed' ? (
                <div role="status">
                  <p className="ds-hint" style={{ marginTop: 6 }}>
                    Кнопка Telegram не открылась ни через наш сервер, ни напрямую: возможно, её блокирует
                    расширение браузера или сеть. Войдите по номеру телефона ниже (код придёт в бота)
                    или по email выше.
                  </p>
                  <button type="button" onClick={() => { setWidget('loading'); setWidgetSource(0); setWidgetAttempt((n) => n + 1) }}
                    className="ds-btn ds-btn--ghost ds-btn--sm" style={{ marginTop: 8 }}>
                    Попробовать ещё раз
                  </button>
                </div>
              ) : (
                <p className="ds-hint" style={{ marginTop: 10 }}>
                  Авторизуйтесь кнопкой Telegram выше. Простое открытие бота не выполняет вход на сайт.
                </p>
              )}
              <button type="button" onClick={handleTelegramLogin}
                className="ds-btn ds-btn--ghost ds-btn--sm" style={{ marginTop: 8 }}>
                <Send size={16} /> Открыть бота
              </button>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '16px 0' }}>
              <div style={{ flex: 1, height: 1, background: 'var(--s-line)' }} />
              <span className="ds-sub">или</span>
              <div style={{ flex: 1, height: 1, background: 'var(--s-line)' }} />
            </div>

            <div className="ds-field">
              <label className="ds-label">Номер телефона</label>
              <div style={{ position: 'relative' }}>
                <Phone
                  size={18}
                  style={{ position: 'absolute', left: 13, top: '50%', transform: 'translateY(-50%)', color: 'var(--s-muted)' }}
                />
                <input
                  type="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="+7 (___) ___-__-__"
                  className="ds-input"
                  style={{ paddingLeft: 40 }}
                />
              </div>
            </div>

            <button
              onClick={handleSendCode}
              disabled={loading || !phone}
              className="ds-btn ds-btn--primary ds-btn--block"
            >
              {loading ? 'Отправляем...' : 'Получить код'}
            </button>

            {error && <p className="ds-error" style={{ justifyContent: 'center', marginTop: 10 }}>{error}</p>}

            <p className="ds-hint" style={{ textAlign: 'center', marginTop: 14 }}>
              Код придёт через @Sochiservice_bot, только если этот номер уже привязан к вашему аккаунту.
              При отсутствии кода используйте прежний подтверждённый email или кнопку Telegram выше, если она открылась.
            </p>
          </div>
        )}

        {mode === 'code' && (
          <div>
            <div style={{ textAlign: 'center', marginBottom: 20 }}>
              {codeSentVia === 'telegram' ? (
                <p className="ds-sub">
                  Код отправлен в <span style={{ fontWeight: 600, color: '#0088cc' }}>Telegram</span> на аккаунт с номером{' '}
                  <span style={{ fontWeight: 600, color: 'var(--s-text)' }}>{phone}</span>
                </p>
              ) : (
                <p className="ds-sub">
                  Не удалось отправить код в Telegram. Убедитесь, что вы зарегистрированы через бот{' '}
                  <a href="https://t.me/Sochiservice_bot" target="_blank" rel="noopener noreferrer" style={{ color: '#0088cc', fontWeight: 600 }}>@Sochiservice_bot</a>
                </p>
              )}
            </div>

            <div style={{ display: 'flex', justifyContent: 'center', gap: 10, marginBottom: 20 }}>
              {code.map((digit, i) => (
                <input
                  key={i}
                  ref={(el) => { codeRefs.current[i] = el }}
                  type="text"
                  inputMode="numeric"
                  maxLength={1}
                  value={digit}
                  onChange={(e) => handleCodeInput(i, e.target.value)}
                  onKeyDown={(e) => handleCodeKeyDown(i, e)}
                  className="ds-input"
                  style={{
                    width: 48,
                    height: 48,
                    padding: 0,
                    textAlign: 'center',
                    fontSize: 22,
                    fontWeight: 700,
                  }}
                />
              ))}
            </div>

            {loading && <p className="ds-sub" style={{ textAlign: 'center' }}>Проверяем...</p>}

            {error && <p className="ds-error" style={{ justifyContent: 'center' }}>{error}</p>}

            <button
              onClick={() => {
                setMode('phone')
                setCode(['', '', '', '', '', ''])
              }}
              className="ds-btn ds-btn--block"
              style={{ background: 'none', border: 'none', color: 'var(--s-accent)' }}
            >
              Изменить номер
            </button>
          </div>
        )}
      </div>
    </ClientShell>
  )
}
