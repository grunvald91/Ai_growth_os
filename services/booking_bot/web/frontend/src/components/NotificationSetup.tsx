import { useEffect, useState } from 'react'
import { Bell, BellOff, CheckCircle2 } from 'lucide-react'
import { enableServGoPush, supportsWebPush } from '../utils/pwa'

type State = 'checking' | 'idle' | 'enabled' | 'denied' | 'unsupported' | 'error'

export function NotificationSetup({ notifyToken, staff = false }: { notifyToken?: string | null; staff?: boolean }) {
  const [state, setState] = useState<State>(() => !supportsWebPush() ? 'unsupported' : Notification.permission === 'denied' ? 'denied' : 'checking')
  const [message, setMessage] = useState('')

  useEffect(() => {
    if (!supportsWebPush() || Notification.permission === 'denied') return

    navigator.serviceWorker.ready
      .then((registration) => registration.pushManager.getSubscription())
      .then((subscription) => setState(subscription ? 'enabled' : 'idle'))
      .catch(() => setState('idle'))
  }, [])

  const enable = async () => {
    setState('checking')
    setMessage('')
    try {
      await enableServGoPush(notifyToken)
      setState('enabled')
    } catch (error) {
      const text = error instanceof Error ? error.message : 'Не удалось включить уведомления'
      setMessage(text)
      setState(Notification.permission === 'denied' ? 'denied' : 'error')
    }
  }

  if (state === 'unsupported') {
    return (
      <div className="ds-card" style={{ marginTop: 12, padding: 14 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
          <BellOff size={20} style={{ flexShrink: 0, marginTop: 1 }} />
          <div>
            <p className="ds-text" style={{ fontWeight: 700 }}>Системные уведомления недоступны</p>
            <p className="ds-sub" style={{ marginTop: 4 }}>
              Откройте ServGo в современном браузере. На iPhone Web Push работает для приложения, добавленного на экран «Домой».
            </p>
          </div>
        </div>
      </div>
    )
  }

  if (state === 'enabled') {
    return (
      <div className="ds-card" style={{ marginTop: 12, padding: 14 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <CheckCircle2 size={20} className="ds-accent" />
          <div>
            <p className="ds-text" style={{ fontWeight: 700 }}>Напоминания ServGo включены</p>
            <p className="ds-sub" style={{ marginTop: 2 }}>{staff ? 'Будем присылать уведомления о новых записях и предоплатах на проверке.' : 'Будем присылать системные уведомления о предстоящих визитах.'}</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="ds-card" style={{ marginTop: 12, padding: 14 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
        <Bell size={20} className="ds-accent" style={{ flexShrink: 0, marginTop: 1 }} />
        <div style={{ flex: 1 }}>
          <p className="ds-text" style={{ fontWeight: 700 }}>
            {state === 'denied' ? 'Уведомления запрещены' : staff ? 'Не пропустите предоплату' : 'Не пропустите запись'}
          </p>
          <p className="ds-sub" style={{ marginTop: 4 }}>
            {state === 'denied'
              ? 'Разрешите уведомления для ServGo в настройках браузера или телефона.'
              : staff ? 'Включите уведомления о записях и отправленных предоплатах. Они не зависят от Telegram.' : 'Включите системные напоминания ServGo. Они не зависят от Telegram.'}
          </p>
          {message && <p className="ds-sub" style={{ marginTop: 6, color: 'var(--s-danger)' }}>{message}</p>}
          {state !== 'denied' && (
            <button
              type="button"
              onClick={enable}
              disabled={state === 'checking'}
              className="ds-btn ds-btn--primary ds-btn--sm"
              style={{ marginTop: 10 }}
            >
              {state === 'checking' ? 'Подключаем…' : 'Включить напоминания'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
