import { lazy, Suspense, useState, useEffect } from 'react'
import { Routes, Route, Navigate, useSearchParams, useLocation, useNavigate } from 'react-router-dom'
import { api, ApiError } from './api/client'
import { clearLogin, saveLogin } from './authUtils'
import { Layout } from './components/Layout'
import { OfflineNotice } from './components/OfflineNotice'
import { cleanupExpiredOfflineSnapshots } from './utils/offlineStore'
import { HomePage } from './pages/client/HomePage'
import { BusinessPage } from './pages/client/BusinessPage'
import { BusinessBlogPage } from './pages/client/BusinessBlogPage'
import { BookingStatusPage } from './pages/client/BookingStatusPage'
import { MyBookingsPage } from './pages/client/MyBookingsPage'
import { SubscriptionsPage } from './pages/client/SubscriptionsPage'
import { LoginPage } from './pages/client/LoginPage'
import { YandexLoginPage } from './pages/client/YandexLoginPage'
import { OwnerMailPage } from './pages/client/OwnerMailPage'
import { AccountPage } from './pages/client/AccountPage'
import { PrepaymentsPage } from './pages/admin/PrepaymentsPage'
import { AdminPaymentAttention } from './components/AdminPaymentAttention'
import { AdminDashboard } from './pages/admin/AdminDashboard'
import { ServicesPage } from './pages/admin/ServicesPage'
import { EmployeesPage } from './pages/admin/EmployeesPage'
import { AnalyticsPage } from './pages/admin/AnalyticsPage'
import { ContactsPage } from './pages/admin/ContactsPage'
import { SubscriptionsAdminPage } from './pages/admin/SubscriptionsAdminPage'
import { ChatPage } from './pages/client/ChatPage'
import { NotFoundPage } from './pages/client/NotFoundPage'
import { SalesInboxPage } from './pages/admin/SalesInboxPage'
import { SalesSettingsPage } from './pages/admin/SalesSettingsPage'
import { RentalBookingStatusPage } from './pages/client/RentalBookingStatusPage'
import { LegalPage } from './pages/client/LegalPage'
import { applySeo } from './utils/seo'
import { postLoginPath } from './utils/postLogin'

const BookingPage = lazy(() => import('./pages/client/BookingPage').then(module => ({ default: module.BookingPage })))
const MasterPage = lazy(() => import('./pages/master/MasterPage').then(module => ({ default: module.MasterPage })))
const SchedulePage = lazy(() => import('./pages/admin/SchedulePage').then(module => ({ default: module.SchedulePage })))
const SettingsPage = lazy(() => import('./pages/admin/SettingsPage').then(module => ({ default: module.SettingsPage })))

/** Redirect /book?business_id=X → /book/X, or /book → / */
function BookRedirect() {
  const [params] = useSearchParams()
  const bizId = params.get('business_id')
  if (bizId) return <Navigate to={`/book/${bizId}`} replace />
  return <Navigate to="/" replace />
}

/** Redirect /schedule, /analytics, /calendar → admin routes */
function LegacyAdminRedirect({ to }: { to: string }) {
  return <Navigate to={to} replace />
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem('token')
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

function AdminRoute({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem('token')
  const role = localStorage.getItem('userRole')
  if (!token || (role !== 'admin' && role !== 'owner')) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

// Мета-теги по маршруту. Страница бизнеса ставит свои сама, когда загрузит данные.
function RouteSeo() {
  const { pathname } = useLocation()
  useEffect(() => {
    if (pathname.startsWith('/business/')) return
    // В поиск идут только главная и страницы бизнесов; всё остальное — служебное или личное.
    applySeo({ path: pathname, noindex: pathname !== '/' })
  }, [pathname])
  return null
}

function App() {
  const [authReady, setAuthReady] = useState(false)
  // Enforce the approved seven-day retention when the PWA is next opened.
  useEffect(() => { void cleanupExpiredOfflineSnapshots().catch(console.warn) }, [])

  // localStorage changes are broadcast to other tabs by the browser. Full
  // navigation clears React's in-memory bookings, modal state and cached roles.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== 'token' || event.oldValue === event.newValue) return
      // Another tab logged out or switched users; never keep showing its data.
      window.location.reload()
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])
  const navigate = useNavigate()

  useEffect(() => {
    let active = true
    async function restoreAuth() {
      try {
        if (sessionStorage.getItem('servgoExplicitLogout') === '1' ||
            localStorage.getItem('servgoExplicitLogout') === '1') return
        const initData = window.Telegram?.WebApp?.initData
        if (initData) {
          try {
            const login = await api.telegramAuth(initData)
            if (!active || localStorage.getItem('servgoExplicitLogout') === '1') return
            saveLogin(login)
            // Мастера, открывшего сайт из Telegram, один раз за сеанс ведём сразу в его расписание.
            if (window.location.pathname === '/' && !sessionStorage.getItem('servgoMasterRedirect')) {
              sessionStorage.setItem('servgoMasterRedirect', '1')
              const path = await postLoginPath(login.role)
              if (path === '/master') navigate(path, { replace: true })
            }
            return
          } catch (error) {
            console.warn('Telegram auto-login failed:', error)
          }
        }
        if (localStorage.getItem('token')) {
          try {
            await api.getMe()
            return
          } catch (error) {
            // Preserve the session during temporary network/server outages.
            if (!(error instanceof ApiError) || error.status !== 401) return
            await clearLogin().catch(error => console.warn('Offline cleanup after expired login:', error))
          }
        }
        // Existing browser cookie from an authenticated Telegram session.
        // Never use it after the user explicitly logged out.
        try {
          const session = await api.sharedSession()
          if (!active || localStorage.getItem('servgoExplicitLogout') === '1' ||
              sessionStorage.getItem('servgoExplicitLogout') === '1') return
          saveLogin(session)
        } catch {
          // Not every visitor has a shared Telegram session.
        }
      } finally {
        if (active) setAuthReady(true)
      }
    }
    // Проверка входа не должна держать публичные страницы (главную со списком мест): если она
    // зависла, через пару секунд показываем сайт как есть, а вход доделается в фоне.
    // В Telegram-приложении не торопимся: там вход приходит из самого Telegram, и без него закрытые
    // страницы (например, «Записи») отправили бы человека на страницу входа.
    const inTelegram = Boolean(window.Telegram?.WebApp?.initData)
    const patience = inTelegram ? 0 : window.setTimeout(() => { if (active) setAuthReady(true) }, 3000)
    void restoreAuth()
    return () => { active = false; window.clearTimeout(patience) }
  }, [navigate])

  if (!authReady) {
    return <div role="status" style={{ padding: 24 }}>Проверяем вход…</div>
  }

  return (
    <><OfflineNotice /><RouteSeo /><AdminPaymentAttention /><Suspense fallback={<div role="status" className="flex min-h-[30vh] items-center justify-center">Загрузка…</div>}><Routes>
      <Route element={<Layout />}>
        {/* Client routes */}
        <Route path="/" element={<HomePage />} />
        <Route path="/business/:id" element={<BusinessPage />} />
        <Route path="/business/:id/blog" element={<BusinessBlogPage />} />
        <Route path="/business/:id/blog/:slug" element={<BusinessBlogPage />} />
        <Route path="/book/:businessId" element={<BookingPage />} />
        <Route path="/b/:token" element={<BookingStatusPage />} />
        <Route path="/rental/b/:token" element={<RentalBookingStatusPage />} />
        <Route
          path="/my"
          element={
            <ProtectedRoute>
              <MyBookingsPage />
            </ProtectedRoute>
          }
        />
        <Route path="/subscriptions" element={<SubscriptionsPage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/legal/:docType/:businessId" element={<LegalPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/login/yandex" element={<YandexLoginPage />} />
        <Route path="/mail" element={<OwnerMailPage />} />
        <Route path="/workspace/mail" element={<OwnerMailPage />} />
        <Route path="/account" element={<ProtectedRoute><AccountPage /></ProtectedRoute>} />
        <Route path="/master" element={<ProtectedRoute><MasterPage /></ProtectedRoute>} />
        <Route path="/admin/prepayments" element={<AdminRoute><PrepaymentsPage /></AdminRoute>} />

        {/* Legacy redirects */}
        <Route path="/book" element={<BookRedirect />} />
        <Route path="/schedule" element={<LegacyAdminRedirect to="/admin/schedule" />} />
        <Route path="/analytics" element={<LegacyAdminRedirect to="/admin/analytics" />} />
        <Route path="/calendar" element={<LegacyAdminRedirect to="/admin/schedule" />} />

        {/* Admin routes */}
        <Route path="/admin/sales" element={<AdminRoute><SalesInboxPage /></AdminRoute>} />
        <Route path="/admin/sales/settings" element={<AdminRoute><SalesSettingsPage /></AdminRoute>} />
        <Route
          path="/admin"
          element={
            <AdminRoute>
              <AdminDashboard />
            </AdminRoute>
          }
        />
        <Route
          path="/admin/schedule"
          element={
            <AdminRoute>
              <SchedulePage />
            </AdminRoute>
          }
        />
        <Route
          path="/admin/services"
          element={
            <AdminRoute>
              <ServicesPage />
            </AdminRoute>
          }
        />
        <Route
          path="/admin/employees"
          element={
            <AdminRoute>
              <EmployeesPage />
            </AdminRoute>
          }
        />
        <Route path="/admin/contacts" element={<AdminRoute><ContactsPage /></AdminRoute>} />
        <Route
          path="/admin/analytics"
          element={
            <AdminRoute>
              <AnalyticsPage />
            </AdminRoute>
          }
        />
        <Route
          path="/admin/settings"
          element={
            <AdminRoute>
              <SettingsPage />
            </AdminRoute>
          }
        />
        <Route
          path="/admin/subscriptions"
          element={
            <AdminRoute>
              <SubscriptionsAdminPage />
            </AdminRoute>
          }
        />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes></Suspense></>
  )
}

export default App
