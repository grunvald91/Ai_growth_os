import { useEffect, useRef, useState } from 'react'
import { Check, Monitor, Moon, Sun } from 'lucide-react'
import {
  getServgoThemePreference,
  setServgoThemePreference,
  SERVGO_THEME_EVENT,
  type ServgoThemePreference,
} from '../utils/servgoTheme'

const choices = [
  { value: 'light', label: 'Светлая', icon: Sun },
  { value: 'dark', label: 'Тёмная', icon: Moon },
  { value: 'system', label: 'Как в системе', icon: Monitor },
] as const

/** Same preference for clients, owner admin and (once co-hosted) Growth/Mail. */
export function ThemeControl() {
  const [mode, setMode] = useState<ServgoThemePreference>(getServgoThemePreference)
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const update = () => setMode(getServgoThemePreference())
    window.addEventListener(SERVGO_THEME_EVENT, update)
    return () => window.removeEventListener(SERVGO_THEME_EVENT, update)
  }, [])

  useEffect(() => {
    if (!open) return
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const CurrentIcon = choices.find(item => item.value === mode)?.icon || Monitor
  return (
    <div className="servgo-theme-control" ref={rootRef}>
      <button
        type="button"
        className="servgo-theme-trigger"
        aria-label="Настройка темы оформления"
        aria-haspopup="true"
        aria-expanded={open}
        title="Тема оформления"
        onClick={() => setOpen(previous => !previous)}
      >
        <CurrentIcon size={19} strokeWidth={1.8} aria-hidden="true" />
      </button>
      {open && (
        <div className="servgo-theme-menu" role="group" aria-label="Тема оформления">
          <p className="servgo-theme-menu-title">Оформление ServGo</p>
          {choices.map(item => {
            const Icon = item.icon
            return (
              <button key={item.value} type="button"
                className="servgo-theme-option"
                aria-pressed={mode === item.value}
                onClick={() => {
                  setServgoThemePreference(item.value)
                  setMode(item.value)
                  setOpen(false)
                }}>
                <Icon size={17} aria-hidden="true" />
                <span>{item.label}</span>
                {mode === item.value && <Check size={16} aria-hidden="true" />}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
