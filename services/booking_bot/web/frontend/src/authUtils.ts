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
  // Finish clearing local/offline data even if remote logout failed.
  await clearOfflineSnapshots()
  // The ServGo identity cookie must be deleted. A failed platform logout may
  // leave both the central session and its bound Studio session alive.
  if (completed[0].status === 'rejected') {
    throw new Error('ServGo server logout did not complete')
  }
}

export function saveLogin(res: AuthResponse) {
  const previousToken = localStorage.getItem('token')
  localStorage.setItem('token', res.token)
  if (previousToken !== res.token) window.dispatchEvent(new Event('servgo-account-changed'))
  localStorage.setItem('userRole', res.role)
  localStorage.setItem('userName', res.user.full_name)
  window.dispatchEvent(new Event('servgo-login-saved'))
  sessionStorage.removeItem('servgoExplicitLogout')
  localStorage.removeItem('servgoExplicitLogout')
}
