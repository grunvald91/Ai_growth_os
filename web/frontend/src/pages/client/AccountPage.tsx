import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { CalendarDays, LayoutDashboard, LogOut } from 'lucide-react'
import { api } from '../../api/client'
import { EmailAuthForm } from '../../components/EmailAuthForm'
import { clearLogin, errorMessage } from '../../authUtils'
import { TelegramAccountLink } from '../../components/TelegramAccountLink'
import { ClientShell } from '../../components/ClientShell'
import { StudioEntry } from '../../components/StudioEntry'
import { YandexAccountLink } from '../../components/YandexAccountLink'

export function AccountPage() {
  const [account, setAccount] = useState<Awaited<ReturnType<typeof api.emailAccount>> | null>(null)
  const [error, setError] = useState('')
  const [isMaster, setIsMaster] = useState(false)
  useEffect(() => { api.masterMe().then(r => setIsMaster(r.employees.length > 0)).catch(() => setIsMaster(false)) }, [])
  useEffect(() => { api.emailAccount().then(setAccount).catch(e => setError(errorMessage(e))) }, [])
  const role = localStorage.getItem('userRole')
  const isAdmin = role === 'admin' || role === 'owner'
  const logout = async () => {
    sessionStorage.setItem('servgoExplicitLogout', '1')
    try { await clearLogin() }
    catch { alert('Не удалось удалить офлайн-данные. Закройте другие вкладки ServGo и повторите выход.'); return }
    window.location.href = '/'
  }
  return <ClientShell title="Профиль" subtitle={localStorage.getItem('userName') || undefined}
    backTo={role === 'client' ? '/my' : '/admin/settings'}>
    <div className="ds-form-narrow">
      {error && <p role="alert">{error}</p>}
      {account && (account.email ? <div className="bf-account-card">
        <span className="bf-kicker">Email подтверждён</span>
        <strong>{account.email}</strong>
        <p>Можно входить по email и паролю без Telegram.</p>
        <Link to="/login">Забыли пароль? Восстановить</Link>
      </div> : <><p>Привяжите подтверждённую почту к текущему аккаунту — ваши бизнесы и права сохранятся.</p><EmailAuthForm link /></>)}
      {account?.telegram ? <div className="bf-account-card"><span className="bf-kicker">Telegram привязан</span><strong>{account.telegram.username ? `@${account.telegram.username}` : account.telegram.id}</strong></div> : account?.email && (account.telegram_link_available ? <TelegramAccountLink /> : <p className="bf-intro">Привязка Telegram пока не настроена администратором платформы.</p>)}
      <YandexAccountLink />
      <div className="bf-account-actions">
        <StudioEntry />
        {isMaster && <Link to="/master" className="bf-account-row"><CalendarDays /><span>Моё расписание мастера</span></Link>}
        {isAdmin && <Link to="/admin" className="bf-account-row"><LayoutDashboard /><span>Кабинет бизнеса</span></Link>}
        <button type="button" className="bf-account-row danger" onClick={logout}><LogOut /><span>Выйти</span></button>
      </div>
    </div>
  </ClientShell>
}
