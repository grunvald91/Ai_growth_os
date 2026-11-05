import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { ArrowLeft, ClipboardList, Home, LayoutDashboard, MessageCircle, Ticket, User } from 'lucide-react'
import { Logo } from '../../components/Logo'
import './booking-flow.css'

// Общий каркас клиентских экранов ServGo Air (главная, страница бизнеса, запись):
// белое «устройство», шапка с логотипом и кабинетом; контент сверху,
// управление — внизу у большого пальца.

// ── Иллюстрация обложки, если у бизнеса нет фото ──
export function HeroArt() {
  return (
    <svg viewBox="0 0 900 485" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <defs>
        <linearGradient id="bfSky" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#eef5fd" />
          <stop offset="0.55" stopColor="#dce9f7" />
          <stop offset="1" stopColor="#c3d8ef" />
        </linearGradient>
        <radialGradient id="bfGlow" cx="0.72" cy="0.3" r="0.6">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0.95" />
          <stop offset="1" stopColor="#ffffff" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="900" height="485" fill="url(#bfSky)" />
      <rect width="900" height="485" fill="url(#bfGlow)" />
      <circle cx="640" cy="120" r="150" fill="none" stroke="#f8fdff" strokeOpacity="0.7" strokeWidth="18" />
      <path d="M330 485V210a120 120 0 0 1 240 0v275" fill="#ffffff" fillOpacity="0.45" stroke="#fafcff" strokeOpacity="0.8" strokeWidth="22" />
      <rect x="360" y="370" width="180" height="46" rx="23" fill="#5f88b0" />
      <rect x="378" y="404" width="144" height="16" rx="8" fill="#4a7399" />
      <ellipse cx="450" cy="470" rx="320" ry="40" fill="#ffffff" fillOpacity="0.5" />
    </svg>
  )
}

const TABS = [
  { to: '/', label: 'Главная', Icon: Home },
  { to: '/my', label: 'Записи', Icon: ClipboardList },
  { to: '/subscriptions', label: 'Абонементы', Icon: Ticket },
  { to: '/chat', label: 'Чат', Icon: MessageCircle },
]

export function AirFrame({
  label,
  backTo,
  tabs = false,
  topExtra,
  overlay,
  children,
}: {
  label: string
  backTo?: string
  tabs?: boolean
  topExtra?: ReactNode
  overlay?: ReactNode
  children: ReactNode
}) {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const loggedIn = !!localStorage.getItem('token')
  const role = localStorage.getItem('userRole')
  const isAdmin = role === 'admin' || role === 'owner'
  const profileHref = loggedIn ? '/account' : '/login'

  return createPortal(
    <div className="bf-root">
      <main className="bf-device" aria-label={label}>
        <header className="bf-topbar">
          {backTo ? (
            <button type="button" className="bf-user" onClick={() => navigate(backTo)} aria-label="Назад"><ArrowLeft /></button>
          ) : null}
          <Link to="/" className="bf-brand" aria-label="ServGo — на главную"><Logo size={28} /></Link>
          {tabs && (
            <nav className="bf-topnav" aria-label="Разделы">
              {TABS.map(({ to, label: tabLabel }) => (
                <Link key={to} to={to} className={`bf-topnav-link${pathname === to ? ' active' : ''}`} aria-current={pathname === to ? 'page' : undefined}>
                  {tabLabel}
                </Link>
              ))}
            </nav>
          )}
          <div className="bf-spacer" />
          {topExtra}
          {isAdmin && (
            <Link to="/admin" className="bf-top-link"><LayoutDashboard />Кабинет</Link>
          )}
          <Link to={profileHref} className="bf-user" title={loggedIn ? 'Профиль' : 'Войти'} aria-label={loggedIn ? 'Профиль' : 'Войти'}><User /></Link>
        </header>
        {children}
        {tabs && (
          <nav className="bf-tabbar" aria-label="Разделы">
            {TABS.map(({ to, label: tabLabel, Icon }) => (
              <Link key={to} to={to} className={`bf-tab${pathname === to ? ' active' : ''}`} aria-current={pathname === to ? 'page' : undefined}>
                <Icon />
                <span>{tabLabel}</span>
              </Link>
            ))}
          </nav>
        )}
      </main>
      {overlay}
    </div>,
    document.body
  )
}
