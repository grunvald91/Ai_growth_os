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
  window.dispatchEvent(new Event('servgo-logout'))
  // The server endpoint deletes the HttpOnly shared-session cookie; local
  // logout still works offline, while the tombstone blocks silent re-login.
  try { await api.logout() }
  catch (error) { console.warn('Could not clear the shared session cookie; automatic restoration is blocked locally', error) }
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
