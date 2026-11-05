import type { ReactNode } from 'react'
import { AirFrame } from '../pages/client/AirFrame'
import { StudioEntry } from './StudioEntry'

/**
 * Каркас остальных клиентских экранов (записи, абонементы, статус записи,
 * вход, аккаунт, документы) — тот же ServGo Air, что у главной и записи:
 * шапка с логотипом и профилем, заголовок сверху, вкладки внизу.
 */
export function ClientShell({
  title,
  subtitle,
  backTo,
  headerExtra,
  studioEntry,
  children,
}: {
  title?: string
  subtitle?: ReactNode
  backTo?: string
  headerExtra?: ReactNode
  /** Показать вход в маркетинговую студию (если он открыт этому человеку). */
  studioEntry?: boolean
  children: ReactNode
}) {
  return (
    <AirFrame label={title || 'ServGo'} backTo={backTo} topExtra={headerExtra} tabs>
      <section className="bf-screen" aria-label={title}>
        <div className="bf-inner bf-page bf-shell-page">
          {(title || subtitle != null) && (
            <header className="bf-shell-head">
              {title && <h1 className="bf-h2">{title}</h1>}
              {subtitle != null && <div className="bf-intro">{subtitle}</div>}
            </header>
          )}
          {studioEntry && <StudioEntry />}
          {children}
        </div>
      </section>
    </AirFrame>
  )
}
