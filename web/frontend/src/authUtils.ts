import { clearOfflineSnapshots } from './utils/offlineStore'
import { api, type AuthResponse } from './api/client'

export function errorMessage(error: unknown) {
  const message = error instanceof Error ? error.message : 'Не удалось выполнить действие'
  try {
    const detail = JSON.parse(message).detail
    return typeof detail === 'string' ? detail : 'Проверьте введённые данные'
  } catch { return message }
}

export async function clearLogin() {
  // Persist the logout tombstone across tabs BEFORE removing the JWT or
  // awaiting a network request. A fresh tab must never restore a cookie login.
  localStorage.setItem('servgoExplicitLogout', '1')
  sessionStorage.setItem('servgoExplicitLogout', '1')
  localStorage.removeItem('token')
  localStorage.removeItem('userRole')
  localStorage.removeItem('userName')
  localStorage.removeItem('adminBusinessId')
  sessionStorage.removeItem('servgoLoginNext')
  window.dispatchEvent(new Event('servgo-logout'))
  // The server endpoint deletes the HttpOnly shared-session cookie; local
  // logout still works offline, while the tombstone blocks silent re-login.
  // The canonical PWA also owns the Growth cookies under /studio.
  // Clear both server sessions before navigation; a failure never resurrects
  // the locally logged-out account.
  const completed = await Promise.allSettled([
    api.logout(),
    fetch('/studio/session/logout', { method: 'POST', credentials: 'same-origin' }).then(response => {
      if (!response.ok) throw new Error('Studio logout HTTP ' + response.status)
    }),
  ])
  for (const entry of completed) {
    if (entry.status === 'rejected') console.warn('Remote logout unavailable; local logout remains active', entry.reason)
  }
  // Callers handling explicit logout must await deletion before navigation.
  await clearOfflineSnapshots()
}

export function saveLogin(res: AuthResponse) {
  const previousToken = localStorage.getItem('token')
  localStorage.setItem('token', res.token)
  if (previousToken !== res.token) window.dispatchEvent(new Event('servgo-account-changed'))
  localStorage.setItem('userRole', res.role)
  localStorage.setItem('userName', res.user.full_name)
  sessionStorage.removeItem('servgoExplicitLogout')
  localStorage.removeItem('servgoExplicitLogout')
}
