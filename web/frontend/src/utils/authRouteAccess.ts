/** Protected routes wait for in-flight cookie/Telegram restoration instead of
 * redirecting at the public page's three-second display deadline. */
export type RouteAccess = 'allow' | 'wait' | 'login'

export function routeAccess(
  token: string | null,
  role: string | null,
  adminOnly: boolean,
  restorationPending: boolean,
  explicitlyLoggedOut: boolean,
): RouteAccess {
  if (explicitlyLoggedOut) return 'login'
  if (token && (!adminOnly || role === 'admin' || role === 'owner')) return 'allow'
  return restorationPending ? 'wait' : 'login'
}
