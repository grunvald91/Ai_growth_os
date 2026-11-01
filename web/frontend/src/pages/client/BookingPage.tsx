import { useState, useEffect, useMemo, useRef, lazy, Suspense, Component, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useParams, useSearchParams, useNavigate, Link } from 'react-router-dom'
import {
  Calendar,
  Clock,
  Check,
  ChevronLeft,
  ChevronRight,
  ArrowLeft,
  ArrowRight,
  Sparkles,
  User,
  Users,
  UserCheck,
  DoorOpen,
  Package,
  CreditCard,
  Paperclip,
  Plus,
  Minus,
  Info,
  X,
  Bell,
  Send,
  Phone,
  MessageCircle,
} from 'lucide-react'
import {
  api,
  type Service,
  type EmployeeWithPricing,
  type BusinessDetail,
  type CalculatePriceResponse,
} from '../../api/client'
import { getTelegramUser } from '../../utils/telegram'
import { downloadBookingIcs } from '../../utils/calendar'
import { NotificationSetup } from '../../components/NotificationSetup'
import { Logo } from '../../components/Logo'
import { ApiError } from '../../api/client'
import { HeroArt } from './AirFrame'
import { photoSrc, photoThumbSrc } from './airPhotos'
import { ACCUSATIVE, choosePhrase } from '../../utils/bookingLabels'
import { resolveBookingEntry, resourceSupportsService, getAvailableResources, getAvailableServices, type BookingFlowOrder } from './bookingEntry'
import { withSeoRef } from '../../utils/seoReferral'
import './booking-flow.css'

// ── Helpers ──

function formatPrice(price?: number | null, isHourly?: boolean) {
  if (price == null) return '—'
  const formatted = new Intl.NumberFormat('ru-RU').format(price)
  return isHourly ? `${formatted} ₽/ч` : `${formatted} ₽`
}

function cleanPaymentDetails(value?: string | null) {
  if (!value) return ''
  return value
    .replace(/(?:^|\s*)чек\s+в\s+telegram[-\s]?канале\.?/gi, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

function formatDuration(minutes?: number | null) {
  if (!minutes || minutes <= 0) return ''
  if (minutes < 60) return `${minutes} мин`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m ? `${h} ч ${m} мин` : `${h} ч`
}

function getRoomPriceRange(room: EmployeeWithPricing): string {
  if (!room.time_pricing || room.time_pricing.length === 0) return ''
  const prices = room.time_pricing.map((tp) => tp.price_per_hour)
  const min = Math.min(...prices)
  const max = Math.max(...prices)
  if (min === max) return formatPrice(min, true)
  return `${new Intl.NumberFormat('ru-RU').format(min)}–${new Intl.NumberFormat('ru-RU').format(max)} ₽/ч`
}

function parsePhotos(raw?: string | null): string[] {
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed)) return parsed.filter((v): v is string => typeof v === 'string')
  } catch {
    if (typeof raw === 'string' && raw.length > 0) return [raw]
  }
  return []
}

function resourcePhotos(resource: EmployeeWithPricing): string[] {
  return Array.from(
    new Set(
      [resource.primary_photo, ...parsePhotos(resource.photos), resource.photo_url]
        .filter((photo): photo is string => typeof photo === 'string' && photo.trim().length > 0)
    )
  )
}

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join('')
}

function linkifyText(text: string): ReactNode {
  const urlPattern = /(https?:\/\/[^\s]+|(?:vk\.com|t\.me)\/[^\s]+)/gi
  const parts = text.split(urlPattern)
  if (parts.length === 1) return text
  return parts.map((part, i) => {
    if (/^(https?:\/\/|vk\.com\/|t\.me\/)/i.test(part)) {
      const href = part.startsWith('http') ? part : `https://${part}`
      return (
        <a key={i} href={href} target="_blank" rel="noopener noreferrer">
          {part}
        </a>
      )
    }
    return part
  })
}

function dateKey(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function dateFromKey(key: string) {
  const [y, m, d] = key.split('-').map(Number)
  return new Date(y, m - 1, d)
}

function ruDate(key: string) {
  return dateFromKey(key).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })
}

function ruDateLong(key: string) {
  const s = dateFromKey(key).toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' })
  return s.charAt(0).toUpperCase() + s.slice(1)
}

function plural(n: number, one: string, few: string, many: string) {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few
  return many
}

// ── Error boundaries ──

// Защита от белого экрана: если в итоговом экране что-то падает,
// клиент должен увидеть хоть какое-то сообщение, а не пустоту.
class SuccessErrorBoundary extends Component<{ children: ReactNode; bookingId?: number | null; notifyToken?: string | null }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }
  static getDerivedStateFromError(error: Error) { return { error } }
  componentDidCatch(error: Error) {
    try { console.error('Booking success render crashed:', error) } catch { /* noop */ }
  }
  render() {
    if (this.state.error) {
      const publicUrl = this.props.notifyToken
        ? `${window.location.origin}/b/${this.props.notifyToken}`
        : null
      return (
        <div className="bf-fallback">
          <div className="bf-orb"><Check /></div>
          <h2 className="bf-h2">Запись создана</h2>
          <p className="bf-intro">
            Бронь сохранена{this.props.bookingId ? ` (№${this.props.bookingId})` : ''}. Подробности и статус доступны по ссылке.
          </p>
          {publicUrl && <a href={publicUrl} className="bf-primary" style={{ flex: 'none', width: '100%' }}>Открыть страницу брони</a>}
          <Link to="/" className="bf-text-alt">На главную</Link>
        </div>
      )
    }
    return this.props.children
  }
}

// ── Info sheet («Подробнее») ──

type InfoModalData = {
  title: string
  description?: string
  details?: string[]
  photos?: string[]
}

function InfoSheet({ data, onClose }: { data: InfoModalData; onClose: () => void }) {
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return (
    <div className="bf-sheet-ov" role="presentation" onClick={onClose}>
      <div className="bf-sheet" role="dialog" aria-modal="true" aria-label={data.title} onClick={(e) => e.stopPropagation()}>
        <div className="bf-sheet-grip" />
        <div className="bf-sheet-head">
          <h3>{data.title}</h3>
          <button type="button" className="bf-sheet-close" onClick={onClose} aria-label="Закрыть"><X size={17} /></button>
        </div>
        {data.photos && data.photos.length > 0 && (
          <div className="bf-gallery" aria-label={`Фотографии: ${data.title}`}>
            {data.photos.map((photo, i) => (
              <a key={`${photo}-${i}`} href={photoSrc(photo)} target="_blank" rel="noopener noreferrer" aria-label={`Открыть фото ${i + 1} из ${data.photos!.length}`}>
                <img src={photoSrc(photo)} alt={`${data.title} — фото ${i + 1}`} loading={i === 0 ? 'eager' : 'lazy'} />
              </a>
            ))}
          </div>
        )}
        <p>{data.description?.trim() ? linkifyText(data.description) : 'Подробное описание пока не добавлено.'}</p>
        {data.details && data.details.filter(Boolean).length > 0 && (
          <div className="details">
            {data.details.filter(Boolean).map((detail, i) => <div key={i}>{detail}</div>)}
          </div>
        )}
      </div>
    </div>
  )
}

// Чат-помощник подгружается только когда нужен (на компьютере — окошком поверх записи).
const ChatThread = lazy(() => import('./ChatPage').then((m) => ({ default: m.SalesThread })))

// «Компьютер» = ширина от 1024px: там вместо перехода на страницу чата открывается небольшое окошко.
function useIsDesktop() {
  const query = '(min-width: 1024px)'
  const [matches, setMatches] = useState(() => typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia(query).matches)
  useEffect(() => {
    if (!window.matchMedia) return
    const mql = window.matchMedia(query)
    const onChange = () => setMatches(mql.matches)
    onChange()
    // Старый Safari (до 14) знает только addListener.
    if (mql.addEventListener) mql.addEventListener('change', onChange)
    else mql.addListener(onChange)
    return () => {
      if (mql.removeEventListener) mql.removeEventListener('change', onChange)
      else mql.removeListener(onChange)
    }
  }, [])
  return matches
}

function prefersReducedMotion() {
  try { return typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches } catch { return false }
}

// Лёгкая вибрация-отклик на телефоне (где поддерживается; при «уменьшении движения» — выключена).
function haptic(pattern: number | number[]) {
  try {
    if (!prefersReducedMotion() && typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function') navigator.vibrate(pattern)
  } catch { /* noop */ }
}

// Цена, которая «досчитывается» до нового значения (fromZero — при первом показе считает с нуля).
function CountPrice({ value, fromZero = false }: { value: number; fromZero?: boolean }) {
  const [shown, setShown] = useState(() => (fromZero && !prefersReducedMotion() ? 0 : value))
  const shownRef = useRef(shown)
  useEffect(() => {
    const from = shownRef.current
    if (from === value) return
    const dur = prefersReducedMotion() ? 0 : Math.min(900, 420 + Math.abs(value - from) / 40)
    const t0 = performance.now()
    let raf = 0
    const tick = (now: number) => {
      const p = dur > 0 ? Math.min(1, (now - t0) / dur) : 1
      const v = p >= 1 ? value : Math.round(from + (value - from) * (1 - Math.pow(1 - p, 3)))
      shownRef.current = v
      setShown(v)
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [value])
  return <span className="bf-count">{formatPrice(shown)}</span>
}

// Порядок экранов — чтобы понимать, куда идёт переход: вперёд (вход справа) или назад (слева).
const SCREEN_RANK: Record<string, number> = { home: 0, services: 1, resource: 2, calendar: 3, extras: 4, review: 5, prepay: 6, done: 7 }

// Галочка, которая «рисуется» (штрих анимируется стилями; без анимации — просто галочка).
function DrawnCheck() {
  return (
    <svg className="bf-drawn-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 12.5l4.6 4.6L19 7.4" pathLength={1} />
    </svg>
  )
}

// Праздничное конфетти: детерминированный разброс (без Math.random), чтобы рендер был чистым.
const CONFETTI_COLORS = ['#2f6fd6', '#4e93ea', '#75d5f1', '#f4b740', '#ef7d57', '#7ecb9b']
function Confetti() {
  const pieces = Array.from({ length: 34 }, (_, i) => {
    const r = (n: number) => { const x = Math.sin(i * 12.9898 + n * 78.233) * 43758.5453; return x - Math.floor(x) }
    return {
      left: `${(i / 34) * 100 + (r(1) - 0.5) * 6}%`,
      delay: `${(r(2) * 0.9).toFixed(2)}s`,
      dur: `${(2.2 + r(3) * 1.6).toFixed(2)}s`,
      drift: `${Math.round((r(4) - 0.5) * 120)}px`,
      spin: `${Math.round(360 + r(5) * 540)}deg`,
      color: CONFETTI_COLORS[i % CONFETTI_COLORS.length],
      round: i % 3 === 0,
      w: 6 + Math.round(r(6) * 5),
    }
  })
  return (
    <div className="bf-confetti" aria-hidden="true">
      {pieces.map((p, i) => (
        <span key={i} className={p.round ? 'is-round' : ''} style={{ left: p.left, width: p.w, height: p.round ? p.w : p.w * 1.6, background: p.color, animationDelay: p.delay, animationDuration: p.dur, ['--drift' as string]: p.drift, ['--spin' as string]: p.spin }} />
      ))}
    </div>
  )
}

type Screen = 'home' | 'services' | 'resource' | 'calendar' | 'extras' | 'review' | 'prepay' | 'done'

// ── Main Component ──

function BookingPageInner() {
  const { businessId } = useParams<{ businessId: string }>()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()

  const preselectedServiceId = searchParams.get('service') ? Number(searchParams.get('service')) : null
  const preselectedEmployeeId = searchParams.get('employee') ? Number(searchParams.get('employee')) : null
  const seoRefCandidate = searchParams.get('seo_ref')
  const seoRef = seoRefCandidate && /^[A-Za-z0-9_.-]{1,512}$/.test(seoRefCandidate)
    ? seoRefCandidate : undefined

  // Business data
  const [business, setBusiness] = useState<BusinessDetail | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [services, setServices] = useState<Service[]>([])
  const [additionalServices, setAdditionalServices] = useState<Service[]>([])
  const [employees, setEmployees] = useState<EmployeeWithPricing[]>([])
  const [infoModal, setInfoModal] = useState<InfoModalData | null>(null)
  const isDesktop = useIsDesktop()
  const [chatOpen, setChatOpen] = useState(false)
  const [chatEverOpened, setChatEverOpened] = useState(false)
  const openChat = () => { setChatOpen(true); setChatEverOpened(true) }
  useEffect(() => {
    if (!chatOpen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setChatOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [chatOpen])

  // Navigation
  const [screen, setScreen] = useState<Screen>('home')
  const [entryFlowOrder, setEntryFlowOrder] = useState<BookingFlowOrder | null>(null)
  // Двойной «пульс» при успешной записи
  useEffect(() => { if (screen === 'done') haptic([18, 60, 30]) }, [screen])
  const historyRef = useRef<Screen[]>([])
  const [track, setTrack] = useState<{ screen: Screen; prev: Screen }>({ screen: 'home', prev: 'home' })
  if (track.screen !== screen) setTrack({ screen, prev: track.screen })
  const direction = (SCREEN_RANK[track.screen] ?? 0) >= (SCREEN_RANK[track.prev] ?? 0) ? 'fwd' : 'back'
  const submittingRef = useRef(false)

  // Selections
  const [selectedServices, setSelectedServices] = useState<Service[]>([])
  const [selectedResource, setSelectedResource] = useState<EmployeeWithPricing | null>(null)
  const [anyResource, setAnyResource] = useState(false)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [selectedTime, setSelectedTime] = useState<string | null>(null)
  const [extrasSeen, setExtrasSeen] = useState(false)
  const [isSoloPricing, setIsSoloPricing] = useState(false)
  const [selectedAddons, setSelectedAddons] = useState<Set<number>>(new Set())
  const [addonHours, setAddonHours] = useState<Record<number, number>>({})
  const [month, setMonth] = useState(() => {
    const t = new Date()
    return new Date(t.getFullYear(), t.getMonth(), 1)
  })

  // Price
  const [priceData, setPriceData] = useState<CalculatePriceResponse | null>(null)
  const [priceLoading, setPriceLoading] = useState(false)

  // Slots
  const [slots, setSlots] = useState<string[]>([])
  const [loadingSlots, setLoadingSlots] = useState(false)

  // Form
  const tgUser = getTelegramUser()
  const [clientName, setClientName] = useState(
    tgUser ? [tgUser.first_name, tgUser.last_name].filter(Boolean).join(' ') : ''
  )
  const [clientPhone, setClientPhone] = useState(() => localStorage.getItem('clientPhone') || '')
  const [clientEmail, setClientEmail] = useState(() => localStorage.getItem('clientEmail') || '')
  const [comment, setComment] = useState('')
  const [moreOpen, setMoreOpen] = useState(false)
  const [personalDataConsent, setPersonalDataConsent] = useState(false)
  const [bookingTermsAccepted, setBookingTermsAccepted] = useState(false)
  const [repeatEnabled, setRepeatEnabled] = useState(false)
  const [repeatWeeks, setRepeatWeeks] = useState(4)
  const [createdDates, setCreatedDates] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [bookingError, setBookingError] = useState<string | null>(null)
  const [createdBookingId, setCreatedBookingId] = useState<number | null>(null)
  const [createdNotifyToken, setCreatedNotifyToken] = useState<string | null>(null)
  const [createdPrepayment, setCreatedPrepayment] = useState<number | null>(null)
  const [paymentSent, setPaymentSent] = useState(false)
  const [receiptUploading, setReceiptUploading] = useState(false)
  const [receiptUploaded, setReceiptUploaded] = useState(false)
  const receiptInputRef = useRef<HTMLInputElement>(null)
  const [pendingReceipt, setPendingReceipt] = useState<File | null>(null)
  const [pendingReceiptPreview, setPendingReceiptPreview] = useState<string | null>(null)
  const [receiptError, setReceiptError] = useState<string | null>(null)
  const [paymentError, setPaymentError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<{ name?: boolean; phone?: boolean; email?: boolean }>({})
  const nameInputRef = useRef<HTMLInputElement>(null)
  const phoneInputRef = useRef<HTMLInputElement>(null)
  const emailInputRef = useRef<HTMLInputElement>(null)

  // Toast
  const [toast, setToast] = useState<string | null>(null)
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 2200)
    return () => clearTimeout(t)
  }, [toast])

  // Auto-fill phone (и префилл +7 9 если ничего не нашлось)
  useEffect(() => {
    if (clientPhone) return
    const token = localStorage.getItem('token')
    const setPrefillFallback = () => setClientPhone((v) => v || '+7 9')
    if (token) {
      api.getMe().then((user) => {
        if (user?.phone) {
          setClientPhone(user.phone)
          localStorage.setItem('clientPhone', user.phone)
        } else {
          setPrefillFallback()
        }
        if (user?.email) {
          setClientEmail((current) => current || user.email || '')
          localStorage.setItem('clientEmail', user.email)
        }
      }).catch(() => setPrefillFallback())
    } else {
      setPrefillFallback()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const isVenue = business?.business_type === 'venue_rental'
  const multiService = isVenue || !!business?.allow_multiple_services
  const configuredFlowOrder = business?.booking_flow_order === 'resource_first' ? 'resource_first' : 'service_first'
  const flowOrder = entryFlowOrder ?? configuredFlowOrder
  const hasResources = employees.length > 0
  // Политика обязательности мастера принадлежит бизнесу и не зависит от входной ссылки.
  const resourceOptional = !isVenue && configuredFlowOrder === 'service_first'
  const labels = useMemo(() => ({
    service: business?.booking_labels?.service?.trim() || 'Услуга',
    resource: business?.booking_labels?.resource?.trim() || (isVenue ? 'Зал' : 'Мастер'),
    additional: business?.booking_labels?.additional_services?.trim() || 'Доп. услуги',
    date: business?.booking_labels?.date?.trim() || 'Дата',
    time: business?.booking_labels?.time?.trim() || 'Время',
  }), [business?.booking_labels, isVenue])

  const availableServices = useMemo(() =>
    getAvailableServices(services, selectedResource, employees, flowOrder),
  [services, selectedResource, employees, flowOrder])

  const availableResources = useMemo(() =>
    getAvailableResources(selectedServices, employees, flowOrder),
  [selectedServices, employees, flowOrder])

  // «Любой специалист» имеет смысл, только если из кого выбирать: когда выбранная услуга
  // привязана ровно к одному мастеру, выбор «любого» не предлагаем.
  const anyAllowedFor = (picked: Service[]) => {
    if (!resourceOptional) return false
    const suitable = picked.length === 0
      ? employees.length
      : employees.filter((resource) => picked.every((service) => resourceSupportsService(resource, service.id, employees))).length
    return suitable !== 1
  }
  const anyAllowed = anyAllowedFor(selectedServices)
  useEffect(() => {
    if (anyResource && !anyAllowed) setAnyResource(false)
  }, [anyResource, anyAllowed])

  // Load business data. Ссылка с ?service= или ?employee= — уже сделанный выбор,
  // поэтому открываем следующий незаполненный шаг, а не повторный экран маршрута.
  useEffect(() => {
    let cancelled = false
    const bizId = Number(businessId)
    // Invalidate the previous visit BEFORE the network request resolves:
    // switching ?service=, ?employee= or business must not leave old details
    // actionable while the next company's catalogue is still loading.
    setBusiness(null)
    setLoadError(false)
    setServices([])
    setAdditionalServices([])
    setEmployees([])
    setEntryFlowOrder(null)
    setSelectedServices([])
    setSelectedResource(null)
    setAnyResource(false)
    setSelectedDate(null)
    setSelectedTime(null)
    setSlots([])
    setSlotsKey('')
    setPriceData(null)
    setExtrasSeen(false)
    setIsSoloPricing(false)
    setSelectedAddons(new Set())
    setAddonHours({})
    const now = new Date()
    setMonth(new Date(now.getFullYear(), now.getMonth(), 1))
    setBookingError(null)
    setFieldErrors({})
    setRepeatEnabled(false)
    setCreatedBookingId(null)
    setCreatedNotifyToken(null)
    setCreatedPrepayment(null)
    setCreatedDates([])
    setPaymentSent(false)
    setReceiptUploaded(false)
    historyRef.current = []
    setScreen('home')
    api
      .getBusiness(bizId)
      .then((biz: BusinessDetail) => {
        if (cancelled) return
        const entry = resolveBookingEntry(biz, preselectedServiceId, preselectedEmployeeId)
        setBusiness(biz)
        setServices(biz.services.filter((s: Service) => s.is_active && !s.is_additional))
        setAdditionalServices(biz.services.filter((s: Service) => s.is_active && s.is_additional))
        setEmployees(biz.employees || [])
        setEntryFlowOrder(entry.order)
        setSelectedServices(entry.services)
        setSelectedResource(entry.resource)
        setAnyResource(false)
        setScreen(entry.step)
      })
      .catch(() => { if (!cancelled) setLoadError(true) })
    return () => { cancelled = true }
  }, [businessId, preselectedServiceId, preselectedEmployeeId])

  // Total duration
  const totalDuration = useMemo(() => {
    if (selectedServices.length === 0) return 0
    // Как на сервере (visit_duration): услуга без длительности — 30 мин.
    return selectedServices.reduce((sum, s) => sum + (s.duration_minutes ?? 30), 0)
  }, [selectedServices])

  // Load slots. slotsKey привязывает список к запросу, чтобы ответ для
  // прошлого дня не подставился в новый.
  const slotsRequestKey = selectedDate && selectedServices[0]
    ? `${selectedDate}|${selectedServices[0].id}|${selectedResource?.id ?? ''}|${totalDuration}`
    : ''
  const [slotsKey, setSlotsKey] = useState('')
  useEffect(() => {
    if (!selectedDate || !businessId) return
    const serviceId = selectedServices[0]?.id ?? 0
    if (!serviceId) return
    const key = `${selectedDate}|${serviceId}|${selectedResource?.id ?? ''}|${totalDuration}`
    let stale = false
    setLoadingSlots(true)
    api
      .getSlots(Number(businessId), serviceId, selectedDate, selectedResource?.id, totalDuration)
      .then((list) => {
        if (stale) return
        setSlots(list)
        setSlotsKey(key)
        setSelectedTime((t) => (t && !list.includes(t) ? null : t))
      })
      .catch(() => { if (!stale) { setSlots([]); setSlotsKey(key) } })
      .finally(() => { if (!stale) setLoadingSlots(false) })
    return () => { stale = true }
  }, [businessId, selectedDate, selectedServices, selectedResource, totalDuration])
  const slotsReady = !loadingSlots && slotsKey === slotsRequestKey

  // Auto-pick when a step has exactly one option.
  useEffect(() => {
    if (!business || selectedServices.length > 0) return
    if (flowOrder === 'resource_first' && hasResources && !selectedResource) return
    const candidates = flowOrder === 'resource_first' ? availableServices : services
    if (candidates.length === 1) {
      setSelectedServices([candidates[0]])
      setToast(`Выбрано — ${labels.service.toLowerCase()}: ${candidates[0].name}`)
    }
  }, [business, selectedServices.length, flowOrder, hasResources, selectedResource, availableServices, services, labels.service])

  useEffect(() => {
    if (!business || selectedResource) return
    if (flowOrder === 'service_first' && selectedServices.length === 0) return
    const candidates = flowOrder === 'service_first' ? availableResources : employees
    if (candidates.length === 1) {
      setSelectedResource(candidates[0])
      setToast(`Выбрано — ${labels.resource.toLowerCase()}: ${candidates[0].name}`)
    }
  }, [business, selectedResource, flowOrder, selectedServices.length, availableResources, employees, labels.resource])

  // Единственный слот времени
  useEffect(() => {
    if (!selectedDate || selectedTime || !slotsReady || slots.length !== 1) return
    setSelectedTime(slots[0])
    setToast(`Свободно только ${slots[0]} — время выбрано`)
  }, [selectedDate, slots, selectedTime, slotsReady])

  // Calculate price for venue
  useEffect(() => {
    if (!isVenue || !selectedResource || !selectedTime || !businessId || totalDuration <= 0) {
      setPriceData(null)
      return
    }
    setPriceLoading(true)
    api
      .calculatePrice(Number(businessId), selectedResource.id, selectedTime, totalDuration, selectedDate || undefined)
      .then(setPriceData)
      .catch(() => setPriceData(null))
      .finally(() => setPriceLoading(false))
  }, [isVenue, selectedResource, selectedTime, totalDuration, businessId, selectedDate])

  // Addon total
  const addonTotal = useMemo(() => {
    let total = 0
    additionalServices.forEach((svc) => {
      if (!selectedAddons.has(svc.id)) return
      const base = svc.price ?? 0
      total += svc.is_hourly_price ? base * (addonHours[svc.id] ?? 1) : base
    })
    return total
  }, [additionalServices, selectedAddons, addonHours])

  // Total price
  const totalPrice = useMemo(() => {
    if (isVenue) {
      if (isSoloPricing && selectedResource?.solo_price_per_hour) {
        const hours = Math.max(1, totalDuration / 60)
        return selectedResource.solo_price_per_hour * hours + addonTotal
      }
      const basePrice = (priceData?.has_time_price && priceData?.total)
        ? priceData.total
        : selectedServices.reduce((sum, s) => sum + (s.price ?? 0), 0)
      return basePrice + addonTotal
    }
    return selectedServices.reduce((sum, s) => sum + (s.price ?? 0), 0) + addonTotal
  }, [isVenue, isSoloPricing, selectedResource, totalDuration, priceData, addonTotal, selectedServices])

  const hasFormatChoice = isVenue && !!selectedResource?.solo_price_per_hour
  const hasExtrasStep = additionalServices.length > 0 || hasFormatChoice
  const prepaymentNeeded = !!business?.prepayment_enabled && totalPrice > 0
  // Шаг предоплаты учитываем в счётчике заранее, чтобы «Шаг N / M» не прыгал после выбора услуги.
  const prepaymentStep = !!business?.prepayment_enabled && (totalPrice > 0 || (selectedServices.length === 0 && services.some((s) => (s.price ?? 0) > 0)))
  const prepaymentPercent = business?.prepayment_percent ?? 100
  // Оценка до создания записи (округление как на сервере); после создания — сумма с сервера.
  const estimatedPrepayment = totalPrice > 0 ? Math.floor(totalPrice * prepaymentPercent / 100) : 0
  const prepaymentAmount = createdPrepayment ?? estimatedPrepayment

  // ── Шаги ──
  const selectionSteps = useMemo<Screen[]>(() => {
    const steps: Screen[] = flowOrder === 'resource_first'
      ? [...(hasResources ? ['resource' as Screen] : []), 'services']
      : ['services', ...(hasResources ? ['resource' as Screen] : [])]
    steps.push('calendar')
    if (hasExtrasStep) steps.push('extras')
    steps.push('review')
    if (prepaymentStep) steps.push('prepay')
    return steps
  }, [flowOrder, hasResources, hasExtrasStep, prepaymentStep])

  const servicesDone = selectedServices.length > 0
  const resourceDone = !hasResources || !!selectedResource || (anyAllowed && anyResource)
  const dateDone = !!selectedDate && !!selectedTime

  function nextNeeded(): Screen {
    for (const step of selectionSteps) {
      if (step === 'services' && !servicesDone) return 'services'
      if (step === 'resource' && !resourceDone) return 'resource'
      if (step === 'calendar' && !dateDone) return 'calendar'
      if (step === 'extras' && !extrasSeen) return 'extras'
    }
    return 'review'
  }

  function go(next: Screen, record = true) {
    // Календарь и проверка требуют выбранных услуги и ресурса.
    let target = next
    if (target === 'calendar' && (!servicesDone || !resourceDone)) target = nextNeeded()
    if ((target === 'extras' || target === 'review') && (!servicesDone || !resourceDone || !dateDone)) target = nextNeeded()
    if (target === screen) return
    if (record) pushStep()
    setScreen(target)
  }

  // Каждый шаг — отдельная запись и во внутренней истории, и в истории браузера: системный «назад»
  // (Android, свайп iOS, Telegram) и кнопка «Назад» возвращают на прошлый шаг, а не уходят со страницы.
  // Обе истории должны совпадать по длине — иначе «Назад» в какой-то момент уводит со страницы записи.
  function pushStep() {
    historyRef.current.push(screen)
    window.history.pushState({ ...(window.history.state ?? {}), bfStep: historyRef.current.length }, '')
  }

  function back() {
    if (historyRef.current.length > 0) window.history.back()
    else if (screen !== 'home' && preselectedServiceId === null && preselectedEmployeeId === null) setScreen('home')
    else navigate(withSeoRef(`/business/${businessId}`, seoRef))
  }

  useEffect(() => {
    const onPop = () => {
      const prev = historyRef.current.pop()
      if (prev) setScreen(prev)
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const resetDownstream = () => {
    setSelectedDate(null)
    setSelectedTime(null)
    setSlots([])
    setPriceData(null)
    setIsSoloPricing(false)
    setExtrasSeen(false)
  }

  // После выбора на шаге — сразу дальше (решение одной рукой, как в макете V8).
  const advanceAfter = (state: { services?: Service[]; resource?: EmployeeWithPricing | null; any?: boolean; date?: boolean }) => {
    const sDone = (state.services ?? selectedServices).length > 0
    const rRes = state.resource !== undefined ? state.resource : selectedResource
    const rDone = !hasResources || !!rRes || (anyAllowedFor(state.services ?? selectedServices) && (state.any ?? anyResource))
    const dDone = state.date ?? dateDone
    for (const step of selectionSteps) {
      if (step === 'services' && !sDone) return go('services')
      if (step === 'resource' && !rDone) return go('resource')
      if (step === 'calendar' && !dDone) {
        pushStep()
        return setScreen('calendar')
      }
      if (step === 'extras' && !extrasSeen) {
        pushStep()
        return setScreen('extras')
      }
    }
    pushStep()
    setScreen('review')
  }

  const chooseService = (service: Service) => {
    if (multiService) {
      setSelectedServices((prev) =>
        prev.some((item) => item.id === service.id)
          ? prev.filter((item) => item.id !== service.id)
          : [...prev, service]
      )
      if (flowOrder === 'service_first' && selectedResource && !resourceSupportsService(selectedResource, service.id, employees)) {
        setSelectedResource(null)
      }
      resetDownstream()
      return
    }
    const nextServices = [service]
    setSelectedServices(nextServices)
    let res = selectedResource
    if (flowOrder === 'service_first' && res && !resourceSupportsService(res, service.id, employees)) {
      res = null
      setSelectedResource(null)
    }
    // Услугу делает один-единственный мастер — выбирать не из кого, берём его сразу
    // и экран выбора мастера пропускаем.
    if (flowOrder === 'service_first' && !res && !isVenue) {
      const suitable = employees.filter((resource) => resourceSupportsService(resource, service.id, employees))
      if (suitable.length === 1) {
        res = suitable[0]
        setSelectedResource(res)
      }
    }
    resetDownstream()
    advanceAfter({ services: nextServices, resource: res, date: false })
  }

  const chooseResource = (resource: EmployeeWithPricing | null) => {
    setSelectedResource(resource)
    setAnyResource(resource === null)
    let nextServices = selectedServices
    if (resource && flowOrder === 'resource_first') {
      nextServices = selectedServices.filter((service) => resourceSupportsService(resource, service.id, employees))
      setSelectedServices(nextServices)
    }
    resetDownstream()
    advanceAfter({ services: nextServices, resource, any: resource === null, date: false })
  }

  const chooseTime = (time: string) => {
    setSelectedTime(time)
    advanceAfter({ date: true })
  }

  const toggleAddon = (svcId: number) => {
    setSelectedAddons((prev) => {
      const next = new Set(prev)
      if (next.has(svcId)) next.delete(svcId)
      else next.add(svcId)
      return next
    })
  }

  const setAddonHourCount = (svcId: number, hours: number) => {
    setAddonHours((prev) => ({ ...prev, [svcId]: Math.max(1, hours) }))
  }

  const showServiceInfo = (service: Service) => {
    const details = [
      service.price != null ? `Стоимость: ${formatPrice(service.price, service.is_hourly_price)}` : '',
      formatDuration(service.duration_minutes) ? `Длительность: ${formatDuration(service.duration_minutes)}` : '',
    ].filter(Boolean)
    setInfoModal({ title: service.name, description: service.description, details })
  }

  const showResourceInfo = (resource: EmployeeWithPricing) => {
    const details = [
      resource.specialization ? `Специализация: ${resource.specialization}` : '',
      resource.capacity ? `Вместимость: до ${resource.capacity} чел.` : '',
      resource.area_sqm ? `Площадь: ${resource.area_sqm} м²` : '',
      getRoomPriceRange(resource) ? `Стоимость: ${getRoomPriceRange(resource)}` : '',
    ].filter(Boolean)
    setInfoModal({ title: resource.name, description: resource.description, details, photos: resourcePhotos(resource) })
  }

  const businessPhotos = useMemo(() => parsePhotos(business?.photos), [business?.photos])
  const businessCover = businessPhotos[0] ?? null

  // Submit
  const handleSubmit = async () => {
    if (!selectedDate || !selectedTime || !businessId) return

    // Валидация контактов: подсветить пустые поля и сфокусировать первое
    const errs: { name?: boolean; phone?: boolean; email?: boolean } = {}
    if (!clientName.trim()) errs.name = true
    if (!clientPhone.trim() || clientPhone.replace(/\D/g, '').length < 10) errs.phone = true
    if (clientEmail.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(clientEmail.trim())) errs.email = true
    if (errs.name || errs.phone || errs.email) {
      setFieldErrors(errs)
      if (errs.email && !errs.name && !errs.phone) {
        setMoreOpen(true)
        setBookingError('Проверьте email или оставьте поле пустым.')
      } else {
        setBookingError(`Заполните: ${[errs.name && 'имя', errs.phone && 'телефон'].filter(Boolean).join(' и ')}`)
      }
      const target = errs.name ? nameInputRef.current : errs.phone ? phoneInputRef.current : emailInputRef.current
      setTimeout(() => {
        target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
        target?.focus()
      }, 50)
      return
    }
    setFieldErrors({})

    if (business?.legal_documents_enabled && !personalDataConsent) {
      setBookingError('Для записи необходимо отдельно дать согласие на обработку персональных данных.')
      return
    }
    if (business?.legal_documents_enabled && !bookingTermsAccepted) {
      setBookingError('Для записи необходимо принять условия записи.')
      return
    }

    if (submittingRef.current) return
    submittingRef.current = true
    setSubmitting(true)
    setBookingError(null)
    try {
      const additionalServiceIds = Array.from(selectedAddons)
      const additionalServiceHoursMap: Record<string, number> = {}
      additionalServiceIds.forEach((id) => {
        const svc = additionalServices.find((s) => s.id === id)
        if (svc?.is_hourly_price) additionalServiceHoursMap[String(id)] = addonHours[id] ?? 1
      })

      if (clientPhone) localStorage.setItem('clientPhone', clientPhone)
      if (clientEmail.trim()) localStorage.setItem('clientEmail', clientEmail.trim().toLowerCase())

      const result = await api.createBooking({
        business_id: Number(businessId),
        service_ids: selectedServices.map((s) => s.id),
        additional_service_ids: additionalServiceIds.length > 0 ? additionalServiceIds : undefined,
        additional_service_hours: Object.keys(additionalServiceHoursMap).length > 0 ? additionalServiceHoursMap : undefined,
        is_solo_booking: isVenue && isSoloPricing ? true : undefined,
        employee_id: selectedResource?.id,
        date: selectedDate,
        time: selectedTime,
        client_name: clientName,
        client_phone: clientPhone,
        client_email: clientEmail.trim() || undefined,
        notes: comment || undefined,
        personal_data_consent: business?.legal_documents_enabled ? personalDataConsent : undefined,
        booking_terms_accepted: business?.legal_documents_enabled ? bookingTermsAccepted : undefined,
        repeat_weeks: repeatEnabled ? repeatWeeks : undefined,
        seo_ref: seoRef,
      })
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const firstResult: any = Array.isArray(result) && result.length > 0
        ? result[0]
        : (result && typeof result === 'object' && 'id' in result ? result : null)
      if (firstResult) {
        setCreatedBookingId(firstResult.id)
        setCreatedNotifyToken(firstResult.notify_token ?? null)
        setCreatedPrepayment(typeof firstResult.prepayment_amount === 'number' ? firstResult.prepayment_amount : null)
        if (Array.isArray(result) && repeatEnabled) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const uniqueDates = Array.from(new Set(result.map((b: any) => b.date).filter(Boolean))) as string[]
          setCreatedDates(uniqueDates)
        }
        // Сохранить бронь в "Мои брони" для возврата без логина
        if (firstResult.notify_token) {
          try {
            const raw = localStorage.getItem('myBookings') || '[]'
            const arr = JSON.parse(raw)
            const list = Array.isArray(arr) ? arr : []
            const entry = {
              token: firstResult.notify_token,
              business: firstResult.business_name || business?.name || '',
              date: firstResult.date || selectedDate || '',
              time: firstResult.time || selectedTime || '',
              saved_at: Date.now(),
            }
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const idx = list.findIndex((x: any) => x?.token === entry.token)
            if (idx >= 0) list[idx] = entry
            else list.unshift(entry)
            localStorage.setItem('myBookings', JSON.stringify(list.slice(0, 20)))
          } catch { /* noop */ }
        }
      }
      setBookingError(null)
      // После создания брони назад в выбор не возвращаемся.
      historyRef.current = []
      // Экран предоплаты — только если сервер действительно ждёт предоплату.
      const needsPrepay = firstResult?.status ? firstResult.status === 'pending_prepayment' : prepaymentNeeded
      setScreen(needsPrepay ? 'prepay' : 'done')
    } catch (err: unknown) {
      console.error('Booking creation error:', err)
      const msg = err instanceof Error ? err.message : ''
      if (repeatEnabled) {
        try {
          const parsed = JSON.parse(msg)
          setBookingError(parsed?.detail ? `Не удалось создать серию — ${parsed.detail}` : 'Не удалось создать серию записей. Попробуйте другое время.')
        } catch {
          setBookingError('Не удалось создать серию записей. Попробуйте другое время.')
        }
      } else if ((err instanceof ApiError && err.status === 409) || msg.includes('already taken') || msg.includes('Занято')) {
        setBookingError('Это время уже занято. Выберите другое время.')
        setSelectedTime(null)
        setSlotsKey('')
        pushStep()
        setScreen('calendar')
      } else {
        setBookingError('Не удалось создать запись. Попробуйте ещё раз.')
      }
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }

  const handlePaymentSent = async () => {
    setPaymentError(null)
    try {
      // QR-клиент без логина в Telegram → шлём по notify_token (без auth).
      if (createdNotifyToken) await api.prepaymentSentByToken(createdNotifyToken)
      else if (createdBookingId) await api.prepaymentSent(createdBookingId)
      else return
      setPaymentSent(true)
      // Публичная страница брони — там поллинг и обновление статуса после проверки владельцем.
      if (createdNotifyToken) navigate(`/b/${createdNotifyToken}`)
    } catch {
      setPaymentError('Не удалось сообщить владельцу об оплате. Проверьте соединение и попробуйте ещё раз.')
    }
  }

  const handleReceiptSubmit = async () => {
    if (!pendingReceipt) return
    if (!createdNotifyToken && !createdBookingId) return
    setReceiptError(null)
    setReceiptUploading(true)
    try {
      if (createdNotifyToken) await api.uploadReceiptByToken(createdNotifyToken, pendingReceipt)
      else if (createdBookingId) await api.uploadReceipt(createdBookingId, pendingReceipt)
      setReceiptUploaded(true)
      if (pendingReceiptPreview) URL.revokeObjectURL(pendingReceiptPreview)
      setPendingReceipt(null)
      setPendingReceiptPreview(null)
      if (createdNotifyToken) navigate(`/b/${createdNotifyToken}`)
    } catch {
      setReceiptError('Не удалось отправить чек. Попробуйте ещё раз.')
    } finally {
      setReceiptUploading(false)
    }
  }

  const clearReceipt = () => {
    if (pendingReceiptPreview) URL.revokeObjectURL(pendingReceiptPreview)
    setPendingReceipt(null)
    setPendingReceiptPreview(null)
    setReceiptError(null)
  }

  // ── Тексты ──
  const serviceLabel = selectedServices.map((s) => s.name).join(', ')
  const resourceLabel = selectedResource?.name || (anyResource ? 'Любой специалист' : '')
  const minPrice = services.reduce<number | null>((min, s) => (s.price != null && s.price > 0 && (min == null || s.price < min) ? s.price : min), null)
  const minDuration = services.reduce<number | null>((min, s) => (s.duration_minutes && (min == null || s.duration_minutes < min) ? s.duration_minutes : min), null)
  const stepIndex = selectionSteps.indexOf(screen)
  // «След» выбора на телефоне: что уже выбрано — чипами слева в верхней строке; по нажатию можно вернуться и поменять.
  const trailChips: Array<{ key: string; text: string; target: Screen }> = []
  if (servicesDone && screen !== 'services') trailChips.push({ key: 'svc', text: selectedServices.length > 1 ? `${selectedServices.length} ${plural(selectedServices.length, 'услуга', 'услуги', 'услуг')}` : serviceLabel, target: 'services' })
  if ((selectedResource || anyResource) && screen !== 'resource') trailChips.push({ key: 'res', text: resourceLabel, target: 'resource' })
  if (dateDone && screen !== 'calendar') trailChips.push({ key: 'date', text: `${ruDate(selectedDate!)} · ${selectedTime}`, target: 'calendar' })
  const showTrail = (screen === 'resource' || screen === 'calendar' || screen === 'extras') && trailChips.length > 0
  const stepCount = (
    <>
      {showTrail && (
        <span className="bf-trail" aria-label="Уже выбрано">
          {trailChips.map((c) => (
            <button key={c.key} type="button" className="bf-trail-chip" onClick={() => go(c.target)} title="Изменить">{c.text}</button>
          ))}
        </span>
      )}
      <span className="bf-stepcount">{stepIndex >= 0 ? `Шаг ${stepIndex + 1} / ${selectionSteps.length}` : ''}</span>
    </>
  )
  const resourceIcon = isVenue ? <DoorOpen /> : <Users />

  const mapsHref = business?.address ? `https://yandex.ru/maps/?text=${encodeURIComponent(business.address)}` : null
  const profileHref = localStorage.getItem('token') ? '/my' : '/login'

  const backButton = (
    <button type="button" className="bf-back" onClick={back} aria-label="Назад">
      <ArrowLeft /><span>Назад</span>
    </button>
  )

  // ── Portrait helper ──
  const portrait = (resource: EmployeeWithPricing | null) => {
    if (!resource) return <span className="bf-portrait is-any" aria-hidden="true"><Users size={18} /></span>
    const cover = resourcePhotos(resource)[0]
    return (
      <span className={`bf-portrait ${isVenue ? 'is-venue' : ''}`} aria-hidden="true">
        {cover ? <img src={photoThumbSrc(cover)} alt="" loading="lazy" decoding="async" /> : (isVenue ? <DoorOpen size={17} /> : initials(resource.name))}
      </span>
    )
  }

  // ── Экраны ──

  function renderHome() {
    const choices: Array<{ key: Screen; icon: ReactNode; name: string; details: string; complete: boolean }> = []
    const serviceChoice = {
      key: 'services' as Screen,
      icon: <Sparkles />,
      name: choosePhrase('Выбрать', labels.service),
      details: servicesDone
        ? `${serviceLabel}${totalPrice > 0 ? ` · ${formatPrice(totalPrice)}` : ''}`
        : services.length > 0
          ? `${services.length} ${plural(services.length, 'вариант', 'варианта', 'вариантов')}${minPrice != null ? ` · от ${formatPrice(minPrice)}` : ''}`
          : 'Список уточняется',
      complete: servicesDone,
    }
    const resourceChoice = {
      key: 'resource' as Screen,
      icon: resourceIcon,
      name: choosePhrase('Выбрать', labels.resource),
      details: selectedResource || anyResource
        ? resourceLabel
        : anyAllowed ? 'Можно выбрать любого' : `${availableResources.length} ${plural(availableResources.length, 'вариант', 'варианта', 'вариантов')}`,
      complete: !!selectedResource || (anyAllowed && anyResource),
    }
    if (flowOrder === 'resource_first') {
      if (hasResources) choices.push(resourceChoice)
      choices.push(serviceChoice)
    } else {
      choices.push(serviceChoice)
      if (hasResources) choices.push(resourceChoice)
    }
    choices.push({
      key: 'calendar',
      icon: <Calendar />,
      name: `${choosePhrase('Выбрать', labels.date)} и ${(ACCUSATIVE[labels.time.trim().toLowerCase()] || labels.time.toLowerCase())}`,
      details: dateDone ? `${ruDate(selectedDate!)} · ${selectedTime}` : 'Удобное время для визита',
      complete: dateDone,
    })
    const ready = nextNeeded() === 'review'
    const hasAbout = !!(business?.description || business?.directions || businessPhotos.length > 1)
    return (
      <section
        className="bf-screen bf-screen--bhome"
        aria-label="Главная страница записи"
        onPointerMove={(e) => {
          // лёгкий параллакс за курсором (только мышь; на телефоне фото просто плавно «наезжает»)
          if (e.pointerType !== 'mouse') return
          const hero = e.currentTarget.querySelector<HTMLElement>('.bf-hero')
          if (!hero) return
          const r = e.currentTarget.getBoundingClientRect()
          hero.style.setProperty('--px', (((e.clientX - r.left) / r.width) * 2 - 1).toFixed(3))
          hero.style.setProperty('--py', (((e.clientY - r.top) / r.height) * 2 - 1).toFixed(3))
        }}
        onPointerLeave={(e) => {
          const hero = e.currentTarget.querySelector<HTMLElement>('.bf-hero')
          if (hero) { hero.style.setProperty('--px', '0'); hero.style.setProperty('--py', '0') }
        }}
      >
        <div className="bf-hero">
          {businessCover ? <img src={photoSrc(businessCover)} alt={business?.name || ''} /> : <HeroArt />}
          <div className="bf-hero-fade" />
        </div>
        <div className="bf-panel">
          <div className="bf-logo" aria-hidden="true">
            {businessPhotos[1] ? (
              <img src={photoSrc(businessPhotos[1])} alt="" />
            ) : (
              <>
                <div className="sym">{(business?.name || 'S').replace(/[«»"']/g, '').trim().charAt(0).toUpperCase()}</div>
                <div className="cap">{(business?.name || '').replace(/[«»"']/g, '').toUpperCase()}</div>
              </>
            )}
          </div>
          {business && !loadError && !isDesktop && (
            <Link to={`/chat?business_id=${business.id}`} className="bf-panel-chat" aria-label="Открыть чат с помощником">
              <MessageCircle /><span>Чат</span>
            </Link>
          )}
          <h1 className="bf-h1">{business?.name}</h1>
          {business?.address ? (
            <a className="bf-address" href={mapsHref!} target="_blank" rel="noopener noreferrer" style={{ display: 'block', textDecoration: 'none' }}>{business.address}</a>
          ) : business?.description ? (
            <div className="bf-address" style={{ display: '-webkit-box', WebkitLineClamp: 1, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{business.description}</div>
          ) : null}
          {hasAbout && (
            <button
              type="button"
              className="bf-about"
              onClick={() => setInfoModal({
                title: business?.name || 'О нас',
                description: [business?.description, business?.directions ? `Как добраться:\n${business.directions}` : ''].filter(Boolean).join('\n\n'),
                photos: businessPhotos,
              })}
            >
              Подробнее о месте
            </button>
          )}
          <nav className="bf-choices" aria-label="Параметры записи">
            {choices.map((c) => (
              <button key={c.key} type="button" className={`bf-choice ${c.complete ? 'is-complete' : ''}`} onClick={() => go(c.key)}>
                <span className="bf-choice-icon">{c.icon}</span>
                <span className="bf-choice-body">
                  <span className="bf-choice-name">{c.name}</span>
                  <span className="bf-choice-details">{c.details}</span>
                </span>
                <ChevronRight className="bf-chevron" />
              </button>
            ))}
          </nav>
          {/* На компьютере на первом экране — тот же заметный «Напишите» вместо плавающей кнопки */}
          {isDesktop && business && (
            <button type="button" className="bf-help bf-help--home" onClick={openChat}>{helpInner}</button>
          )}
        </div>
        {ready ? (
          <div className="bf-bottombar">
            <button type="button" className="bf-primary" onClick={() => go('review')}>Перейти к записи <ArrowRight /></button>
          </div>
        ) : (
          <footer className="bf-home-footer">
            <a className="bf-powered" href="https://servgo.ru" target="_blank" rel="noopener noreferrer">
              Работает на <Logo size={16} withText={false} /><strong>ServGo</strong>
            </a>
          </footer>
        )}
      </section>
    )
  }

  // Телефон бизнеса для звонка одним касанием (только цифры и ведущий +).
  const callHref = (() => {
    const raw = (business?.phone || '').trim()
    const digits = raw.replace(/[^\d+]/g, '').replace(/(?!^)\+/g, '')
    return digits.replace(/\D/g, '').length >= 5 ? `tel:${digits}` : ''
  })()

  // Подсказка на шаге услуг: не можете выбрать — напишите в чат.
  const helpInner = (
    <>
      <span className="bf-help-icon" aria-hidden="true"><MessageCircle /></span>
      <span className="bf-help-text">
        <strong>Не можете выбрать? Напишите</strong>
        <span>Подскажем в чате, какая услуга вам подходит</span>
      </span>
      <ArrowRight className="bf-help-arrow" aria-hidden="true" />
    </>
  )

  function renderServices() {
    const total = selectedServices.reduce((sum, s) => sum + (s.price ?? 0), 0)
    const first = selectedServices[0]
    return (
      <section className="bf-screen" aria-label="Выбор услуги">
        <div className="bf-inside-nav bf-inside-nav--call">
          {/* На компьютере сверху — «Напишите в чат» (телефон есть в карточке справа), на телефоне — звонок */}
          {isDesktop && business ? (
            <button type="button" className="bf-help" onClick={openChat}>{helpInner}</button>
          ) : callHref && (
            <a className="bf-call" href={callHref} aria-label={`Позвонить: ${business?.phone}`}>
              <Phone /><span>Позвонить <strong>{business?.phone}</strong></span>
            </a>
          )}
          {stepCount}
        </div>
        <div className="bf-inner">
          <h2 className="bf-h2">{choosePhrase('Выберите', labels.service)}</h2>
          <p className="bf-intro">{multiService ? 'Можно выбрать несколько — затем продолжите' : 'Нажмите на вариант — сразу перейдём дальше'}</p>
          {business && !isDesktop && (
            <Link to={`/chat?business_id=${business.id}`} className="bf-help">{helpInner}</Link>
          )}
          <div className="bf-stage" aria-live="polite">
            <div className="bf-stage-kicker">{business?.name} · {labels.service}</div>
            <div className="bf-stage-feature" key={first?.id ?? 'none'}>
              <span className="bf-stage-symbol"><Sparkles /></span>
              <div className="bf-stage-main">
                <div className="bf-stage-big">{first ? (selectedServices.length > 1 ? `${selectedServices.length} ${plural(selectedServices.length, 'услуга', 'услуги', 'услуг')}` : first.name) : 'Не уверены, что выбрать?'}</div>
                <div className="bf-stage-detail">{first ? (selectedServices.length > 1 ? serviceLabel : (first.description || 'Цена и длительность — ниже')) : <>Нажмите на значок <Info size={14} style={{ display: 'inline', verticalAlign: '-2px' }} /> справа от услуги — там подробности: что входит и для кого</>}</div>
              </div>
            </div>
            <div className="bf-stage-metrics">
              {first ? (
                <>
                  {total > 0 && <span>{formatPrice(total)}</span>}
                  {totalDuration > 0 && <span>{formatDuration(totalDuration)}</span>}
                </>
              ) : (
                <>
                  {minPrice != null && <span>От {formatPrice(minPrice)}</span>}
                  {minDuration != null && <span>от {formatDuration(minDuration)}</span>}
                  <span>{services.length} {plural(services.length, 'вариант', 'варианта', 'вариантов')}</span>
                </>
              )}
              {business?.prepayment_enabled && <span>Запись по предоплате{prepaymentPercent < 100 ? ` ${prepaymentPercent}%` : ''}</span>}
            </div>
          </div>
          <div className="bf-list">
            {availableServices.length === 0 && (
              <div className="bf-empty">
                {services.length === 0 ? 'Пока нет доступных услуг.' : `Для выбранного варианта «${labels.resource.toLowerCase()}» нет доступных услуг.`}
              </div>
            )}
            {availableServices.map((svc) => {
              const selected = selectedServices.some((s) => s.id === svc.id)
              const meta = [formatDuration(svc.duration_minutes), svc.description].filter(Boolean).join(' · ')
              return (
                <div key={svc.id} className="bf-option-row">
                  <button type="button" className={`bf-option has-info ${selected ? 'selected' : ''}`} aria-pressed={selected} onClick={() => chooseService(svc)}>
                    <span className="bf-mini-icon">{isVenue ? <Package /> : <Sparkles />}</span>
                    <span className="bf-option-data">
                      <span className="bf-option-title">{svc.name}</span>
                      {meta && <span className="bf-option-meta">{meta}</span>}
                      {svc.price != null && svc.price > 0 && <span className="bf-option-price">{formatPrice(svc.price, svc.is_hourly_price)}</span>}
                    </span>
                    {multiService ? <span className="bf-check" aria-hidden="true">{selected && <Check size={12} strokeWidth={3} />}</span> : <span className="bf-radio" aria-hidden="true" />}
                  </button>
                  <button type="button" className="bf-info-btn" onClick={() => showServiceInfo(svc)} aria-label={`Подробнее: ${svc.name}`}><Info /></button>
                </div>
              )
            })}
          </div>
        </div>
        <div className="bf-bottombar">
          <div className="bf-summary">
            {selectedServices.length === 0
              ? (multiService ? 'Выберите одну или несколько услуг' : 'Нажмите на услугу — перейдём дальше')
              : `${selectedServices.length > 1 ? `${selectedServices.length} усл.` : serviceLabel}${total > 0 ? ` · ${formatPrice(total)}` : ''}`}
          </div>
          {backButton}
          {multiService && (
            <button type="button" className="bf-primary" disabled={selectedServices.length === 0} onClick={() => advanceAfter({ date: false })}>
              Продолжить <ArrowRight />
            </button>
          )}
        </div>
      </section>
    )
  }

  function renderResource() {
    const list = availableResources
    const crew = list.slice(0, 3)
    return (
      <section className="bf-screen" aria-label={`Выбор: ${labels.resource}`}>
        <div className="bf-inside-nav">{stepCount}</div>
        <div className="bf-inner">
          <h2 className="bf-h2">{choosePhrase('Выберите', labels.resource)}</h2>
          <p className="bf-intro">{anyAllowed ? 'Можно выбрать любого — подберём подходящего' : 'Нажмите на вариант — сразу перейдём дальше'}</p>
          <div className="bf-stage" aria-live="polite">
            <div className="bf-stage-kicker">Ваш визит · {labels.resource}</div>
            <div className="bf-stage-feature" key={selectedResource?.id ?? (anyResource ? 'any' : 'none')}>
              <div className="bf-stage-people">
                {selectedResource ? portrait(selectedResource) : crew.map((r) => <span key={r.id} style={{ display: 'contents' }}>{portrait(r)}</span>)}
              </div>
              <div className="bf-stage-main">
                <div className="bf-stage-big">{selectedResource ? selectedResource.name : isVenue ? 'Где пройдёт встреча?' : 'Кому доверить визит?'}</div>
                <div className="bf-stage-detail">
                  {selectedResource
                    ? (selectedResource.specialization || selectedResource.description || getRoomPriceRange(selectedResource) || '')
                    : `${list.length} ${plural(list.length, 'вариант', 'варианта', 'вариантов')}${anyAllowed ? ' и выбор любого' : ''}`}
                </div>
              </div>
            </div>
            {servicesDone && <div className="bf-stage-metrics"><span>{serviceLabel}{totalDuration > 0 ? ` · ${formatDuration(totalDuration)}` : ''}</span></div>}
          </div>
          <div className="bf-list">
            {anyAllowed && (
              <button type="button" className={`bf-option ${anyResource && !selectedResource ? 'selected' : ''}`} aria-pressed={anyResource && !selectedResource} onClick={() => chooseResource(null)}>
                {portrait(null)}
                <span className="bf-option-data">
                  <span className="bf-option-title">Любой специалист</span>
                  <span className="bf-option-meta">Подберём свободного на выбранное время</span>
                </span>
                <span className="bf-radio" aria-hidden="true" />
              </button>
            )}
            {list.length === 0 && (
              <div className="bf-empty">
                {anyAllowed
                  ? 'Нет специалистов, связанных с выбранной услугой. Можно продолжить с любым специалистом.'
                  : `Нет вариантов «${labels.resource}», совместимых со всеми выбранными услугами.`}
              </div>
            )}
            {list.map((r) => {
              const selected = selectedResource?.id === r.id
              const meta = [r.specialization, r.capacity ? `до ${r.capacity} чел.` : '', r.area_sqm ? `${r.area_sqm} м²` : '', !r.specialization ? r.description : ''].filter(Boolean).join(' · ')
              const price = getRoomPriceRange(r)
              return (
                <div key={r.id} className="bf-option-row">
                  <button type="button" className={`bf-option has-info ${selected ? 'selected' : ''}`} aria-pressed={selected} onClick={() => chooseResource(r)}>
                    {portrait(r)}
                    <span className="bf-option-data">
                      <span className="bf-option-title">{r.name}</span>
                      {meta && <span className="bf-option-meta">{meta}</span>}
                      {price && <span className="bf-option-price">{price}</span>}
                    </span>
                    <span className="bf-radio" aria-hidden="true" />
                  </button>
                  <button type="button" className="bf-info-btn" onClick={() => showResourceInfo(r)} aria-label={`Подробнее: ${r.name}`}><Info /></button>
                </div>
              )
            })}
          </div>
        </div>
        <div className="bf-bottombar">
          <div className="bf-summary">{resourceLabel || `Нажмите на вариант — перейдём дальше`}</div>
          {backButton}
        </div>
      </section>
    )
  }

  function renderCalendar() {
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    const atCurrentMonth = month.getFullYear() === today.getFullYear() && month.getMonth() === today.getMonth()
    const offset = (new Date(month.getFullYear(), month.getMonth(), 1).getDay() + 6) % 7
    const count = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate()
    const monthTitle = month.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' })
    const todayKey = dateKey(today)
    return (
      <section className="bf-screen bf-screen--calendar" aria-label="Выбор даты и времени">
        <div className="bf-inside-nav">{stepCount}</div>
        <div className="bf-inner">
          <h2 className="bf-h2">{labels.date} и {labels.time.toLowerCase()}</h2>
          <div className="bf-stage" aria-live="polite" style={{ minHeight: 48 }}>
            <div className="bf-stage-kicker">Детали визита</div>
            <div className="bf-stage-feature" key={`${selectedDate ?? ''}|${selectedTime ?? ''}`}>
              <span className="bf-stage-symbol"><Clock /></span>
              <div className="bf-stage-main">
                <div className="bf-stage-big">{selectedDate ? `${ruDate(selectedDate)}${selectedTime ? ` · ${selectedTime}` : ''}` : 'Выберите удобный день'}</div>
                <div className="bf-stage-detail">{serviceLabel}{resourceLabel ? ` · ${resourceLabel}` : ''}{totalDuration > 0 ? ` · ${formatDuration(totalDuration)}` : ''}</div>
              </div>
            </div>
          </div>
          <div className="bf-calendar">
            <div className="bf-monthbar">
              <div className="bf-monthtitle">{monthTitle}</div>
              <div className="bf-monthnav">
                <button type="button" disabled={atCurrentMonth} onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))} aria-label="Предыдущий месяц"><ChevronLeft /></button>
                <button type="button" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))} aria-label="Следующий месяц"><ChevronRight /></button>
              </div>
            </div>
            <div className="bf-cal-grid">
              {['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].map((d) => <div key={d} className="bf-dayname">{d}</div>)}
              {Array.from({ length: offset }).map((_, i) => <span key={`e${i}`} />)}
              {Array.from({ length: count }).map((_, i) => {
                const d = new Date(month.getFullYear(), month.getMonth(), i + 1)
                const key = dateKey(d)
                const disabled = d < today
                const selected = key === selectedDate
                return (
                  <button
                    key={key}
                    type="button"
                    className={`bf-day ${key === todayKey ? 'today' : ''} ${selected ? 'selected' : ''}`}
                    disabled={disabled}
                    aria-pressed={selected}
                    aria-label={ruDate(key)}
                    onClick={() => {
                      setSelectedDate(key)
                      setSelectedTime(null)
                      setSlots([])
                      setPriceData(null)
                    }}
                  >
                    {i + 1}
                  </button>
                )
              })}
            </div>
          </div>
          <div className="bf-times">
            <div className="bf-times-heading">{labels.time}</div>
            <p className="bf-times-sub">{selectedDate ? ruDateLong(selectedDate) : 'Сначала выберите дату в календаре'}</p>
            <div className="bf-time-grid">
              {!selectedDate ? (
                <div className="bf-placeholder">Нажмите на удобный день, чтобы увидеть свободное время</div>
              ) : loadingSlots ? (
                Array.from({ length: 8 }).map((_, i) => <div key={i} className="bf-time-skel" />)
              ) : slots.length === 0 ? (
                <div className="bf-placeholder">На этот день свободного времени нет — выберите другую дату</div>
              ) : (
                slots.map((t) => (
                  <button key={t} type="button" className={`bf-time ${selectedTime === t ? 'selected' : ''}`} aria-pressed={selectedTime === t} onClick={() => chooseTime(t)}>{t}</button>
                ))
              )}
            </div>
          </div>
        </div>
        <div className="bf-bottombar">
          <div className="bf-summary">{dateDone ? `${ruDate(selectedDate!)} · ${selectedTime}` : 'Показано только свободное время'}</div>
          {backButton}
          <button type="button" className="bf-primary" disabled={!dateDone} onClick={() => advanceAfter({ date: true })}>Продолжить <ArrowRight /></button>
        </div>
      </section>
    )
  }

  function renderExtras() {
    const hours = Math.max(1, totalDuration / 60)
    const groupPrice = priceData?.total ?? selectedServices.reduce((sum, s) => sum + (s.price ?? 0), 0)
    const soloPrice = (selectedResource?.solo_price_per_hour ?? 0) * hours
    return (
      <section className="bf-screen" aria-label={labels.additional}>
        <div className="bf-inside-nav">{stepCount}</div>
        <div className="bf-inner">
          <h2 className="bf-h2">{hasFormatChoice && additionalServices.length === 0 ? 'Формат занятия' : labels.additional}</h2>
          <p className="bf-intro">{additionalServices.length > 0 ? 'Необязательно — добавьте, если нужно' : 'Выберите формат'}</p>
          <div className="bf-stage" style={{ minHeight: 40 }}>
            <div className="bf-stage-kicker">Ваш визит</div>
            <div className="bf-stage-feature" key={`${totalPrice}|${selectedAddons.size}`}>
              <span className="bf-stage-symbol"><Package /></span>
              <div className="bf-stage-main">
                <div className="bf-stage-big">{priceLoading ? 'Считаем стоимость…' : totalPrice > 0 ? formatPrice(totalPrice) : serviceLabel}</div>
                <div className="bf-stage-detail">{serviceLabel} · {selectedDate && ruDate(selectedDate)} · {selectedTime}</div>
              </div>
            </div>
          </div>
          {hasFormatChoice && (
            <div className="bf-list" style={{ marginBottom: 8 }}>
              <button type="button" className={`bf-option ${!isSoloPricing ? 'selected' : ''}`} aria-pressed={!isSoloPricing} onClick={() => setIsSoloPricing(false)}>
                <span className="bf-mini-icon"><Users /></span>
                <span className="bf-option-data"><span className="bf-option-title">Групповое занятие</span><span className="bf-option-meta">Цена по времени суток</span><span className="bf-option-price">{formatPrice(groupPrice)}</span></span>
                <span className="bf-radio" aria-hidden="true" />
              </button>
              <button type="button" className={`bf-option ${isSoloPricing ? 'selected' : ''}`} aria-pressed={isSoloPricing} onClick={() => setIsSoloPricing(true)}>
                <span className="bf-mini-icon"><UserCheck /></span>
                <span className="bf-option-data"><span className="bf-option-title">Индивидуальное</span><span className="bf-option-meta">{formatPrice(selectedResource?.solo_price_per_hour, true)} × {hours} ч</span><span className="bf-option-price">{formatPrice(soloPrice)}</span></span>
                <span className="bf-radio" aria-hidden="true" />
              </button>
            </div>
          )}
          {additionalServices.length > 0 && (
            <div className="bf-list">
              {additionalServices.map((svc) => {
                const selected = selectedAddons.has(svc.id)
                const h = addonHours[svc.id] ?? 1
                return (
                  <div key={svc.id}>
                    <div className="bf-option-row">
                      <button type="button" className={`bf-option has-info ${selected ? 'selected' : ''}`} aria-pressed={selected} onClick={() => toggleAddon(svc.id)}>
                        <span className="bf-mini-icon"><Plus /></span>
                        <span className="bf-option-data">
                          <span className="bf-option-title">{svc.name}</span>
                          {(svc.description || formatDuration(svc.duration_minutes)) && <span className="bf-option-meta">{[formatDuration(svc.duration_minutes), svc.description].filter(Boolean).join(' · ')}</span>}
                          {svc.price != null && svc.price > 0 && <span className="bf-option-price">{formatPrice(svc.price)}{svc.is_hourly_price ? '/ч' : ''}</span>}
                        </span>
                        <span className="bf-check" aria-hidden="true">{selected && <Check size={12} strokeWidth={3} />}</span>
                      </button>
                      <button type="button" className="bf-info-btn" onClick={() => showServiceInfo(svc)} aria-label={`Подробнее: ${svc.name}`}><Info /></button>
                    </div>
                    {selected && svc.is_hourly_price && (
                      <div className="bf-repeat" style={{ paddingLeft: 46 }}>
                        <span className="bf-stepper">
                          <button type="button" onClick={() => setAddonHourCount(svc.id, h - 1)} aria-label="Меньше часов"><Minus size={14} /></button>
                          <span>{h} ч</span>
                          <button type="button" onClick={() => setAddonHourCount(svc.id, h + 1)} aria-label="Больше часов"><Plus size={14} /></button>
                        </span>
                        <strong style={{ fontWeight: 550 }}>{formatPrice((svc.price ?? 0) * h)}</strong>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
        <div className="bf-bottombar">
          <div className="bf-summary">{selectedAddons.size > 0 ? `Добавлено: ${selectedAddons.size}` : 'Можно пропустить'}{totalPrice > 0 ? ` · итого ${formatPrice(totalPrice)}` : ''}</div>
          {backButton}
          <button type="button" className="bf-primary" onClick={() => { setExtrasSeen(true); pushStep(); setScreen('review') }}>Продолжить <ArrowRight /></button>
        </div>
      </section>
    )
  }

  function renderReview() {
    const rows: Array<{ label: string; value: string; target: Screen }> = []
    if (flowOrder === 'resource_first' && hasResources) rows.push({ label: labels.resource, value: resourceLabel || 'Не выбран', target: 'resource' })
    rows.push({ label: labels.service, value: serviceLabel || 'Не выбрана', target: 'services' })
    if (flowOrder === 'service_first' && hasResources) rows.push({ label: labels.resource, value: resourceLabel || 'Не выбран', target: 'resource' })
    rows.push({
      label: `${labels.date} и ${labels.time.toLowerCase()}`,
      value: dateDone ? `${ruDate(selectedDate!)} в ${selectedTime}${totalDuration > 0 ? ` · ${formatDuration(totalDuration)}` : ''}` : 'Не выбраны',
      target: 'calendar',
    })
    const activeAddons = additionalServices.filter((s) => selectedAddons.has(s.id))
    if (hasExtrasStep && (activeAddons.length > 0 || isSoloPricing)) {
      rows.push({
        label: labels.additional,
        value: [isSoloPricing ? 'Индивидуальное занятие' : '', ...activeAddons.map((a) => (a.is_hourly_price ? `${a.name} (${addonHours[a.id] ?? 1} ч)` : a.name))].filter(Boolean).join(', '),
        target: 'extras',
      })
    }
    const segments = isVenue && !isSoloPricing && priceData && priceData.segments.length > 0 ? priceData.segments : []
    const isMedical = business?.business_type === 'psychologist' || business?.business_type === 'medical'
    const missing: string[] = []
    if (!clientName.trim()) missing.push('имя')
    if (!clientPhone.trim()) missing.push('телефон')
    if (business?.legal_documents_enabled && !personalDataConsent) missing.push('согласие на обработку данных')
    if (business?.legal_documents_enabled && !bookingTermsAccepted) missing.push('принятие условий')
    return (
      <section className="bf-screen bf-screen--review" aria-label="Проверка записи">
        <div className="bf-inside-nav">{stepCount}</div>
        <div className="bf-inner">
          <h2 className="bf-h2">Проверьте запись</h2>
          <p className="bf-intro">Убедитесь, что всё верно</p>
          <div className="bf-col bf-col--main">
          <div className="bf-review">
            {rows.map((r) => (
              <div key={r.label} className="bf-review-row">
                <div>
                  <div className="bf-review-label">{r.label}</div>
                  <div className="bf-review-value">{r.value}</div>
                </div>
                <button type="button" className="bf-review-edit" onClick={() => go(r.target)}>Изменить</button>
              </div>
            ))}
            {segments.length > 0 && (
              <div className="bf-breakdown">
                {segments.map((seg, i) => (
                  <div key={i}><span>{seg.time_from}–{seg.time_to} × {formatPrice(seg.price_per_hour, true)}</span><span>{formatPrice(seg.subtotal)}</span></div>
                ))}
              </div>
            )}
            {totalPrice > 0 && (
              <div className="bf-total"><span>Стоимость</span><strong>{priceLoading ? '…' : <CountPrice value={totalPrice} fromZero />}</strong></div>
            )}
          </div>

          {prepaymentNeeded && (
            <div className="bf-note">
              <strong>Предоплата {prepaymentPercent < 100 ? `${prepaymentPercent}% · ` : ''}{formatPrice(prepaymentAmount)}</strong>
              После оформления переведите предоплату по реквизитам бизнеса и прикрепите чек — владелец проверит поступление и подтвердит запись.
            </div>
          )}
          </div>
          <div className="bf-col bf-col--side">
          {(business?.cancellation_policy_text || business?.cancellation_policy_photo) && (
            <div className="bf-note is-warn">
              <strong>Условия отмены</strong>
              {business.cancellation_policy_text ? (
                <span style={{ whiteSpace: 'pre-line' }}>{business.cancellation_policy_text}</span>
              ) : (
                <img src={photoSrc(business.cancellation_policy_photo!)} alt="Условия отмены" />
              )}
            </div>
          )}
          <div className="bf-fields">
            <h3>Контактные данные</h3>
            <div className="bf-pair">
              <label className="bf-field-label">Ваше имя
                <input
                  ref={nameInputRef}
                  className={`bf-input ${fieldErrors.name ? 'is-error' : ''}`}
                  autoComplete="name"
                  placeholder="Как к вам обращаться?"
                  value={clientName}
                  onChange={(e) => { setClientName(e.target.value); if (fieldErrors.name) setFieldErrors((p) => ({ ...p, name: false })) }}
                />
                {fieldErrors.name && <div className="bf-field-error">Укажите ваше имя</div>}
              </label>
              <label className="bf-field-label">Телефон
                <input
                  ref={phoneInputRef}
                  className={`bf-input ${fieldErrors.phone ? 'is-error' : ''}`}
                  type="tel"
                  inputMode="tel"
                  autoComplete="tel"
                  placeholder="+7 (___) ___-__-__"
                  value={clientPhone}
                  onChange={(e) => { setClientPhone(e.target.value); if (fieldErrors.phone) setFieldErrors((p) => ({ ...p, phone: false })) }}
                />
                {fieldErrors.phone && <div className="bf-field-error">Укажите телефон для связи</div>}
              </label>
            </div>
            {!moreOpen && !clientEmail && !comment ? (
              <button type="button" className="bf-more" onClick={() => setMoreOpen(true)}>+ Email и комментарий (необязательно)</button>
            ) : (
              <>
                <label className="bf-field-label">Email (необязательно)
                  <input
                    ref={emailInputRef}
                    className={`bf-input ${fieldErrors.email ? 'is-error' : ''}`}
                    type="email"
                    inputMode="email"
                    autoComplete="email"
                    placeholder="name@example.com"
                    value={clientEmail}
                    onChange={(e) => { setClientEmail(e.target.value); if (fieldErrors.email) setFieldErrors((p) => ({ ...p, email: false })) }}
                  />
                  {fieldErrors.email ? <div className="bf-field-error">Проверьте адрес email</div> : <div className="bf-policy">Пришлём подтверждение и напоминания ещё и на почту.</div>}
                </label>
                <label className="bf-field-label">Комментарий (необязательно)
                  <textarea className="bf-input" rows={2} placeholder="Пожелания к записи…" value={comment} onChange={(e) => setComment(e.target.value)} />
                  {isMedical && <div className="bf-policy">Не указывайте здесь диагнозы, сведения о здоровье или интимной жизни.</div>}
                </label>
              </>
            )}
            <div className="bf-repeat">
              <span>Повторять каждую неделю</span>
              <button type="button" className={`bf-switch ${repeatEnabled ? 'on' : ''}`} role="switch" aria-checked={repeatEnabled} aria-label="Повторять каждую неделю" onClick={() => setRepeatEnabled((v) => !v)} />
            </div>
            {repeatEnabled && (
              <div className="bf-repeat" style={{ paddingTop: 2 }}>
                <span className="bf-policy" style={{ margin: 0 }}>Ещё {repeatWeeks - 1} {repeatWeeks - 1 === 1 ? 'такая же запись' : 'таких же записи'} каждую неделю</span>
                <span className="bf-stepper">
                  <button type="button" onClick={() => setRepeatWeeks((w) => Math.max(2, w - 1))} aria-label="Меньше недель"><Minus size={14} /></button>
                  <span>{repeatWeeks} нед</span>
                  <button type="button" onClick={() => setRepeatWeeks((w) => Math.min(8, w + 1))} aria-label="Больше недель"><Plus size={14} /></button>
                </span>
              </div>
            )}
          </div>

          {business?.legal_documents_enabled && (
            <div className="bf-legal-box" style={{ marginTop: 8 }}>
              <label className="bf-legal">
                <input type="checkbox" checked={personalDataConsent} onChange={(e) => { setPersonalDataConsent(e.target.checked); setBookingError(null) }} />
                <span>Даю отдельное согласие на обработку персональных данных. <Link to={`/legal/consent/${business.id}`} target="_blank">Открыть согласие</Link></span>
              </label>
              <label className="bf-legal">
                <input type="checkbox" checked={bookingTermsAccepted} onChange={(e) => { setBookingTermsAccepted(e.target.checked); setBookingError(null) }} />
                <span>Принимаю <Link to={`/legal/terms/${business.id}`} target="_blank">условия записи</Link> и ознакомлен(а) с <Link to={`/legal/privacy/${business.id}`} target="_blank">политикой обработки персональных данных</Link>.</span>
              </label>
            </div>
          )}
          </div>
        </div>
        <div className="bf-bottombar">
          {bookingError && <p className="bf-error bf-error--bar" role="alert">{bookingError}</p>}
          <div className="bf-summary">{missing.length > 0 ? `Осталось: ${missing.join(', ')}` : `${serviceLabel}${dateDone ? ` · ${ruDate(selectedDate!)} · ${selectedTime}` : ''}`}</div>
          {backButton}
          <button type="button" className="bf-primary" onClick={handleSubmit} disabled={submitting}>
            {submitting ? <><span className="bf-spinner" />Оформляем…</> : <>Оформить запись <ArrowRight /></>}
          </button>
        </div>
      </section>
    )
  }

  function renderPrepay() {
    const paymentPhone = business?.payment_phone
    const paymentDetails = cleanPaymentDetails(business?.payment_details)
    const done = paymentSent || receiptUploaded
    return (
      <section className="bf-screen bf-screen--prepay" aria-label="Предоплата записи">
        <div className="bf-inside-nav">{stepCount}</div>
        <div className="bf-inner">
          <h2 className="bf-h2">Предоплата</h2>
          <p className="bf-intro">Запись создана. Чтобы закрепить время за вами, внесите предоплату.</p>
          <div className="bf-col bf-col--main">
          <div className="bf-pay-hero">
            <span className="bf-pay-hero-icon" aria-hidden="true"><CreditCard /></span>
            <div>
              <span className="bf-pay-caption">Предоплата{prepaymentPercent < 100 ? ` ${prepaymentPercent}%` : ''}</span>
              <div className="bf-pay-big"><CountPrice value={prepaymentAmount} fromZero /></div>
            </div>
          </div>
          <div className="bf-receipt">
            <div><span>Стоимость</span><strong>{formatPrice(totalPrice)}</strong></div>
            {prepaymentAmount < totalPrice && <div><span>Останется оплатить при визите</span><strong>{formatPrice(Math.max(0, totalPrice - prepaymentAmount))}</strong></div>}
            <div><span>{labels.date}</span><strong>{selectedDate && ruDate(selectedDate)}, {selectedTime}</strong></div>
          </div>
          <div className="bf-method">
            <div className="bf-method-title">Оплата переводом</div>
            <p>Переведите сумму напрямую бизнесу по реквизитам ниже и прикрепите подтверждение оплаты.</p>
            {paymentPhone && <span className="bf-method-chip">Телефон для перевода: {paymentPhone}</span>}
            {paymentDetails && <div><span className="bf-method-chip">{paymentDetails}</span></div>}
          </div>
          </div>
          <div className="bf-col bf-col--side">
          {(business?.cancellation_policy_text || business?.cancellation_policy_photo) && (
            <div className="bf-note is-warn">
              <strong>Условия отмены</strong>
              {business.cancellation_policy_text ? <span style={{ whiteSpace: 'pre-line' }}>{business.cancellation_policy_text}</span> : <img src={photoSrc(business.cancellation_policy_photo!)} alt="Условия отмены" />}
            </div>
          )}
          <div className="bf-upload">
            <input
              ref={receiptInputRef}
              type="file"
              accept="image/*,.pdf,application/pdf"
              hidden
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (!file) return
                setReceiptError(null)
                if (pendingReceiptPreview) URL.revokeObjectURL(pendingReceiptPreview)
                setPendingReceipt(file)
                setPendingReceiptPreview(file.type.startsWith('image/') ? URL.createObjectURL(file) : null)
                // Сброс value, чтобы повторный выбор того же файла снова вызвал onChange
                e.target.value = ''
              }}
            />
            {done ? (
              <div className="bf-flow-status is-ok"><span className="bf-status-dot" />{receiptUploaded ? 'Чек отправлен владельцу' : 'Владелец уведомлён об оплате'}</div>
            ) : (
              <>
                <button type="button" className="bf-upload-btn" onClick={() => receiptInputRef.current?.click()} disabled={!createdBookingId && !createdNotifyToken}>
                  <span className="bf-upload-glyph"><Paperclip /></span>
                  <span>{pendingReceipt ? 'Заменить чек' : 'Прикрепить чек'}</span>
                  <span className="bf-upload-hint">Фото или PDF</span>
                </button>
                {pendingReceipt && (
                  <div className="bf-chosen">
                    {pendingReceiptPreview ? <img src={pendingReceiptPreview} alt="Превью чека" /> : <Check size={16} />}
                    <span className="name">{pendingReceipt.name} · {(pendingReceipt.size / 1024).toFixed(0)} КБ</span>
                    <button type="button" onClick={clearReceipt} aria-label="Удалить чек" disabled={receiptUploading}>×</button>
                  </div>
                )}
              </>
            )}
            {receiptError && <p className="bf-error" role="alert">{receiptError}</p>}
            {paymentError && <p className="bf-error" role="alert">{paymentError}</p>}
            <p className="bf-policy">Оплата поступает напрямую бизнесу, ServGo её не принимает. После отправки подтверждения владелец проверит поступление — статус записи обновится после проверки.</p>
          </div>
          </div>
        </div>
        <div className="bf-bottombar">
          {done ? (
            createdNotifyToken
              ? <Link to={`/b/${createdNotifyToken}`} className="bf-primary">Статус записи <ArrowRight /></Link>
              : <button type="button" className="bf-primary" onClick={() => navigate('/')}>На главную <ArrowRight /></button>
          ) : (
            <>
              <button type="button" className="bf-primary" disabled={!pendingReceipt || receiptUploading} onClick={handleReceiptSubmit}>
                {receiptUploading ? <><span className="bf-spinner" />Отправляем…</> : <>Отправить чек <Send /></>}
              </button>
              {!business?.require_receipt_for_prepayment && (
                <button type="button" className="bf-text-alt" onClick={handlePaymentSent} disabled={!createdBookingId && !createdNotifyToken}>Я уже оплатил(а) · без чека</button>
              )}
            </>
          )}
        </div>
      </section>
    )
  }

  function renderDone() {
    const publicUrl = createdNotifyToken ? `${window.location.origin}/b/${createdNotifyToken}` : null
    return (
      <section className="bf-screen bf-screen--done" aria-label="Запись создана">
        <Confetti />
        <SuccessErrorBoundary bookingId={createdBookingId} notifyToken={createdNotifyToken}>
          <div className="bf-inner bf-result">
            <div className="bf-orb bf-orb--pop"><DrawnCheck /></div>
            <h2 className="bf-h2">Запись создана</h2>
            <p className="bf-intro">
              {serviceLabel}, {selectedDate && ruDate(selectedDate)} в {selectedTime}{resourceLabel ? ` · ${resourceLabel}` : ''}
            </p>
            <div className="bf-review" style={{ marginTop: 12 }}>
              {createdDates.length > 1 && (
                <div className="bf-review-row"><div><div className="bf-review-label">Повторится ещё</div><div className="bf-review-value">{createdDates.slice(1).map((d) => ruDate(d)).join(', ')}</div></div></div>
              )}
              {business?.address && (
                <div className="bf-review-row"><div><div className="bf-review-label">Адрес</div><a className="bf-review-value" href={mapsHref!} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--bf-blue)' }}>{business.address}</a></div></div>
              )}
              {business?.directions && (
                <div className="bf-review-row"><div><div className="bf-review-label">Как добраться</div><div className="bf-review-value" style={{ whiteSpace: 'pre-line', fontSize: 12 }}>{linkifyText(business.directions)}</div></div></div>
              )}
              {totalPrice > 0 && <div className="bf-total"><span>Итого</span><strong><CountPrice value={totalPrice} fromZero /></strong></div>}
            </div>
            <div className="bf-result-actions">
              <NotificationSetup notifyToken={createdNotifyToken} />
              {selectedDate && selectedTime && business && (
                <button
                  type="button"
                  className="bf-link-btn"
                  onClick={() => downloadBookingIcs({
                    businessName: business.name,
                    serviceName: serviceLabel || 'Запись',
                    date: selectedDate,
                    time: selectedTime,
                    durationMinutes: totalDuration || 60,
                    address: business.address,
                    description: resourceLabel ? `${serviceLabel} · ${resourceLabel}` : serviceLabel,
                  })}
                >
                  <Calendar /> Добавить в календарь
                </button>
              )}
              {createdNotifyToken && (
                <a className="bf-link-btn" href={`https://t.me/Sochiservice_bot?start=notify_${createdNotifyToken}`} target="_blank" rel="noopener noreferrer">
                  <Bell /> Уведомления в Telegram
                </a>
              )}
              {publicUrl && (
                <div className="bf-note">
                  <strong>Сохраните страницу брони</strong>
                  Откройте позже, чтобы видеть статус и инструкции владельца. Без Telegram.
                  <div className="bf-save-url">{publicUrl}</div>
                  <div style={{ display: 'flex', gap: 12, marginTop: 6 }}>
                    <button type="button" className="bf-review-edit" onClick={async () => { try { await navigator.clipboard.writeText(publicUrl); setToast('Ссылка скопирована') } catch { /* noop */ } }}>Скопировать</button>
                    <a className="bf-review-edit" href={publicUrl} target="_blank" rel="noopener noreferrer">Открыть ↗</a>
                  </div>
                </div>
              )}
            </div>
          </div>
        </SuccessErrorBoundary>
        <div className="bf-bottombar">
          <button type="button" className="bf-primary" onClick={() => navigate('/')}>На главную <ArrowRight /></button>
        </div>
      </section>
    )
  }

  let content: ReactNode
  if (loadError) {
    content = (
      <div className="bf-fallback">
        <h2 className="bf-h2">Не удалось открыть запись</h2>
        <p className="bf-intro">Проверьте ссылку или попробуйте позже.</p>
        <Link to="/" className="bf-text-alt">На главную</Link>
      </div>
    )
  } else if (!business) {
    content = <div className="bf-loading"><span className="bf-spinner" style={{ borderColor: '#d7e3f2', borderTopColor: '#346fda' }} /></div>
  } else {
    content = ({
      home: renderHome,
      services: renderServices,
      resource: renderResource,
      calendar: renderCalendar,
      extras: renderExtras,
      review: renderReview,
      prepay: renderPrepay,
      done: renderDone,
    } as Record<Screen, () => ReactNode>)[screen]()
  }

  // Карточка «Ваша запись» справа — только на компьютере (на телефоне скрыта стилями).
  const asideScreens: Screen[] = ['services', 'resource', 'calendar', 'extras']
  const showAside = !!business && !loadError && asideScreens.includes(screen)
  function renderAside() {
    const when = dateDone ? `${ruDate(selectedDate!)} · ${selectedTime}` : selectedDate ? ruDate(selectedDate) : ''
    const rowsAside: Array<{ label: string; value: string; target: Screen; empty: string }> = []
    const serviceRow = { label: labels.service, value: serviceLabel, target: 'services' as Screen, empty: 'Не выбрано' }
    const resourceRow = { label: labels.resource, value: resourceLabel, target: 'resource' as Screen, empty: 'Не выбрано' }
    if (flowOrder === 'resource_first') {
      if (hasResources) rowsAside.push(resourceRow)
      rowsAside.push(serviceRow)
    } else {
      rowsAside.push(serviceRow)
      if (hasResources) rowsAside.push(resourceRow)
    }
    rowsAside.push({ label: `${labels.date} и ${labels.time.toLowerCase()}`, value: when, target: 'calendar', empty: 'Не выбрано' })
    return (
      <aside className="bf-aside" aria-label="Ваша запись">
        <div className="bf-aside-kicker">Ваша запись</div>
        <div className="bf-aside-biz">{business?.name}</div>
        {business?.address && <div className="bf-aside-addr">{business.address}</div>}
        <div className="bf-aside-rows">
          {rowsAside.map((r) => (
            <div key={r.label} className={`bf-aside-row${r.value ? ' is-set' : ''}`}>
              <div className="bf-aside-text">
                <div className="bf-aside-label">{r.label}</div>
                <div className="bf-aside-value">{r.value || r.empty}</div>
              </div>
              {r.value && r.target !== screen && (
                <button type="button" className="bf-review-edit" onClick={() => go(r.target)}>Изменить</button>
              )}
            </div>
          ))}
        </div>
        {(totalDuration > 0 || totalPrice > 0 || priceLoading) && (
          <div className="bf-aside-total">
            {totalDuration > 0 && <div className="bf-aside-line"><span>Длительность</span><strong>{formatDuration(totalDuration)}</strong></div>}
            {(totalPrice > 0 || priceLoading) && <div className="bf-aside-line is-big"><span>Стоимость</span><strong>{priceLoading ? '…' : <CountPrice value={totalPrice} />}</strong></div>}
            {prepaymentNeeded && <div className="bf-aside-line"><span>Предоплата{prepaymentPercent < 100 ? ` ${prepaymentPercent}%` : ''}</span><strong><CountPrice value={estimatedPrepayment} /></strong></div>}
          </div>
        )}
        {callHref && (
          <a className="bf-aside-call" href={callHref}><Phone /><span>{business?.phone}</span></a>
        )}
      </aside>
    )
  }

  const chatReady = !!business && !loadError
  // Прогресс: на шагах — текущий шаг, на финальном 100%, на первом экране — сколько уже выбрано
  // (чтобы при возврате на первый экран полоска не «обнулялась»).
  const progressDone = screen === 'done'
    ? selectionSteps.length
    : stepIndex >= 0 ? stepIndex + 1 : (business ? Math.max(0, selectionSteps.indexOf(nextNeeded())) : 0)
  const progressPct = selectionSteps.length > 0 ? Math.round((progressDone / selectionSteps.length) * 100) : 0

  return createPortal(
    <div className="bf-root">
      <main className={`bf-device bf-on-${screen}${chatReady && !isDesktop ? ' bf-has-fab' : ''}${showAside ? ' bf-aside-on' : ''}`} aria-label="Онлайн-запись ServGo" onClickCapture={(e) => { if ((e.target as HTMLElement).closest?.('.bf-option, .bf-time, .bf-day, .bf-choice, .bf-primary')) haptic(8) }}>
        <header className="bf-topbar">
          <Link to="/" className="bf-brand" aria-label="ServGo"><Logo size={28} /></Link>
          <div className="bf-spacer" />
          <Link to={profileHref} className="bf-user" title="Личный кабинет" aria-label="Личный кабинет"><User /></Link>
          {chatReady && (
            <div className="bf-progress" role="progressbar" aria-label="Ход записи" aria-valuemin={0} aria-valuemax={selectionSteps.length} aria-valuenow={progressDone}>
              <span style={{ width: `${progressPct}%` }} />
            </div>
          )}
        </header>
        <div className={`bf-body${showAside ? ' has-aside' : ''}`}>
          <div key={screen} className={`bf-body-main bf-dir-${direction}`} style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>{content}</div>
          {showAside && renderAside()}
        </div>
        {/* Плавающая кнопка «Чат»: на телефоне — полноэкранный чат, на компьютере — небольшое окошко. */}
        {chatReady && !isDesktop && (
          <Link to={`/chat?business_id=${business!.id}`} className="bf-chat-fab" aria-label="Открыть чат с помощником">
            <MessageCircle /><span>Чат</span>
          </Link>
        )}
        {chatReady && isDesktop && !chatOpen && (
          <button type="button" className="bf-chat-fab" onClick={openChat} aria-label="Открыть чат с помощником">
            <MessageCircle /><span>Чат</span>
          </button>
        )}
      </main>
      {chatReady && isDesktop && chatEverOpened && (
        <div className="bf-chat-widget" role="dialog" aria-label="Чат с помощником" hidden={!chatOpen}>
          <Suspense fallback={<div className="bf-loading"><span className="bf-spinner" style={{ borderColor: '#d7e3f2', borderTopColor: '#346fda' }} /></div>}>
            <ChatThread business={business!} embedded onClose={() => setChatOpen(false)} />
          </Suspense>
        </div>
      )}
      {toast && <div className="bf-toast" role="status">{toast}</div>}
      {infoModal && <InfoSheet data={infoModal} onClose={() => setInfoModal(null)} />}
    </div>,
    document.body
  )
}

// Глобальный ErrorBoundary на всю страницу: если main render падает, клиент
// видит сообщение + ссылку на главную, а не белый экран.
class PageErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }
  static getDerivedStateFromError(error: Error) { return { error } }
  componentDidCatch(error: Error, info: { componentStack?: string }) {
    try {
      console.error('BookingPage crashed:', error)
      console.error('Component stack:', info?.componentStack)
    } catch { /* noop */ }
  }
  render() {
    if (this.state.error) {
      return (
        <div className="bf-root">
          <div className="bf-device">
            <div className="bf-fallback">
              <h2 className="bf-h2">Не удалось открыть форму записи</h2>
              <p className="bf-intro">Произошла ошибка. Попробуйте обновить страницу или вернуться на главную.</p>
              <p className="mono">{String(this.state.error?.message || this.state.error)}</p>
              <button type="button" className="bf-primary" style={{ flex: 'none', width: '100%' }} onClick={() => window.location.reload()}>Обновить</button>
              <Link to="/" className="bf-text-alt">На главную</Link>
            </div>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

export function BookingPage() {
  return (
    <PageErrorBoundary>
      <BookingPageInner />
    </PageErrorBoundary>
  )
}
