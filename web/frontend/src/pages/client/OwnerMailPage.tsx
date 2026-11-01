import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ClientShell } from '../../components/ClientShell'

type State = 'checking' | 'login' | 'denied' | 'error'

/** Server-confirmed entry to ServGo mail. Never trust userRole/localStorage for privileges. */
export function OwnerMailPage() {
  const [state, setState] = useState<State>(() => (localStorage.getItem('token') ? 'checking' : 'login'))

  useEffect(() => {
    const token = localStorage.getItem('token')
    if (!token) return
    let active = true
    fetch('/api/owner-mail/entry', {
      method: 'POST',
      cache: 'no-store',
      headers: { Authorization: 'Bearer ' + token },
    })
      .then(async (response) => {
        if (!active) return
        if (response.status === 401) { setState('login'); return }
        if (response.status === 403) { setState('denied'); return }
        if (!response.ok) { setState('error'); return }
        const data: { url?: string } = await response.json()
        const valid = data.url && (
          data.url.startsWith('https://mail.servgo.ru/claim?ticket=') ||
          data.url.startsWith('https://servgo.ru/api/owner-mail/claim?ticket=')
        )
        if (!data.url || !valid) setState('error')
        else window.location.assign(data.url)
      })
      .catch(() => { if (active) setState('error') })
    return () => { active = false }
  }, [])

  return (
    <ClientShell title="Почта" backTo="/admin">
      <div className="ds-form-narrow" style={{ paddingTop: 20 }}>
        <div className="ds-card" style={{ padding: 16 }}>
          <h2 className="ds-h3">Почта ServGo</h2>
          {state === 'checking' && <p role="status">Открываем почту…</p>}
          {state === 'login' && (
            <>
              <p>Войдите в ServGo своим логином и паролем, затем снова нажмите «Почта».</p>
              <Link to="/login" className="ds-btn" style={{ display: 'inline-block', marginTop: 12 }}>Войти</Link>
            </>
          )}
          {state === 'denied' && <p>У этого аккаунта пока нет доступа к почте.</p>}
          {state === 'error' && <p>Не удалось открыть почту. Попробуйте ещё раз через минуту.</p>}
        </div>
      </div>
    </ClientShell>
  )
}
