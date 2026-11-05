import { useEffect, useState } from 'react'
import { api } from '../api/client'

// Кнопка входа через Яндекс ID: обычная ссылка, вход идёт через наш сервер (/api/auth/yandex/start).
export function YandexLoginButton() {
  const [available, setAvailable] = useState(false)
  useEffect(() => { api.yandexCapabilities().then(r => setAvailable(r.available)).catch(() => setAvailable(false)) }, [])
  if (!available) return null
  return (
    <>
    <a href="/api/auth/yandex/start" className="ds-btn ds-btn--primary"
       style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, width: '100%', minHeight: 48, textDecoration: 'none', background: '#000', color: '#fff' }}>
      <span aria-hidden="true" style={{ width: 24, height: 24, borderRadius: '50%', background: '#fc3f1d', color: '#fff', display: 'grid', placeItems: 'center', fontWeight: 700, fontSize: 15 }}>Я</span>
      Войти через Яндекс
    </a>
    <p className="ds-hint" style={{ marginTop: 8 }}>
      Яндекс передаст ServGo имя, почту, телефон и дату рождения, если вы это разрешите. Пароль от Яндекса мы не получаем.
    </p>
    </>
  )
}
