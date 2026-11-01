/** ServGo Air theme contract shared by the BookingBot PWA modules.
 * Storage is same-origin only. Growth/Mail subdomains sync after migration to one origin.
 */
export type ServgoThemePreference = 'light' | 'dark' | 'system'
export type ResolvedServgoTheme = 'light' | 'dark'

export const SERVGO_THEME_KEY = 'servgo-theme'
export const SERVGO_THEME_EVENT = 'servgo-themechange'
const LEGACY_KEYS = ['schedTheme', 'servgo-desktop-theme'] as const
let initialized = false

function valid(value: string | null): value is ServgoThemePreference {
  return value === 'light' || value === 'dark' || value === 'system'
}

function stored(key: string): string | null {
  try { return window.localStorage.getItem(key) } catch { return null }
}

export function getServgoThemePreference(): ServgoThemePreference {
  const selected = stored(SERVGO_THEME_KEY)
  if (valid(selected)) return selected
  for (const key of LEGACY_KEYS) {
    const old = stored(key)
    if (old === 'light' || old === 'dark') return old
  }
  return 'system'
}

export function resolveServgoTheme(preference: ServgoThemePreference = getServgoThemePreference()): ResolvedServgoTheme {
  if (preference !== 'system') return preference
  const telegram = (window as Window & { Telegram?: { WebApp?: { colorScheme?: string } } }).Telegram?.WebApp?.colorScheme
  if (telegram === 'light' || telegram === 'dark') return telegram
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function applyServgoTheme(preference: ServgoThemePreference = getServgoThemePreference()) {
  const resolved = resolveServgoTheme(preference)
  const root = document.documentElement
  root.dataset.theme = resolved
  root.dataset.themePreference = preference
  root.style.colorScheme = resolved
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')
  if (meta) meta.content = resolved === 'dark' ? '#111a29' : '#ffffff'
  window.dispatchEvent(new CustomEvent(SERVGO_THEME_EVENT, { detail: { preference, resolved } }))
  return resolved
}

export function setServgoThemePreference(preference: ServgoThemePreference) {
  if (!valid(preference)) return
  try { window.localStorage.setItem(SERVGO_THEME_KEY, preference) } catch { /* private browsing */ }
  applyServgoTheme(preference)
}

export function initializeServgoTheme() {
  if (initialized) return
  initialized = true
  const preference = getServgoThemePreference()
  // One-time compatibility migration, without overwriting explicit new preferences.
  if (stored(SERVGO_THEME_KEY) === null) {
    const old = LEGACY_KEYS.map(stored).find(value => value === 'light' || value === 'dark')
    if (old) {
      try { window.localStorage.setItem(SERVGO_THEME_KEY, old) } catch { /* private browsing */ }
    }
  }
  applyServgoTheme(preference)
  window.addEventListener('storage', event => {
    if (event.key === SERVGO_THEME_KEY || LEGACY_KEYS.some(key => key === event.key)) {
      applyServgoTheme()
    }
  })
  const systemQuery = window.matchMedia?.('(prefers-color-scheme: dark)')
  systemQuery?.addEventListener?.('change', () => {
    if (getServgoThemePreference() === 'system') applyServgoTheme('system')
  })
  const telegram = (window as Window & { Telegram?: { WebApp?: { onEvent?: (name: string, listener: () => void) => void } } }).Telegram?.WebApp
  telegram?.onEvent?.('themeChanged', () => {
    if (getServgoThemePreference() === 'system') applyServgoTheme('system')
  })
}
