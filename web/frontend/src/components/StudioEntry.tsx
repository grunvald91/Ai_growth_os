import { useCallback, useEffect, useRef, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { api } from '../api/client'
import { isTrustedStudioTicketUrl } from '../utils/studioTicketUrl'

type Entry = { id: number; name: string; role: string }

/**
 * Вход в маркетинговую студию (AI Growth OS) для тех, у кого он открыт: владельцев и администраторов,
 * которым владелец включил доступ. Ничего не показывает, пока доступа нет или вход не настроен.
 */
export function StudioEntry({ showUnavailable = false, autoOpen = false }: { showUnavailable?: boolean; autoOpen?: boolean } = {}) {
  const [items, setItems] = useState<Entry[]>([])
  const [busy, setBusy] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const autoOpened = useRef(false)
  useEffect(() => {
    let active = true
    if (!localStorage.getItem('token')) { setLoading(false); return }
    api.studioBusinesses()
      .then(result => { if (active) setItems(result) })
      .catch(() => { if (active) setItems([]) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])
  const open = useCallback(async (id: number) => {
    if (busy !== null) return
    setBusy(id); setError('')
    try {
      const { url } = await api.studioTicket(id)
      if (!isTrustedStudioTicketUrl(url)) throw new Error('Unexpected Studio SSO destination')
      window.location.assign(url)
    } catch {
      setBusy(null)
      setError('Не удалось открыть маркетинг. Попробуйте ещё раз.')
    }
  }, [busy])

  useEffect(() => {
    if (!autoOpen || loading || items.length !== 1 || autoOpened.current) return
    autoOpened.current = true
    void open(items[0].id)
  }, [autoOpen, loading, items, open])

  if (items.length === 0) return showUnavailable ? (
    <p role="status" className="ds-caption">
      {loading ? 'Проверяем доступ к маркетингу…' : 'Пока нет доступных бизнесов или вход в маркетинг не настроен.'}
    </p>
  ) : null
  return (
    <div className="bf-account-actions" style={{ marginTop: 0, marginBottom: 14 }}>
      {items.map((item) => (
        <button key={item.id} type="button" className="bf-account-row" disabled={busy !== null} onClick={() => open(item.id)}>
          <Sparkles />
          <span>{items.length > 1 ? `Маркетинг — ${item.name}` : 'Маркетинг — AI Growth OS'}</span>
        </button>
      ))}
      {error && <p role="alert" className="ds-caption">{error}</p>}
    </div>
  )
}
