import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { isTrustedStudioTicketUrl } from '../utils/studioTicketUrl'

/** Open only a freshly issued, server-authorized Growth SSO link.
 * On failures stay in the ServGo admin; never fall back to Growth's login.
 */
export function useOpenStudio(businessId: number | null | undefined) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => { setError('') }, [businessId])

  const open = async () => {
    if (busy) return
    if (!businessId || !Number.isSafeInteger(businessId) || businessId < 1) {
      setError('Сначала выберите бизнес в админке.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const { url } = await api.studioTicket(businessId)
      if (!isTrustedStudioTicketUrl(url)) throw new Error('Untrusted Growth SSO destination')
      window.location.assign(url)
    } catch {
      setError('Не удалось открыть маркетинг. Проверьте доступ и попробуйте снова.')
      setBusy(false)
    }
  }

  return { open, busy, error, clearError: () => setError('') }
}
