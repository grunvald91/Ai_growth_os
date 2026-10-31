// === Type definitions ===

export interface BlogArticleSummary {
  slug: string
  title: string
  description: string
  category: string
  lead: string
  published_at: string
}

export interface BusinessBlogFeed {
  business_id: number
  company_url: string
  booking_url: string
  articles: BlogArticleSummary[]
}

export interface BusinessBlogArticle extends BlogArticleSummary {
  direct_answer: string
  sections: { title: string; paragraphs: string[]; bullets: string[] }[]
  closing: string
  faq: { question: string; answer: string }[]
  cta: string
  cta_url: string
  company_url: string
  booking_url: string
}


export interface SalesBookingSummary {
  business_name: string;
  service_name: string;
  resource_name: string;
  resource_label: string;
  date: string;
  time: string;
  duration_minutes: number;
  solo: boolean;
  addons: string[];
  amount: number | null;
  prepayment_amount: number | null;
  prepayment_percent: number | null;
  receipt_required: boolean;
}

export interface SalesPendingAction {
  action_id: string;
  description: string;
  summary?: SalesBookingSummary | null;
  tool_name: string;
  requires_contact: boolean;
  requires_legal_consent: boolean;
  business_id: number;
  cancellation_policy?: string;
  expires_at: string;
}

export interface SalesSlotOffer {
  date: string;
  service_id: number;
  service_name?: string | null;
  duration_minutes?: number | null;
  solo?: boolean;
  slots: { employee_id: number; employee_name: string; time: string }[];
  expires_at: string;
}

export interface SalesChatResult {
  reply: string;
  pending_action?: SalesPendingAction | null;
  available_slots?: SalesSlotOffer | null;
  mode: 'ai' | 'human';
  booking_url?: string | null;
}

export interface SalesHistory {
  messages: { id: string; role: 'user' | 'ai'; text: string }[];
  mode: 'ai' | 'human';
  pending_action?: SalesPendingAction | null;
  available_slots?: SalesSlotOffer | null;
  booking_url?: string | null;
}

export interface SalesConfirmation {
  client_name?: string;
  client_phone?: string;
  personal_data_consent?: boolean;
  booking_terms_accepted?: boolean;
}

export interface SalesInboxThread {
  id: string;
  reason?: string;
  messages: SalesHistory['messages'];
  updated_at: string;
}

export interface SalesPolicy {
  enabled: boolean;
  booking_mode: 'client_confirmation' | 'operator_only';
  show_prices: boolean;
  recommend_specialists: boolean;
  check_availability: boolean;
  view_client_bookings: boolean;
  instructions: string;
  prohibited_topics: string;
}

export interface SalesSettings {
  business_id: number;
  business_name: string;
  revision: number;
  policy: SalesPolicy;
  always_forbidden: string[];
  coverage: { rule: string; status: string }[];
  business_rules: Record<string, string | number | boolean | null>;
}

export interface Business {
  id: number;
  name: string;
  description?: string;
  business_type?: string;
  address?: string;
  phone?: string;
  working_hours_start?: string;
  working_hours_end?: string;
  photos?: string;
  directions?: string;
  services_count?: number;
  // Legacy compatibility
  working_hours?: string;
  photo_url?: string;
  rating?: number;
  reviews_count?: number;
}

export interface GroupPricingTier {
  min_people: number;
  max_people: number;
  price: number;
}

export interface Service {
  id: number;
  name: string;
  description?: string;
  price?: number;
  duration_minutes?: number;
  is_active: boolean;
  is_additional?: boolean;
  is_hourly_price?: boolean;
  sort_order?: number;
  pricing_type?: string;  // 'fixed' | 'per_person'
  group_pricing?: GroupPricingTier[] | null;
  // Legacy compatibility
  duration?: number;
}

export interface Employee {
  id: number;
  name: string;
  role?: string;
  specialization?: string;
  description?: string;
  photo_url?: string;
  is_active?: boolean;
  telegram_id?: number | null;
  /** Email для входа мастера на сайт (виден только владельцу). */
  access_email?: string | null;
  booking_interval_minutes?: number;
  capacity?: number;
  area_sqm?: number;
  min_booking_minutes?: number;
  solo_price_per_hour?: number;
  photos?: string;
  primary_photo?: string;
  on_duty_date?: string;
  service_ids?: number[];
}

export interface MasterEmployee {
  employee_id: number;
  employee_name: string;
  business_id: number;
  business_name: string;
}

export interface MasterBooking {
  id: number;
  business_id: number;
  business_name: string;
  employee_id: number;
  employee_name: string;
  service_id?: number | null;
  service_name: string;
  duration_minutes?: number | null;
  actual_end_time?: string | null;
  date: string;
  time: string;
  status: string;
  client_name: string;
  client_phone?: string | null;
  notes?: string | null;
}

export interface MasterSchedule {
  date_from: string;
  date_to: string;
  employees: MasterEmployee[];
  bookings: MasterBooking[];
  days_off: { employee_id: number; date: string }[];
  services?: { id: number; business_id: number; name: string; duration_minutes?: number | null; is_additional?: boolean }[];
}

export interface TimePricing {
  time_from: string;
  time_to: string;
  price_per_hour: number;
  day_type: string;
}

export interface EmployeeWithPricing extends Employee {
  description?: string;
  capacity?: number;
  solo_price_per_hour?: number;
  time_pricing?: TimePricing[];
}

export interface PriceSegment {
  time_from: string;
  time_to: string;
  minutes: number;
  price_per_hour: number;
  subtotal: number;
}

export interface CalculatePriceResponse {
  has_time_price: boolean;
  total: number;
  segments: PriceSegment[];
}

export interface BusinessDetail extends Business {
  services: Service[];
  employees: EmployeeWithPricing[];
  break_start?: string;
  break_end?: string;
  business_type?: string;
  prepayment_enabled?: boolean;
  prepayment_percent?: number;
  payment_phone?: string;
  payment_details?: string;
  cancellation_policy_photo?: string;
  cancellation_policy_text?: string;
  require_receipt_for_prepayment?: boolean;
  legal_name?: string;
  legal_inn?: string;
  legal_ogrn?: string;
  legal_address?: string;
  legal_email?: string;
  legal_documents_enabled?: boolean;
  booking_labels?: Record<string, string>;
  booking_flow_order?: 'service_first' | 'resource_first';
  allow_multiple_services?: boolean;
  studio_admin_access?: boolean;
}

export interface Booking {
  id: number;
  business_id: number;
  service_id: number;
  business_name: string;
  service_name: string;
  service_price?: number;
  employee_name?: string;
  employee_id?: number;
  date: string;
  time: string;
  duration?: number;
  price?: number;
  status: string;
  notes?: string;
  created_at: string;
  can_cancel?: boolean;
  can_reschedule?: boolean;
  notify_token?: string;
  business_address?: string;
  duration_minutes?: number;
  prepayment_amount?: number | null;
}


export interface SlotAvailability {
  time: string;
  available: boolean;
}

export interface PublicBookingService {
  name: string;
  price?: number | null;
  is_additional: boolean;
}

export interface PublicBooking {
  notify_token: string;
  status: string;
  business_name: string;
  business_address?: string | null;
  business_phone?: string | null;
  business_directions?: string | null;
  date: string;
  time: string;
  duration_minutes: number;
  employee_name?: string | null;
  employee_label: string;
  services: PublicBookingService[];
  total_price: number;
  prepayment_enabled: boolean;
  prepayment_amount: number;
  paid_amount: number;
  prepayment_confirmed: boolean;
  prepayment_expires_at?: string | null;
  price_requires_confirmation?: boolean;
  payment_phone?: string | null;
  payment_details?: string | null;
  require_receipt_for_prepayment: boolean;
  cancellation_policy_text?: string | null;
  cancellation_policy_photo?: string | null;
}

export interface SubscriptionServiceItem {
  id: number;
  name: string;
  quantity: number;
}

export interface SubscriptionPlan {
  id: number;
  name: string;
  description?: string;
  price: number;
  sessions_count?: number;
  validity_days: number;
  services: SubscriptionServiceItem[];
}

export interface ClientSubscription {
  id: number;
  subscription_name: string;
  business_name: string;
  status: string;
  sessions_remaining: number;
  sessions_total: number;
  valid_until?: string;
  payment_status?: string;
  created_at: string;
}

export interface CreateBookingData {
  business_id: number;
  service_ids: number[];
  additional_service_ids?: number[];
  additional_service_hours?: Record<string, number>;
  is_solo_booking?: boolean;
  employee_id?: number;
  date: string;
  time: string;
  client_name: string;
  client_phone: string;
  client_email?: string;
  notes?: string;
  personal_data_consent?: boolean;
  booking_terms_accepted?: boolean;
  repeat_weeks?: number;
}

export interface CRMContact {
  key: string;
  name: string;
  phone: string | null;
  email: string | null;
  notes: string;
  stage: 'new' | 'active' | 'vip' | 'paused';
  follow_up_on: string | null;
  bookings_count: number;
  completed_count: number;
  last_booking: string | null;
  next_booking: string | null;
  created_at: string | null;
}

export interface CRMContactDetail extends CRMContact {
  history: Array<{
    id: number;
    date: string;
    time: string;
    service: string;
    employee: string | null;
    status: string;
    price_estimate: number | null;
  }>;
  history_has_more: boolean;
}

export type CRMContactInput = {
  name?: string | null;
  phone?: string | null;
  email?: string | null;
  notes?: string;
  stage?: CRMContact['stage'];
  follow_up_on?: string | null;
};

export interface AdminBooking {
  version: number;
  id: number;
  client_name: string;
  client_phone?: string;
  client_email?: string;
  service_id?: number;
  service_name: string;
  service_duration?: number;
  /** «ЧЧ:ММ», если запись завершили раньше плана */
  actual_end_time?: string | null;
  price?: number;
  is_rental?: boolean;
  price_requires_confirmation?: boolean;
  prepayment_amount?: number | null;
  employee_name?: string;
  employee_id?: number;
  date: string;
  time: string;
  status: string;
  notes?: string;
  created_at?: string;
}

export interface ManualBookingData {
  business_id: number;
  service_id: number;
  employee_id?: number;
  date: string;
  time: string;
  client_name: string;
  client_phone?: string;
  notes?: string;
  repeat_weeks?: number;
}

export interface DashboardData {
  today_bookings: number;
  total_revenue: number;
  pending_count: number;
  confirmed_count: number;
}

export interface AnalyticsData {
  revenue: number;
  bookings_count: number;
  avg_check: number;
  top_services: { name: string; count: number }[];
  revenue_by_day: { date: string; revenue: number }[];
}

export interface AnalyticsTopItem {
  id: number;
  name: string;
  count: number;
  revenue: number;
  share_percent: number;
}

export interface AnalyticsDailyPoint {
  date: string;
  bookings: number;
  revenue: number;
}

export interface AnalyticsOverview {
  kpis: {
    revenue: number;
    bookings: number;
    avg_check: number;
    load_percent: number | null;
    revenue_prev: number;
    bookings_prev: number;
    avg_check_prev: number;
    load_percent_prev: number | null;
  };
  top_services: AnalyticsTopItem[];
  top_employees: AnalyticsTopItem[];
  daily: AnalyticsDailyPoint[];
}

export interface BusinessAdminDetail {
  id: number;
  name: string;
  description?: string;
  business_type: string;
  address?: string;
  phone?: string;
  working_hours_start?: string;
  working_hours_end?: string;
  break_start?: string;
  break_end?: string;
  booking_interval_minutes?: number;
  directions?: string;
  photos?: string;
  video?: string;
  prepayment_enabled: boolean;
  prepayment_percent?: number;
  payment_phone?: string;
  payment_details?: string;
  cancellation_policy_photo?: string;
  cancellation_policy_text?: string;
  require_receipt_for_prepayment: boolean;
  legal_name?: string;
  legal_inn?: string;
  legal_ogrn?: string;
  legal_address?: string;
  legal_email?: string;
  legal_documents_enabled?: boolean;
  booking_labels?: Record<string, string>;
  booking_flow_order?: 'service_first' | 'resource_first';
  allow_multiple_services?: boolean;
  studio_admin_access?: boolean;
  owner_reminders_enabled: boolean;
  owner_reminder_minutes: number[];
  master_reminders_enabled: boolean;
  master_reminder_minutes: number[];
}

export interface BusinessCreateData {
  name: string;
  business_type: string;
  description?: string;
  address?: string;
  phone?: string;
}

export interface BusinessUpdateData {
  name?: string;
  description?: string;
  address?: string;
  phone?: string;
  working_hours_start?: string;
  working_hours_end?: string;
  break_start?: string;
  break_end?: string;
  directions?: string;
  booking_interval_minutes?: number;
  prepayment_enabled?: boolean;
  prepayment_percent?: number;
  payment_phone?: string;
  payment_details?: string;
  cancellation_policy_text?: string;
  require_receipt_for_prepayment?: boolean;
  legal_name?: string;
  legal_inn?: string;
  legal_ogrn?: string;
  legal_address?: string;
  legal_email?: string;
  legal_documents_enabled?: boolean;
  booking_labels?: Record<string, string>;
  booking_flow_order?: 'service_first' | 'resource_first';
  allow_multiple_services?: boolean;
  studio_admin_access?: boolean;
  owner_reminders_enabled?: boolean;
  owner_reminder_minutes?: number[];
  master_reminders_enabled?: boolean;
  master_reminder_minutes?: number[];
}

export interface OwnerInfo {
  id: number;
  user_id: number;
  role: string;
  user_name: string;
  user_phone?: string;
  telegram_id?: number;
  added_at?: string;
}

export interface TimePricingAdmin {
  id: number;
  time_from: string;
  time_to: string;
  price_per_hour: number;
  day_type: string;
}

export interface EmployeeServiceLink {
  service_id: number;
  service_name: string;
}

export interface SubscriptionAdmin {
  id: number;
  name: string;
  description?: string;
  price: number;
  validity_days: number;
  rules?: string;
  is_active: boolean;
  services: SubscriptionServiceItem[];
}

export interface SubscriptionCreateData {
  name: string;
  description?: string;
  price: number;
  validity_days: number;
  rules?: string;
  services: { service_id: number; quantity: number }[];
}

export interface AuthResponse {
  token: string;
  role: 'client' | 'admin' | 'owner';
  user: {
    id: number;
    full_name: string;
    phone?: string;
    email?: string;
    username?: string;
  };
}

// === API Client ===

import { canSaveOffline, loadOfflineSnapshot, publishOfflineSnapshotUsed, saveOfflineSnapshot } from '../utils/offlineStore'

const API = '/api';

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

export interface TelegramLoginPayload {
  id: number
  auth_date: number
  hash: string
  first_name?: string
  last_name?: string
  username?: string
  photo_url?: string
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const token = localStorage.getItem('token');
  const tgInitData = window.Telegram?.WebApp?.initData || '';
  const method = (options?.method || 'GET').toUpperCase();
  const offlineReadable = method === 'GET' && !!token && canSaveOffline(path);

  // Offline writes are never queued or applied optimistically as server state.
  if (method !== 'GET' && !navigator.onLine) {
    throw new ApiError(0, 'Для изменения данных необходимо подключение к интернету.');
  }

  let res: Response;
  try {
    res = await fetch(`${API}${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(tgInitData ? { 'X-Telegram-Init-Data': tgInitData } : {}),
        ...options?.headers,
      },
    });
  } catch (error) {
    window.dispatchEvent(new Event('servgo-api-offline'))
    // Only a transport failure can fall back to saved data: never HTTP 401/403.
    if (offlineReadable && token) {
      try {
        const snapshot = await loadOfflineSnapshot<T>(path, token);
        if (snapshot) {
          publishOfflineSnapshotUsed(snapshot.savedAt, path);
          return snapshot.data;
        }
      } catch (storageError) {
        console.warn('ServGo offline snapshot unavailable:', storageError);
      }
    }
    throw error;
  }
  // Gateway outage: keep cached schedules readable, but never mask 401/403.
  if ([502, 503, 504].includes(res.status) && offlineReadable && token) {
    try {
      const snapshot = await loadOfflineSnapshot<T>(path, token)
      if (snapshot) {
        window.dispatchEvent(new Event('servgo-api-offline'))
        publishOfflineSnapshotUsed(snapshot.savedAt, path)
        return snapshot.data
      }
    } catch (storageError) {
      console.warn('ServGo offline fallback unavailable:', storageError)
    }
  }
  window.dispatchEvent(new Event('servgo-api-online'))
  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, text || `HTTP ${res.status}`);
  }
  const data = await res.json() as T;
  if (offlineReadable && token) {
    // Storage failures must not prevent the ordinary online booking workflow.
    try { await saveOfflineSnapshot(path, data, token) }
    catch (error) { console.warn('ServGo offline snapshot not saved:', error) }
  }
  return data;
}

export const api = {
  emailCapabilities: () => request<{ available: boolean }>('/auth/email/capabilities'),
  yandexCapabilities: () => request<{ available: boolean }>('/auth/yandex/capabilities'),
  yandexAccount: () => request<{ available: boolean; linked: boolean; login: string | null; telegram_join: boolean }>('/auth/yandex/account'),
  yandexLinkStart: () => request<{ url: string }>('/auth/yandex/link/start', { method: 'POST' }),
  yandexExchange: (body: { ticket: string; code?: string; separate?: boolean }) =>
    request<({ status: 'ok' } & AuthResponse) | { status: 'linked' } | { status: 'code_required' | 'code_unavailable'; account: 'new' | 'existing' }>(
      '/auth/yandex/exchange', { method: 'POST', body: JSON.stringify(body) }),
  emailAccount: () => request<{ email: string | null; available: boolean; telegram: { id: number; username: string | null } | null; telegram_link_available: boolean }>('/auth/email/account'),
  startTelegramLink: (password: string) => request<{ challenge_id: string; url: string; expires_at: string }>('/auth/telegram-link/start', { method: 'POST', body: JSON.stringify({ password }) }),
  telegramLinkStatus: (id: string) => request<{ state: string; telegram: { id: number; name: string; username: string | null } | null; business_count: number }>(`/auth/telegram-link/${id}`),
  completeTelegramLink: (challenge_id: string, password: string) => request<AuthResponse>('/auth/telegram-link/complete', { method: 'POST', body: JSON.stringify({ challenge_id, password }) }),
  cancelTelegramLink: (id: string) => request(`/auth/telegram-link/${id}/cancel`, { method: 'POST' }),
  prepaymentAttention: () => request<{ count: number; first: { booking: AdminBooking; business_name: string; business_id: number } | null }>('/admin/prepayments/attention'),
  emailLogin: (email: string, password: string) => request<AuthResponse>('/auth/email/login', {
    method: 'POST', body: JSON.stringify({ email, password }),
  }),
  emailStart: (data: { email: string; password: string; full_name?: string; purpose: 'register' | 'link' | 'reset' }) =>
    request<{ challenge_id: string; message: string }>('/auth/email/start', { method: 'POST', body: JSON.stringify(data) }),
  emailVerify: (challenge_id: string, code: string) => request<AuthResponse>('/auth/email/verify', {
    method: 'POST', body: JSON.stringify({ challenge_id, code }),
  }),
  getPrepayments: (businessId: number, status: string, beforeId?: number) =>
    request<{ items: AdminBooking[]; next_before_id: number | null }>(`/admin/prepayments?business_id=${businessId}&status=${status}${beforeId ? `&before_id=${beforeId}` : ''}`),
  getPaymentDetails: (bookingId: number) => request<{ booking: AdminBooking; visit: AdminBooking[]; prepayment_total: number;
    receipts: { id: number; mime_type: string; size: number; created_at: string }[];
    reviews: { decision: string; reviewer_id: number; reviewed_at: string }[] }>(`/admin/bookings/${bookingId}/payment-details`),
  reviewPrepayment: (bookingId: number, decision: 'confirm' | 'reject', expected_version: number) =>
    request<AdminBooking>(`/admin/bookings/${bookingId}/prepayment-review`, {
      method: 'POST', body: JSON.stringify({ decision, expected_version }),
    }).then(result => { window.dispatchEvent(new Event('servgo:payment-reviewed')); return result }),
  downloadReceipt: async (receiptId: number) => {
    const response = await fetch(`${API}/admin/receipts/${receiptId}`, {
      headers: { Authorization: `Bearer ${localStorage.getItem('token') || ''}` },
    });
    if (!response.ok) throw new Error('Не удалось открыть чек');
    return response.blob();
  },
  // Auth
  loginPhone: (phone: string) =>
    request<{ message: string; fallback?: boolean }>('/auth/phone', {
      method: 'POST',
      body: JSON.stringify({ phone }),
    }),
  verifyCode: (phone: string, code: string) =>
    request<AuthResponse>('/auth/verify', {
      method: 'POST',
      body: JSON.stringify({ phone, code }),
    }),
  telegramAuth: (initData: string) =>
    request<AuthResponse>('/auth/telegram', {
      method: 'POST',
      body: JSON.stringify({ init_data: initData }),
    }),
  getMe: () => request<AuthResponse['user']>('/auth/me'),
  sharedSession: () => request<AuthResponse>('/auth/session'),
  telegramLogin: (data: TelegramLoginPayload) => request<AuthResponse>('/auth/telegram-login', {
    method: 'POST',
    body: JSON.stringify(data),
  }),
  logout: () => request<{ ok: boolean }>('/auth/logout', { method: 'POST' }),

  // PWA / Web Push
  getPushPublicKey: () =>
    request<{ public_key: string }>('/notifications/vapid-public-key'),
  subscribePush: (data: {
    endpoint: string;
    keys: { p256dh: string; auth: string };
    notify_token?: string;
  }) =>
    request<{ ok: boolean }>('/notifications/subscribe', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Client
  getBusinesses: () => request<Business[]>('/businesses'),
  getBusiness: (id: number) => request<BusinessDetail>(`/businesses/${id}`),
  getBusinessBlog: (id: number) => request<BusinessBlogFeed>(`/businesses/${id}/blog/articles`),
  getBusinessBlogArticle: (id: number, slug: string) => request<BusinessBlogArticle>(
    `/businesses/${id}/blog/articles/${encodeURIComponent(slug)}`),
  getSlots: async (
    bizId: number,
    serviceId: number,
    date: string,
    employeeId?: number,
    durationMinutes?: number
  ) => {
    const employeeQuery = employeeId ? `&employee_id=${employeeId}` : '';
    const durationQuery = durationMinutes ? `&duration_minutes=${durationMinutes}` : '';
    const data = await request<Array<{ time: string } | string>>(
      `/businesses/${bizId}/slots?service_id=${serviceId}&date=${date}${employeeQuery}${durationQuery}`
    );
    // Normalize: API returns [{time:"09:00"},...] but we need ["09:00",...]
    return data.map((s) => (typeof s === 'string' ? s : s.time));
  },
  getSlotAvailability: async (
    bizId: number,
    serviceId: number,
    date: string,
    employeeId?: number,
    durationMinutes?: number,
    excludeBookingId?: number
  ) => {
    const params = new URLSearchParams({
      service_id: String(serviceId),
      date,
      include_unavailable: 'true',
    });
    if (employeeId) params.set('employee_id', String(employeeId));
    if (durationMinutes) params.set('duration_minutes', String(durationMinutes));
    if (excludeBookingId) params.set('exclude_booking_id', String(excludeBookingId));
    return request<SlotAvailability[]>(`/businesses/${bizId}/slots?${params.toString()}`);
  },
  calculatePrice: (
    bizId: number,
    employeeId: number,
    time: string,
    duration: number,
    date?: string
  ) =>
    request<CalculatePriceResponse>(
      `/businesses/${bizId}/calculate-price?employee_id=${employeeId}&time=${time}&duration=${duration}${date ? '&date=' + date : ''}`
    ),
  createBooking: (data: CreateBookingData) =>
    request<Booking>('/bookings', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  rescheduleBooking: (id: number, date: string, time: string) =>
    request<{ success: boolean; message: string }>(`/bookings/${id}/reschedule`, {
      method: 'POST',
      body: JSON.stringify({ date, time }),
    }),
  getMyBookings: () => request<Booking[]>('/my/bookings'),
  cancelBooking: (id: number) =>
    request<{ message: string }>(`/bookings/${id}/cancel`, { method: 'POST' }),
  prepaymentSent: (bookingId: number) =>
    request<{ message: string }>(`/bookings/${bookingId}/prepayment-sent`, { method: 'POST' }),
  uploadReceipt: async (bookingId: number, file: File): Promise<{ message: string }> => {
    const formData = new FormData();
    formData.append('file', file);
    const token = localStorage.getItem('token');
    const tgInitData = window.Telegram?.WebApp?.initData || '';
    const res = await fetch(`${API}/bookings/${bookingId}/receipt`, {
      method: 'POST',
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(tgInitData ? { 'X-Telegram-Init-Data': tgInitData } : {}),
      },
      body: formData,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `HTTP ${res.status}`);
    }
    return res.json();
  },
  // QR/guest flow (no auth): identify the booking by its notify-token.
  getBookingByToken: (notifyToken: string) =>
    request<PublicBooking>(`/bookings/by-token/${notifyToken}`),
  prepaymentSentByToken: (notifyToken: string) =>
    request<{ message: string }>(`/bookings/by-token/${notifyToken}/prepayment-sent`, { method: 'POST' }),
  uploadReceiptByToken: async (notifyToken: string, file: File): Promise<{ message: string }> => {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch(`${API}/bookings/by-token/${notifyToken}/receipt`, {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `HTTP ${res.status}`);
    }
    return res.json();
  },

  // Subscriptions
  getSubscriptions: (businessId: number) =>
    request<SubscriptionPlan[]>(`/subscriptions?business_id=${businessId}`),
  getSubscriptionDetails: (id: number) =>
    request<SubscriptionPlan>(`/subscriptions/${id}`),
  buySubscription: (id: number) =>
    request<ClientSubscription>(`/subscriptions/${id}/buy`, { method: 'POST', body: JSON.stringify({}) }),
  getMySubscriptions: () =>
    request<ClientSubscription[]>('/my/subscriptions'),
  subscriptionPaymentSent: (clientSubId: number) =>
    request<{ message: string }>(`/my/subscriptions/${clientSubId}/payment-sent`, { method: 'POST' }),

  // Admin
  getDashboard: () => request<DashboardData>('/admin/dashboard'),
  getCRMContacts: (bizId: number, q = '', stage = 'all', page = 1) =>
    request<{ items: CRMContact[]; total: number; page: number; page_size: number }>(
      `/admin/businesses/${bizId}/contacts?${new URLSearchParams({ q, stage, page: String(page) })}`),
  getCRMContact: (bizId: number, key: string) =>
    request<CRMContactDetail>(`/admin/businesses/${bizId}/contacts/${encodeURIComponent(key)}`),
  createCRMContact: (bizId: number, data: CRMContactInput) =>
    request<CRMContact>(`/admin/businesses/${bizId}/contacts`, {
      method: 'POST', body: JSON.stringify(data),
    }),
  updateCRMContact: (bizId: number, key: string, data: CRMContactInput) =>
    request<CRMContactDetail>(`/admin/businesses/${bizId}/contacts/${encodeURIComponent(key)}`, {
      method: 'PATCH', body: JSON.stringify(data),
    }),
  deleteCRMContact: (bizId: number, key: string) =>
    request<{ ok: boolean }>(`/admin/businesses/${bizId}/contacts/${encodeURIComponent(key)}`, { method: 'DELETE' }),
  getAdminBookings: (params?: string) =>
    request<AdminBooking[]>(`/admin/bookings${params ? '?' + params : ''}`),
  createManualBooking: (data: ManualBookingData) =>
    request<AdminBooking[]>('/admin/bookings', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateBookingStatus: (bookingId: number, status: string) =>
    request<AdminBooking>(`/admin/bookings/${bookingId}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),
  updateRentalQuote: (bookingId: number, estimatedPrice: number, prepaymentAmount: number) =>
    request<AdminBooking>(`/admin/rental/bookings/${bookingId}/quote`, {
      method: 'PATCH',
      body: JSON.stringify({ estimated_price: estimatedPrice, prepayment_amount: prepaymentAmount }),
    }),
  changeBookingService: (bookingId: number, serviceId: number) =>
    request<AdminBooking>(`/admin/bookings/${bookingId}/service`, {
      method: 'PATCH',
      body: JSON.stringify({ service_id: serviceId }),
    }),
  adminReschedule: (bookingId: number, date: string, time: string, employeeId?: number) =>
    request<AdminBooking>(`/admin/bookings/${bookingId}/reschedule`, {
      method: 'POST',
      body: JSON.stringify({ date, time, employee_id: employeeId }),
    }),
  getServices: (bizId: number) =>
    request<Service[]>(`/admin/businesses/${bizId}/services`),
  updateService: (bizId: number, serviceId: number, data: Partial<Service>) =>
    request<Service>(`/admin/businesses/${bizId}/services/${serviceId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  createService: (bizId: number, data: Partial<Service>) =>
    request<Service>(`/admin/businesses/${bizId}/services`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  deleteService: (bizId: number, serviceId: number) =>
    request<void>(`/admin/businesses/${bizId}/services/${serviceId}`, {
      method: 'DELETE',
    }),
  getEmployees: (bizId: number, includeInactive: boolean = false) =>
    request<Employee[]>(
      `/admin/businesses/${bizId}/employees${includeInactive ? '?include_inactive=true' : ''}`
    ),
  createEmployee: (bizId: number, data: Partial<Employee>) =>
    request<Employee>(`/admin/businesses/${bizId}/employees`, { method: 'POST', body: JSON.stringify(data) }),
  updateEmployee: (bizId: number, empId: number, data: Partial<Employee>) =>
    request<Employee>(`/admin/businesses/${bizId}/employees/${empId}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteEmployee: (bizId: number, empId: number) =>
    request<void>(`/admin/businesses/${bizId}/employees/${empId}`, { method: 'DELETE' }),
  getAnalytics: (bizId: number, period?: string) =>
    request<AnalyticsData>(
      `/admin/analytics/${bizId}${period ? '?period=' + period : ''}`
    ),
  getAnalyticsOverview: (bizId: number, days: number) =>
    request<AnalyticsOverview>(`/admin/analytics/${bizId}/overview?days=${days}`),
  getAdminBusinesses: () =>
    request<Business[]>('/admin/businesses'),
  createBusiness: (data: BusinessCreateData) =>
    request<BusinessAdminDetail>('/admin/businesses', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  getBusinessDetail: (bizId: number) =>
    request<BusinessAdminDetail>(`/admin/businesses/${bizId}/detail`),
  studioBusinesses: () => request<{ id: number; name: string; role: string }[]>('/studio/businesses'),
  studioTicket: (businessId: number) =>
    request<{ url: string; expires_in: number }>('/studio/ticket', { method: 'POST', body: JSON.stringify({ business_id: businessId }) }),

  updateBusiness: (bizId: number, data: BusinessUpdateData) =>
    request<BusinessAdminDetail>(`/admin/businesses/${bizId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  // Service reorder
  reorderServices: (bizId: number, serviceIds: number[]) =>
    request<{ message: string }>(`/admin/businesses/${bizId}/services/reorder`, {
      method: 'POST',
      body: JSON.stringify({ service_ids: serviceIds }),
    }),

  // Employee-Service links
  getEmployeeServices: (bizId: number, empId: number) =>
    request<EmployeeServiceLink[]>(`/admin/businesses/${bizId}/employees/${empId}/services`),
  setEmployeeServices: (bizId: number, empId: number, serviceIds: number[]) =>
    request<{ message: string }>(`/admin/businesses/${bizId}/employees/${empId}/services`, {
      method: 'PUT',
      body: JSON.stringify({ service_ids: serviceIds }),
    }),

  // Venue time pricing
  getTimePricing: (bizId: number, empId: number) =>
    request<TimePricingAdmin[]>(`/admin/businesses/${bizId}/employees/${empId}/time-pricing`),
  addTimePricing: (bizId: number, empId: number, data: { time_from: string; time_to: string; price_per_hour: number; day_type?: string }) =>
    request<TimePricingAdmin>(`/admin/businesses/${bizId}/employees/${empId}/time-pricing`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  deleteTimePricing: (bizId: number, empId: number, pricingId: number) =>
    request<{ message: string }>(`/admin/businesses/${bizId}/employees/${empId}/time-pricing/${pricingId}`, {
      method: 'DELETE',
    }),

  // Duty toggle
  toggleDuty: (bizId: number, empId: number) =>
    request<{ on_duty: boolean; date: string }>(`/admin/businesses/${bizId}/employees/${empId}/toggle-duty`, {
      method: 'POST',
    }),

  // Co-owners
  // Кабинет мастера
  masterMe: () => request<{ employees: MasterEmployee[] }>('/master/me'),
  masterSchedule: (dateFrom?: string, dateTo?: string) => {
    const params = new URLSearchParams()
    if (dateFrom) params.set('date_from', dateFrom)
    if (dateTo) params.set('date_to', dateTo)
    const query = params.toString()
    return request<MasterSchedule>(`/master/schedule${query ? `?${query}` : ''}`)
  },

  masterFinishBooking: (bookingId: number) =>
    request<{ ok: boolean }>(`/master/bookings/${bookingId}/finish`, { method: 'POST' }),
  masterChangeService: (bookingId: number, serviceId: number) =>
    request<{ ok: boolean }>(`/master/bookings/${bookingId}/service`, {
      method: 'PATCH',
      body: JSON.stringify({ service_id: serviceId }),
    }),

  getOwners: (bizId: number) =>
    request<OwnerInfo[]>(`/admin/businesses/${bizId}/owners`),
  addOwner: (bizId: number, data: { telegram_id?: number; username?: string; email?: string }) =>
    request<OwnerInfo>(`/admin/businesses/${bizId}/owners`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  removeOwner: (bizId: number, ownerId: number) =>
    request<{ message: string }>(`/admin/businesses/${bizId}/owners/${ownerId}`, {
      method: 'DELETE',
    }),

  // Subscriptions admin
  getAdminSubscriptions: (bizId: number) =>
    request<SubscriptionAdmin[]>(`/admin/businesses/${bizId}/subscriptions`),
  createSubscription: (bizId: number, data: SubscriptionCreateData) =>
    request<SubscriptionAdmin>(`/admin/businesses/${bizId}/subscriptions`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateSubscription: (bizId: number, subId: number, data: Partial<SubscriptionCreateData> & { is_active?: boolean }) =>
    request<SubscriptionAdmin>(`/admin/businesses/${bizId}/subscriptions/${subId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  deleteSubscription: (bizId: number, subId: number) =>
    request<{ message: string }>(`/admin/businesses/${bizId}/subscriptions/${subId}`, {
      method: 'DELETE',
    }),

  // Duty schedule
  getDutySchedule: (bizId: number, empId: number) =>
    request<{ dates: string[] }>(`/admin/businesses/${bizId}/employees/${empId}/duty-schedule`),
  setDutySchedule: (bizId: number, empId: number, dates: string[]) =>
    request<{ dates: string[] }>(`/admin/businesses/${bizId}/employees/${empId}/duty-schedule`, {
      method: 'PUT',
      body: JSON.stringify({ dates }),
    }),

  // QR code
  getQrUrl: (bizId: number, target: 'webapp' | 'telegram') =>
    `/api/admin/businesses/${bizId}/qr?target=${target}`,

  // Business photo management
  uploadBusinessPhoto: async (bizId: number, file: File): Promise<{ file_id: string; photos: string[] }> => {
    const formData = new FormData();
    formData.append('file', file);
    const token = localStorage.getItem('token');
    const tgInitData = window.Telegram?.WebApp?.initData || '';
    const res = await fetch(`${API}/admin/businesses/${bizId}/photo`, {
      method: 'POST',
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(tgInitData ? { 'X-Telegram-Init-Data': tgInitData } : {}),
      },
      body: formData,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `HTTP ${res.status}`);
    }
    return res.json();
  },
  deleteBusinessPhoto: (bizId: number, photoIndex: number) =>
    request<{ photos: string[] }>(`/admin/businesses/${bizId}/photo/${photoIndex}`, { method: 'DELETE' }),

  // Employee photo management
  uploadEmployeePhoto: async (bizId: number, empId: number, file: File): Promise<{ file_id: string; photos: string[]; primary_photo?: string }> => {
    const formData = new FormData();
    formData.append('file', file);
    const token = localStorage.getItem('token');
    const tgInitData = window.Telegram?.WebApp?.initData || '';
    const res = await fetch(`${API}/admin/businesses/${bizId}/employees/${empId}/photo`, {
      method: 'POST',
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(tgInitData ? { 'X-Telegram-Init-Data': tgInitData } : {}),
      },
      body: formData,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `HTTP ${res.status}`);
    }
    return res.json();
  },
  deleteEmployeePhoto: (bizId: number, empId: number, photoIndex: number) =>
    request<{ photos: string[]; primary_photo?: string }>(`/admin/businesses/${bizId}/employees/${empId}/photo/${photoIndex}`, { method: 'DELETE' }),
  setEmployeePrimaryPhoto: (bizId: number, empId: number, fileId: string) =>
    request<{ photos: string[]; primary_photo: string }>(`/admin/businesses/${bizId}/employees/${empId}/primary-photo`, {
      method: 'PUT',
      body: JSON.stringify({ file_id: fileId }),
    }),

  // AI Chat
  aiChat: (message: string, businessId?: number) =>
    request<SalesChatResult>('/ai/chat', {
      method: 'POST',
      body: JSON.stringify({ message, business_id: businessId }),
    }),
  aiHistory: (businessId: number) => request<SalesHistory>(`/ai/history?business_id=${businessId}`),
  aiSelectSlot: (businessId: number, employeeId: number, time: string) =>
    request<SalesChatResult>('/ai/select-slot', {
      method: 'POST', body: JSON.stringify({ business_id: businessId, employee_id: employeeId, time }),
    }),
  aiHandoff: (businessId: number) => request<SalesChatResult>('/ai/handoff', {
    method: 'POST', body: JSON.stringify({ business_id: businessId }),
  }),
  aiConfirm: (actionId: string, details: SalesConfirmation = {}) =>
    request<SalesChatResult>('/ai/confirm', {
      method: 'POST',
      body: JSON.stringify({ action_id: actionId, ...details }),
    }),
  aiReject: (actionId: string) =>
    request<{ message: string }>('/ai/reject', {
      method: 'POST',
      body: JSON.stringify({ action_id: actionId }),
    }),
  salesInbox: (businessId: number) => request<SalesInboxThread[]>(`/admin/ai/businesses/${businessId}/conversations`),
  salesSettings: (businessId: number) => request<SalesSettings>(`/admin/ai/businesses/${businessId}/settings`),
  saveSalesSettings: (businessId: number, settings: SalesSettings) => request<SalesSettings>(`/admin/ai/businesses/${businessId}/settings`, {
    method: 'PUT', body: JSON.stringify({ expected_revision: settings.revision, policy: settings.policy }),
  }),
  salesReply: (conversationId: string, message: string) => request(`/admin/ai/conversations/${conversationId}/reply`, {
    method: 'POST', body: JSON.stringify({ message }),
  }),
  salesResume: (conversationId: string) => request(`/admin/ai/conversations/${conversationId}/resume`, { method: 'POST' }),
  rentalStatus: (token: string) => request<{
    id: number; status: string; date: string; time: string; duration_minutes: number;
    estimated_price: number | null; price_requires_confirmation: boolean; prepayment_amount: number | null;
  }>(`/rental/bookings/${token}`),
};
