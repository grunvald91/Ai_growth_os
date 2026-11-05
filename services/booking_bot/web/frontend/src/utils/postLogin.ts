import { api } from '../api/client'

/** Only this known internal destination may survive an external OAuth redirect.
 * Never accept arbitrary URLs or arbitrary local paths from a login query.
 */
export const STUDIO_ENTRY = '/workspace/studio-entry'
const NEXT_KEY = 'servgoLoginNext'
const NEXT_TTL_MS = 10 * 60 * 1000

export function rememberLoginDestination(next: string | null): void {
  if (next !== STUDIO_ENTRY) return
  sessionStorage.setItem(NEXT_KEY, JSON.stringify({ path: next, until: Date.now() + NEXT_TTL_MS }))
}

export function pendingLoginDestination(): string | null {
  try {
    const raw = sessionStorage.getItem(NEXT_KEY)
    if (!raw) return null
    const data = JSON.parse(raw) as { path?: unknown; until?: unknown }
    if (data.path === STUDIO_ENTRY && typeof data.until === 'number' && data.until > Date.now()) {
      return STUDIO_ENTRY
    }
  } catch {
    // Corrupted or forged session storage is ignored, never used as a URL.
  }
  sessionStorage.removeItem(NEXT_KEY)
  return null
}

export function consumeLoginDestination(): string | null {
  const result = pendingLoginDestination()
  sessionStorage.removeItem(NEXT_KEY)
  return result
}

/**
 * Where to send a user after Telegram, email or Yandex login.
 * A direct Studio sign-in only changes the destination, never the identity.
 */
export async function postLoginPath(role: string): Promise<string> {
  const requested = consumeLoginDestination()
  if (requested) return requested
  if (role !== 'client') return '/admin'
  try {
    const me = await api.masterMe()
    if (me.employees.length > 0) return '/master'
  } catch {
    // Нет кабинета мастера или сервер недоступен: обычный вход клиента.
  }
  return '/my'
}
