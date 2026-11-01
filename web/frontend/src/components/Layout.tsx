import { clearLogin } from '../authUtils'
import { OwnerMailEntry } from './OwnerMailEntry'
import { ThemeControl } from './ThemeControl'
import { useState, useEffect, useRef } from 'react'
import { Outlet, Link, useLocation } from 'react-router-dom'
import {
  ClipboardList,
  User,
  LayoutDashboard,
  Clock,
  Scissors,
  Users,
  BarChart3,
  LogOut,
  Home,
  Ticket,
  MessageCircle,
  Settings,
  ExternalLink,
} from 'lucide-react'
import { Logo } from './Logo'
import { PwaInstallPrompt } from './PwaInstallPrompt'

const clientNav = [
  { to: '/', label: 'Главная', icon: Home },
  { to: '/my', label: 'Мои записи', icon: ClipboardList },
  { to: '/subscriptions', label: 'Абонементы', icon: Ticket },
  { to: '/chat', label: 'Чат', icon: MessageCircle },
]

const adminNav = [
  { to: '/admin', label: 'Панель', icon: LayoutDashboard },
  { to: '/admin/schedule', label: 'Расписание', icon: Clock },
  { to: '/admin/prepayments', label: 'Предоплаты', icon: Clock },
  { to: '/admin/services', label: 'Услуги', icon: Scissors },
  { to: '/admin/employees', label: 'Сотрудники', icon: Users },
  { to: '/admin/subscriptions', label: 'Абонементы', icon: Ticket },
  { to: '/admin/analytics', label: 'Аналитика', icon: BarChart3 },
  { to: '/admin/sales', label: 'Диалоги ИИ', icon: MessageCircle },
  { to: '/admin/settings', label: 'Настройки', icon: Settings },
]

const mobileBottomNav = [
  { to: '/', label: 'Главная', icon: Home },
  { to: '/my', label: 'Записи', icon: ClipboardList },
  { to: '/subscriptions', label: 'Абонементы', icon: Ticket },
  { to: '/chat', label: 'Чат', icon: MessageCircle },
  { to: '/login', label: 'Профиль', icon: User, authAware: true },
]

const adminBottomNav = [
  { to: '/admin', label: 'Панель', icon: LayoutDashboard },
  { to: '/admin/schedule', label: 'Расписание', icon: Clock },
  { to: '/admin/services', label: 'Услуги', icon: Scissors },
  { to: '/admin/employees', label: 'Мастера', icon: Users },
  { to: '/admin/settings', label: 'Настройки', icon: Settings },
]

export function Layout() {
  const location = useLocation()
  const isAdmin = location.pathname.startsWith('/admin')
  const token = localStorage.getItem('token')
  const role = localStorage.getItem('userRole')
  const isLoggedIn = !!token
  const isAdminUser = role === 'admin' || role === 'owner'
  const [profileOpen, setProfileOpen] = useState(false)
  const profilePanelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (profilePanelRef.current && !profilePanelRef.current.contains(e.target as Node)) {
        setProfileOpen(false)
      }
    }
    if (profileOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [profileOpen])

  const handleLogout = async () => {
    sessionStorage.setItem('servgoExplicitLogout', '1')
    try { await clearLogin() }
    catch { alert('Не удалось удалить офлайн-данные. Закройте другие вкладки ServGo и повторите выход.'); return }
    window.location.href = '/'
  }

  return (
    <div className="min-h-screen bg-apple-bg">
      {/* Header */}
      <header className="servgo-shell-header sticky top-0 z-50 backdrop-blur-xl border-b shadow-sm">
        <div className="max-w-6xl mx-auto px-4 sm:px-6">
          <div className="flex items-center justify-between h-14">
            {/* Logo */}
            <Link to="/" className="flex items-center">
              <Logo size={28} />
            </Link>

            {/* Desktop nav */}
            <nav className="hidden md:flex items-center gap-1">
              {isAdmin && isAdminUser ? (
                <>
                  {adminNav.map((item) => {
                    const Icon = item.icon
                    const active = location.pathname === item.to
                    return (
                      <Link
                        key={item.to}
                        to={item.to}
                        className={`flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium transition-all duration-200 ${
                          active
                            ? 'bg-apple-blue/10 text-apple-blue'
                            : 'text-apple-gray hover:text-apple-text hover:bg-apple-bg'
                        }`}
                      >
                        <Icon size={16} />
                        {item.label}
                      </Link>
                    )
                  })}
                  <div className="w-px h-6 bg-apple-border mx-2" />
                  <Link
                    to="/"
                    className="text-sm text-apple-gray hover:text-apple-text px-3 py-2 rounded-xl transition-all duration-200"
                  >
                    На сайт
                  </Link>
                </>
              ) : (
                <>
                  {clientNav.map((item) => {
                    const Icon = item.icon
                    const active = location.pathname === item.to
                    return (
                      <Link
                        key={item.to}
                        to={item.to}
                        className={`flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium transition-all duration-200 ${
                          active
                            ? 'bg-apple-blue/10 text-apple-blue'
                            : 'text-apple-gray hover:text-apple-text hover:bg-apple-bg'
                        }`}
                      >
                        <Icon size={16} />
                        {item.label}
                      </Link>
                    )
                  })}
                </>
              )}

              <ThemeControl />
              <OwnerMailEntry />
              {isLoggedIn ? (
                <div className="flex items-center gap-2 ml-2">
                  {isAdminUser && !isAdmin && (
                    <Link
                      to="/admin"
                      className="text-sm text-apple-blue hover:text-apple-blue-hover px-3 py-2 rounded-xl transition-all duration-200"
                    >
                      Админ
                    </Link>
                  )}
                  <button
                    onClick={handleLogout}
                    className="flex items-center gap-1 text-sm text-apple-gray hover:text-apple-danger px-3 py-2 rounded-xl transition-all duration-200"
                  >
                    <LogOut size={16} />
                    Выйти
                  </button>
                </div>
              ) : (
                <Link
                  to="/login"
                  className="flex items-center gap-2 bg-apple-blue text-white text-sm font-semibold px-5 py-2.5 rounded-full hover:bg-apple-blue-hover transition-all duration-200 ml-3 shadow-sm"
                >
                  <User size={16} />
                  Войти
                </Link>
              )}
            </nav>

            {/* Mobile: admin exit + client login */}
            <div className="md:hidden flex items-center gap-2">
              <ThemeControl />
              {isAdmin && isAdminUser && (
                <Link
                  to="/"
                  className="flex items-center gap-1 text-xs text-apple-blue font-medium px-2.5 py-1.5 rounded-lg bg-apple-blue/10 active:bg-apple-blue/20 transition-colors"
                >
                  <ExternalLink size={12} />
                  На сайт
                </Link>
              )}
              {!isAdmin && !isLoggedIn && (
                <Link
                  to="/login"
                  className="flex items-center gap-1 bg-apple-blue text-white text-xs font-semibold px-3 py-1.5 rounded-full"
                >
                  <User size={12} />
                  Войти
                </Link>
              )}
            </div>
          </div>
        </div>

      </header>

      {/* Main content */}
      <main className="max-w-6xl mx-auto px-4 sm:px-6 py-6 pb-24 md:pb-6 animate-fade-in">
        <Outlet />
      </main>

      {!isAdmin && <PwaInstallPrompt />}

      {/* Mobile bottom tab bar (client + admin) */}
      <div className="md:hidden fixed bottom-0 left-0 right-0 z-50">
        {/* Profile panel — client only */}
        {!isAdmin && profileOpen && (
          <div
            ref={profilePanelRef}
            className="servgo-shell-popover absolute bottom-full right-3 mb-2 w-56 rounded-2xl shadow-lg border overflow-hidden animate-fade-in"
          >
            <div className="px-4 py-3 border-b border-apple-border/20">
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 rounded-full bg-apple-blue/10 flex items-center justify-center">
                  <User size={18} className="text-apple-blue" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-apple-text truncate">
                    {localStorage.getItem('userName') || 'Пользователь'}
                  </p>
                </div>
              </div>
            </div>
              <Link to="/account" onClick={() => setProfileOpen(false)} className="ds-row" style={{ padding: '10px', display: 'block' }}>Почта и Telegram</Link>
            {isAdminUser && (
              <Link
                to="/admin"
                onClick={() => setProfileOpen(false)}
                className="flex items-center gap-3 px-4 py-3 text-sm text-apple-text hover:bg-apple-bg active:bg-apple-bg transition-colors duration-150"
              >
                <LayoutDashboard size={18} className="text-apple-gray" />
                Админ-панель
              </Link>
            )}
            <button
              onClick={() => {
                setProfileOpen(false)
                handleLogout()
              }}
              className="flex items-center gap-3 px-4 py-3 text-sm text-apple-danger hover:bg-red-50 active:bg-red-50 transition-colors duration-150 w-full"
            >
              <LogOut size={18} />
              Выйти
            </button>
          </div>
        )}

        <nav className="servgo-shell-bottom backdrop-blur-xl border-t shadow-[0_-1px_3px_rgba(0,0,0,0.05)]">
          <div className="flex items-center justify-around py-1.5 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
            {(isAdmin ? adminBottomNav : mobileBottomNav).map((item) => {
              const Icon = item.icon

              if (!isAdmin && 'authAware' in item && item.authAware && isLoggedIn) {
                return (
                  <button
                    key={item.label}
                    onClick={() => setProfileOpen(!profileOpen)}
                    className={`flex flex-col items-center gap-0.5 min-w-[56px] min-h-[44px] justify-center px-2 py-1 text-[10px] font-medium transition-all duration-200 rounded-xl active:scale-95 ${
                      profileOpen ? 'text-apple-blue' : 'text-apple-gray'
                    }`}
                  >
                    <Icon size={20} strokeWidth={profileOpen ? 2.5 : 2} />
                    Профиль
                  </button>
                )
              }

              const active = location.pathname === item.to
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  onClick={() => setProfileOpen(false)}
                  className={`flex flex-col items-center gap-0.5 min-w-[56px] min-h-[44px] justify-center px-2 py-1 text-[10px] font-medium transition-all duration-200 rounded-xl active:scale-95 ${
                    active ? 'text-apple-blue' : 'text-apple-gray'
                  }`}
                >
                  <Icon size={20} strokeWidth={active ? 2.5 : 2} />
                  {item.label}
                </Link>
              )
            })}
          </div>
        </nav>
      </div>
    </div>
  )
}
