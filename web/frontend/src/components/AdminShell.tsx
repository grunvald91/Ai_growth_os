import { clearLogin } from '../authUtils'
import { OwnerMailEntry } from './OwnerMailEntry'
import { useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  Wallet, LayoutDashboard, Clock, Scissors, Users, Settings, Moon, Sun, LogOut, MessageCircle,
  MoreHorizontal, BarChart3, Repeat, Bot, Mail, Sparkles, X, ChevronRight, PlusCircle, ContactRound,
} from 'lucide-react'
import { CreateBusinessSheet } from './CreateBusinessSheet'
import { useTheme } from '../hooks/useTheme'
import { useAdminBusiness } from '../hooks/useAdminBusiness'
import { useOpenStudio } from '../hooks/useOpenStudio'

// Нижняя панель: пять пунктов, которые нужны каждый день. Всё остальное —
// в «Ещё», чтобы любой раздел был доступен с телефона.
const NAV = [
  { to: '/admin', label: 'Панель', Icon: LayoutDashboard },
  { to: '/admin/schedule', label: 'Расписание', Icon: Clock },
  { to: '/admin/prepayments', label: 'Оплаты', Icon: Wallet },
  { to: '/admin/contacts', label: 'Клиенты', Icon: ContactRound },
]

const MORE = [
  { to: '/admin/sales', label: 'Диалоги', Icon: MessageCircle },
  { to: '/admin/services', label: 'Услуги', Icon: Scissors },
  { to: '/admin/employees', label: 'Мастера', Icon: Users },
  { to: '/admin/analytics', label: 'Аналитика', Icon: BarChart3 },
  { to: '/admin/subscriptions', label: 'Абонементы', Icon: Repeat },
  { to: '/admin/sales/settings', label: 'ИИ-продавец', Icon: Bot },
  { to: '/admin/settings', label: 'Настройки', Icon: Settings },
  { to: '/mail', label: 'Почта ServGo', Icon: Mail },
]

/**
 * Единый каркас экранов админки в дизайне расписания:
 * полноэкранный ds-screen + шапка (заголовок, слот, переключатель темы) +
 * нижняя навигация ds-bottomnav. Контент скроллится между ними.
 * Тема — общий useTheme() (свет/тёмная + Telegram + localStorage).
 */
export function AdminShell({
  title,
  subtitle,
  headerExtra,
  pageClassName,
  children,
}: {
  title: string
  subtitle?: ReactNode
  headerExtra?: ReactNode
  /** Доп. класс страницы: на десктопе позволяет собрать свою сетку вместо телефонной колонки. */
  pageClassName?: string
  children: ReactNode
}) {
  const [theme, toggleTheme] = useTheme()
  const navigate = useNavigate()

  const handleLogout = async () => {
    sessionStorage.setItem('servgoExplicitLogout', '1')
    try { await clearLogin() }
    catch { alert('Не удалось удалить офлайн-данные. Закройте другие вкладки ServGo и повторите выход.'); return }
    navigate('/login', { replace: true })
  }
  return createPortal(
    <div className="ds-screen" data-theme={theme}>
      {/* На телефоне в шапке только название и выбор бизнеса: почта, тема и
          выход живут в «Ещё», чтобы ничего не обрезалось. */}
      <header className="ds-shell-header" style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', columnGap: 10, padding: '5px 16px', flexShrink: 0 }}>
        <h1 className="ds-h1 ds-shell-title">{title}</h1>
        <div className="ds-shell-header-extra">{headerExtra}</div>
        <div className="ds-shell-header-desktop">
          <OwnerMailEntry />
          <button className="ds-icon-btn bordered" onClick={toggleTheme} aria-label="Тема">
            {theme === 'dark' ? <Sun size={19} /> : <Moon size={19} />}
          </button>
          <button className="ds-icon-btn bordered" onClick={handleLogout} aria-label="Выйти" title="Выйти">
            <LogOut size={18} />
          </button>
        </div>
        {subtitle != null && <div className="ds-sub" style={{ flexBasis: '100%', marginTop: 2 }}>{subtitle}</div>}
      </header>

      <div className="ds-shell-content" style={{ flex: 1, minHeight: 0, overflowY: 'auto', WebkitOverflowScrolling: 'touch', padding: '0 16px 16px' }}>
        {/* Мобильная раскладка «информация сверху — управление снизу» (см. .ds-page) */}
        <div className={`ds-page${pageClassName ? ` ${pageClassName}` : ''}`}>{children}</div>
      </div>

      <AdminBottomNav theme={theme} toggleTheme={toggleTheme} onLogout={handleLogout} />
    </div>,
    document.body
  )
}


/** Нижняя навигация админки: четыре раздела и «Ещё» со всеми остальными. */
export function AdminBottomNav({ theme, toggleTheme, onLogout }: {
  theme: 'light' | 'dark'
  toggleTheme: () => void
  onLogout: () => void
}) {
  const { pathname } = useLocation()
  const [moreOpen, setMoreOpen] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const moreActive = MORE.some(item => item.to === pathname)
  const handleLogout = onLogout
  const { selectedId } = useAdminBusiness()
  const { open: openStudio, busy: studioBusy } = useOpenStudio(selectedId)
  return (
    <>
        <nav className="ds-bottomnav">
          {NAV.map(({ to, label, Icon }) => (
            <Link key={to} to={to} className={`ds-bn${pathname === to ? ' active' : ''}`}>
              <Icon size={21} />
              <span>{label}</span>
            </Link>
          ))}
          {/* Десктоп: все разделы сразу в боковом меню, без «Ещё» — любой экран в один клик. */}
          <div className="ds-bn-group ds-desk-only">Управление</div>
          {MORE.map(({ to, label, Icon }) => (
            <Link key={to} to={to} className={`ds-bn ds-desk-only${pathname === to ? ' active' : ''}`}>
              <Icon size={21} />
              <span>{label}</span>
            </Link>
          ))}
          <button type="button" className="ds-bn ds-desk-only" onClick={openStudio} disabled={studioBusy}>
            <Sparkles size={21} />
            <span>AI Growth OS</span>
          </button>
          <button type="button" className="ds-bn ds-desk-only" onClick={() => setCreateOpen(true)}>
            <PlusCircle size={21} />
            <span>Новый бизнес</span>
          </button>
          <button type="button" className={`ds-bn ds-mobile-only${moreActive || moreOpen ? ' active' : ''}`}
            onClick={() => setMoreOpen(true)} aria-haspopup="dialog" aria-expanded={moreOpen}>
            <MoreHorizontal size={21} />
            <span>Ещё</span>
          </button>
        </nav>
  
        {moreOpen && (
          <div className="ds-sheet-ov" onClick={() => setMoreOpen(false)}>
            <div className="ds-sheet" role="dialog" aria-label="Все разделы" onClick={e => e.stopPropagation()}>
              <div className="ds-sheet-grip" />
              <div className="ds-sheet-head">
                <h3>Все разделы</h3>
                <button className="ds-icon-btn" onClick={() => setMoreOpen(false)} aria-label="Закрыть"><X size={18} /></button>
              </div>
              <nav className="ds-more-list">
                {MORE.map(({ to, label, Icon }) => (
                  <Link key={to} to={to} onClick={() => setMoreOpen(false)} className={`ds-more-item${pathname === to ? ' active' : ''}`}>
                    <Icon size={20} /><span>{label}</span><ChevronRight size={17} className="ds-more-chev" />
                  </Link>
                ))}
                <button type="button" className="ds-more-item" onClick={openStudio} disabled={studioBusy}>
                  <Sparkles size={20} /><span>Маркетинг — AI Growth OS</span><ChevronRight size={17} className="ds-more-chev" />
                </button>
                <button type="button" className="ds-more-item" onClick={() => { setMoreOpen(false); setCreateOpen(true) }}>
                  <PlusCircle size={20} /><span>Новый бизнес</span><ChevronRight size={17} className="ds-more-chev" />
                </button>
                <button type="button" className="ds-more-item" onClick={toggleTheme}>
                  {theme === 'dark' ? <Sun size={20} /> : <Moon size={20} />}
                  <span>{theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'}</span>
                </button>
                <button type="button" className="ds-more-item danger" onClick={handleLogout}>
                  <LogOut size={20} /><span>Выйти</span>
                </button>
              </nav>
            </div>
          </div>
        )}
        {createOpen && <CreateBusinessSheet onClose={() => setCreateOpen(false)} />}
    </>
  )
}
