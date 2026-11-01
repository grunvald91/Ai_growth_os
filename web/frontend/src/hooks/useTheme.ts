import { useCallback, useEffect, useState } from 'react'
import {
  SERVGO_THEME_EVENT,
  resolveServgoTheme,
  setServgoThemePreference,
  type ResolvedServgoTheme,
} from '../utils/servgoTheme'

/** Backward-compatible two-state toggle for existing admin widgets.
 * The global preference is light/dark/system and is shared with the client shell.
 */
export function useTheme(): [ResolvedServgoTheme, () => void] {
  const [theme, setTheme] = useState<ResolvedServgoTheme>(resolveServgoTheme)
  useEffect(() => {
    const sync = () => setTheme(resolveServgoTheme())
    window.addEventListener(SERVGO_THEME_EVENT, sync)
    return () => window.removeEventListener(SERVGO_THEME_EVENT, sync)
  }, [])
  const toggle = useCallback(() => {
    setServgoThemePreference(resolveServgoTheme() === 'dark' ? 'light' : 'dark')
  }, [])
  return [theme, toggle]
}
