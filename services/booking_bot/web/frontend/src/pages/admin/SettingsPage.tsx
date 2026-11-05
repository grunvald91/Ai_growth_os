import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import {
  Building2,
  Camera,
  CreditCard,
  Users,
  QrCode,
  Plus,
  Trash2,
  X,
  Download,
  Check,
  Loader2,
  DoorOpen,
  User as UserIcon,
  ImagePlus,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { api, type BusinessUpdateData, type OwnerInfo, type Employee } from '../../api/client'

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

async function fetchQrBlob(url: string): Promise<string> {
  const token = localStorage.getItem('token')
  const tgInitData = window.Telegram?.WebApp?.initData || ''
  const res = await fetch(url, {
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(tgInitData ? { 'X-Telegram-Init-Data': tgInitData } : {}),
    },
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const blob = await res.blob()
  return URL.createObjectURL(blob)
}
import { useAdminBusiness } from '../../hooks/useAdminBusiness'
import { BusinessSelector } from '../../components/BusinessSelector'
import { AdminShell } from '../../components/AdminShell'
import { NotificationSetup } from '../../components/NotificationSetup'
import { choosePhrase } from '../../utils/bookingLabels'

const REMINDER_PRESETS = [1440, 720, 360, 180, 120, 60, 30]

function reminderLabel(minutes: number) {
  if (minutes % 1440 === 0) return `${minutes / 1440} дн.`
  if (minutes % 60 === 0) return `${minutes / 60} ч.`
  return `${minutes} мин.`
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      style={{
        position: 'relative',
        display: 'inline-flex',
        height: 28,
        width: 48,
        flexShrink: 0,
        cursor: 'pointer',
        borderRadius: 999,
        border: 'none',
        transition: 'background 0.2s ease',
        background: checked ? 'var(--s-accent)' : 'var(--s-surface-3)',
      }}
    >
      <span
        style={{
          pointerEvents: 'none',
          display: 'inline-block',
          width: 20,
          height: 20,
          borderRadius: '50%',
          background: '#fff',
          boxShadow: '0 1px 4px rgba(0,0,0,0.25)',
          position: 'absolute',
          top: 4,
          left: checked ? 24 : 4,
          transition: 'left 0.2s ease',
        }}
      />
    </button>
  )
}

export function SettingsPage() {
  const { businesses, selectedId: businessId, selectBusiness } = useAdminBusiness()
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  // Business profile form
  const [form, setForm] = useState<BusinessUpdateData>({})
  // Перерыв включается отдельным переключателем: выключен — перерыва нет совсем (поля очищаются).
  const [breakEnabled, setBreakEnabled] = useState(false)
  const [ownerCustomMinutes, setOwnerCustomMinutes] = useState('')
  const [masterCustomMinutes, setMasterCustomMinutes] = useState('')
  const [photos, setPhotos] = useState<string[]>([])
  const [uploadingPhoto, setUploadingPhoto] = useState(false)

  // QR blobs
  const [qrTelegram, setQrTelegram] = useState<string | null>(null)
  const [qrWebapp, setQrWebapp] = useState<string | null>(null)

  const handleSetEmpPrimaryPhoto = async (empId: number, fileId: string) => {
    if (!businessId) return
    try {
      const res = await api.setEmployeePrimaryPhoto(businessId, empId, fileId)
      setEmployees((prev) =>
        prev.map((e) => (e.id === empId ? { ...e, primary_photo: res.primary_photo } : e))
      )
      showToast('Главное фото изменено')
    } catch (e) {
      alert('Ошибка выбора главного фото: ' + (e instanceof Error ? e.message : String(e)))
    }
  }

  // Owners
  const [owners, setOwners] = useState<OwnerInfo[]>([])
  const [ownerModalOpen, setOwnerModalOpen] = useState(false)
  const [ownerField, setOwnerField] = useState('')

  // Employees / rooms with photos
  const [employees, setEmployees] = useState<Employee[]>([])
  const [employeesLoading, setEmployeesLoading] = useState(false)
  const [employeesError, setEmployeesError] = useState<string | null>(null)
  const [empPhotoUploadingId, setEmpPhotoUploadingId] = useState<number | null>(null)
  const empFileInputs = useRef<Record<number, HTMLInputElement | null>>({})

  const selectedBusiness = businesses.find((b) => b.id === businessId)
  const isVenueRental = selectedBusiness?.business_type === 'venue_rental'

  const fileInputRef = useRef<HTMLInputElement>(null)
  const toastTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)

  const showToast = (msg: string) => {
    setToast(msg)
    if (toastTimeout.current) clearTimeout(toastTimeout.current)
    toastTimeout.current = setTimeout(() => setToast(null), 3000)
  }

  // Load employees / rooms for the "Фото сотрудников/залов" section.
  // include_inactive=true so we can manage photos even for disabled employees/rooms.
  useEffect(() => {
    if (!businessId) {
      setEmployees([])
      setEmployeesError(null)
      return
    }
    setEmployeesLoading(true)
    setEmployeesError(null)
    api
      .getEmployees(businessId, true)
      .then((list) => {
        setEmployees(list)
        setEmployeesError(null)
      })
      .catch((err) => {
        setEmployees([])
        setEmployeesError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => setEmployeesLoading(false))
  }, [businessId])

  // Load business detail
  useEffect(() => {
    if (!businessId) return
    setLoading(true)
    Promise.all([
      api.getBusinessDetail(businessId),
      api.getOwners(businessId).catch(() => [] as OwnerInfo[]),
    ])
      .then(async ([biz, ownersList]) => {
        setBreakEnabled(!!(biz.break_start && biz.break_end && biz.break_start !== biz.break_end))
        setForm({
          name: biz.name || '',
          description: biz.description || '',
          address: biz.address || '',
          phone: biz.phone || '',
          working_hours_start: biz.working_hours_start || '',
          working_hours_end: biz.working_hours_end || '',
          break_start: biz.break_start || '',
          break_end: biz.break_end || '',
          booking_interval_minutes: biz.booking_interval_minutes ?? undefined,
          directions: biz.directions || '',
          prepayment_enabled: biz.prepayment_enabled ?? false,
          prepayment_percent: biz.prepayment_percent ?? undefined,
          payment_phone: biz.payment_phone || '',
          payment_details: biz.payment_details || '',
          cancellation_policy_text: biz.cancellation_policy_text || '',
          require_receipt_for_prepayment: biz.require_receipt_for_prepayment ?? false,
          legal_name: biz.legal_name || '',
          legal_inn: biz.legal_inn || '',
          legal_ogrn: biz.legal_ogrn || '',
          legal_address: biz.legal_address || '',
          legal_email: biz.legal_email || '',
          legal_documents_enabled: biz.legal_documents_enabled ?? false,
          studio_admin_access: biz.studio_admin_access ?? false,
          booking_labels: biz.booking_labels || {},
          booking_flow_order: biz.booking_flow_order || 'service_first',
          owner_reminders_enabled: biz.owner_reminders_enabled ?? true,
          owner_reminder_minutes: biz.owner_reminder_minutes?.length ? biz.owner_reminder_minutes : [1440, 120],
          master_reminders_enabled: biz.master_reminders_enabled ?? true,
          master_reminder_minutes: biz.master_reminder_minutes?.length ? biz.master_reminder_minutes : [1440, 120],
        })
        try {
          setPhotos(biz.photos ? JSON.parse(biz.photos) : [])
        } catch {
          setPhotos([])
        }
        setOwners(ownersList)
        // Load QR codes with auth
        try {
          const [tg, wa] = await Promise.all([
            fetchQrBlob(api.getQrUrl(businessId, 'telegram')),
            fetchQrBlob(api.getQrUrl(businessId, 'webapp')),
          ])
          setQrTelegram(tg)
          setQrWebapp(wa)
        } catch {
          setQrTelegram(null)
          setQrWebapp(null)
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [businessId])

  const updateField = <K extends keyof BusinessUpdateData>(key: K, value: BusinessUpdateData[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  // Save business profile
  const handleSaveProfile = async () => {
    if (!businessId) return
    if (breakEnabled) {
      const from = (form.break_start || '').slice(0, 5)
      const to = (form.break_end || '').slice(0, 5)
      if (!from || !to) { alert('Укажите, с какого и до какого времени перерыв, или выключите переключатель «Есть перерыв».'); return }
      if (to <= from) { alert('Конец перерыва должен быть позже начала. Если перерыва нет, выключите переключатель «Есть перерыв».'); return }
    }
    setSaving(true)
    try {
      const updated = await api.updateBusiness(businessId, {
        name: form.name,
        description: form.description,
        address: form.address,
        phone: form.phone,
        working_hours_start: form.working_hours_start,
        working_hours_end: form.working_hours_end,
        break_start: breakEnabled ? form.break_start : '',
        break_end: breakEnabled ? form.break_end : '',
        booking_interval_minutes: form.booking_interval_minutes,
        directions: form.directions,
      })
      setForm((prev) => ({
        ...prev,
        name: updated.name,
        break_start: breakEnabled ? prev.break_start : '',
        break_end: breakEnabled ? prev.break_end : '',
      }))
      showToast('Профиль сохранён')
    } catch (e) {
      alert('Ошибка при сохранении: ' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setSaving(false)
    }
  }

  const handleSaveBookingFlow = async () => {
    if (!businessId) return
    setSaving(true)
    try {
      const updated = await api.updateBusiness(businessId, {
        booking_labels: form.booking_labels || {},
        booking_flow_order: form.booking_flow_order || 'service_first',
      })
      setForm((prev) => ({
        ...prev,
        booking_labels: updated.booking_labels || {},
        booking_flow_order: updated.booking_flow_order || 'service_first',
      }))
      showToast('Сценарий записи сохранён')
    } catch (e) {
      alert('Ошибка при сохранении сценария: ' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setSaving(false)
    }
  }

  // Включатель доступа администраторов в студию: сохраняется сразу, при ошибке возвращается назад.
  const handleStudioAccess = async (value: boolean) => {
    if (!businessId) return
    const before = form.studio_admin_access ?? false
    setForm((prev) => ({ ...prev, studio_admin_access: value }))
    try {
      const updated = await api.updateBusiness(businessId, { studio_admin_access: value })
      setForm((prev) => ({ ...prev, studio_admin_access: updated.studio_admin_access ?? value }))
      showToast(value ? 'Администраторы допущены в маркетинг' : 'Доступ администраторов закрыт')
    } catch (e) {
      setForm((prev) => ({ ...prev, studio_admin_access: before }))
      alert('Не удалось сохранить: ' + (e instanceof Error ? e.message : String(e)))
    }
  }

  // Save legal operator details and document switch
  const handleSaveLegal = async () => {
    if (!businessId) return
    setSaving(true)
    try {
      const updated = await api.updateBusiness(businessId, {
        legal_name: form.legal_name,
        legal_inn: form.legal_inn,
        legal_ogrn: form.legal_ogrn,
        legal_address: form.legal_address,
        legal_email: form.legal_email,
        legal_documents_enabled: form.legal_documents_enabled,
      })
      setForm((prev) => ({
        ...prev,
        legal_name: updated.legal_name || '',
        legal_inn: updated.legal_inn || '',
        legal_ogrn: updated.legal_ogrn || '',
        legal_address: updated.legal_address || '',
        legal_email: updated.legal_email || '',
        legal_documents_enabled: updated.legal_documents_enabled ?? false,
      }))
      showToast('Юридические данные сохранены')
    } catch (e) {
      alert('Ошибка при сохранении юридических данных: ' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setSaving(false)
    }
  }

  const toggleReminderOffset = (field: 'owner_reminder_minutes' | 'master_reminder_minutes', minutes: number) => {
    const current = form[field] || []
    const next = current.includes(minutes)
      ? current.filter((value) => value !== minutes)
      : [...current, minutes]
    if (next.length === 0) return
    updateField(field, [...new Set(next)].sort((a, b) => b - a).slice(0, 6))
  }

  const addCustomReminder = (
    field: 'owner_reminder_minutes' | 'master_reminder_minutes',
    raw: string,
    clear: () => void,
  ) => {
    const minutes = Number(raw)
    if (!Number.isInteger(minutes) || minutes < 5 || minutes > 10080) {
      alert('Укажите от 5 до 10080 минут')
      return
    }
    const next = [...new Set([...(form[field] || []), minutes])].sort((a, b) => b - a).slice(0, 6)
    updateField(field, next)
    clear()
  }

  const handleSaveReminders = async () => {
    if (!businessId) return
    setSaving(true)
    try {
      const updated = await api.updateBusiness(businessId, {
        owner_reminders_enabled: form.owner_reminders_enabled ?? true,
        owner_reminder_minutes: form.owner_reminder_minutes?.length ? form.owner_reminder_minutes : [1440, 120],
        master_reminders_enabled: form.master_reminders_enabled ?? true,
        master_reminder_minutes: form.master_reminder_minutes?.length ? form.master_reminder_minutes : [1440, 120],
      })
      setForm((prev) => ({
        ...prev,
        owner_reminders_enabled: updated.owner_reminders_enabled,
        owner_reminder_minutes: updated.owner_reminder_minutes,
        master_reminders_enabled: updated.master_reminders_enabled,
        master_reminder_minutes: updated.master_reminder_minutes,
      }))
      showToast('Настройки уведомлений сохранены')
    } catch (e) {
      alert('Ошибка сохранения уведомлений: ' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setSaving(false)
    }
  }

  // Save prepayment settings
  const handleSavePrepayment = async () => {
    if (!businessId) return
    setSaving(true)
    try {
      await api.updateBusiness(businessId, {
        prepayment_enabled: form.prepayment_enabled,
        prepayment_percent: form.prepayment_percent,
        payment_phone: form.payment_phone,
        payment_details: form.payment_details,
        cancellation_policy_text: form.cancellation_policy_text,
        require_receipt_for_prepayment: form.require_receipt_for_prepayment,
      })
      showToast('Настройки предоплаты сохранены')
    } catch (e) {
      alert('Ошибка при сохранении: ' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setSaving(false)
    }
  }

  // Photo upload
  const handlePhotoUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file || !businessId) return
    setUploadingPhoto(true)
    try {
      const result = await api.uploadBusinessPhoto(businessId, file)
      setPhotos(result.photos)
      showToast('Фото загружено')
    } catch (err) {
      alert('Ошибка загрузки фото: ' + (err instanceof Error ? err.message : String(err)))
    } finally {
      setUploadingPhoto(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handlePhotoDelete = async (index: number) => {
    if (!businessId || !confirm('Удалить фото?')) return
    try {
      const result = await api.deleteBusinessPhoto(businessId, index)
      setPhotos(result.photos)
      showToast('Фото удалено')
    } catch (e) {
      alert('Ошибка удаления фото: ' + (e instanceof Error ? e.message : String(e)))
    }
  }

  // Per-employee/room photo upload
  const handleEmpPhotoUpload = async (empId: number, file: File) => {
    if (!businessId) return
    setEmpPhotoUploadingId(empId)
    try {
      const res = await api.uploadEmployeePhoto(businessId, empId, file)
      setEmployees((prev) =>
        prev.map((e) =>
          e.id === empId
            ? { ...e, photos: JSON.stringify(res.photos), primary_photo: res.primary_photo ?? e.primary_photo }
            : e
        )
      )
      showToast('Фото загружено')
    } catch (err) {
      alert('Ошибка загрузки фото: ' + (err instanceof Error ? err.message : String(err)))
    } finally {
      setEmpPhotoUploadingId(null)
      const input = empFileInputs.current[empId]
      if (input) input.value = ''
    }
  }

  const handleEmpPhotoDelete = async (empId: number, index: number) => {
    if (!businessId || !confirm('Удалить фото?')) return
    try {
      const res = await api.deleteEmployeePhoto(businessId, empId, index)
      setEmployees((prev) =>
        prev.map((e) =>
          e.id === empId
            ? {
                ...e,
                photos: res.photos.length > 0 ? JSON.stringify(res.photos) : undefined,
                primary_photo: res.primary_photo,
              }
            : e
        )
      )
      showToast('Фото удалено')
    } catch (e) {
      alert('Ошибка удаления фото: ' + (e instanceof Error ? e.message : String(e)))
    }
  }

  // Owners
  const handleAddOwner = async () => {
    if (!businessId || !ownerField.trim()) return
    const data: { telegram_id?: number; username?: string; email?: string } = {}
    const trimmed = ownerField.trim()
    if (trimmed.includes('@') && !trimmed.startsWith('@')) {
      data.email = trimmed
    } else if (/^\d+$/.test(trimmed)) {
      data.telegram_id = Number(trimmed)
    } else {
      data.username = trimmed.replace(/^@/, '')
    }
    try {
      const owner = await api.addOwner(businessId, data)
      setOwners((prev) => [...prev, owner])
      setOwnerField('')
      setOwnerModalOpen(false)
      showToast('Совладелец добавлен')
    } catch (e) {
      alert('Ошибка: ' + (e instanceof Error ? e.message : String(e)))
    }
  }

  const handleRemoveOwner = async (ownerId: number) => {
    if (!businessId || !confirm('Удалить совладельца?')) return
    try {
      await api.removeOwner(businessId, ownerId)
      setOwners((prev) => prev.filter((o) => o.id !== ownerId))
      showToast('Совладелец удалён')
    } catch (e) {
      alert('Ошибка: ' + (e instanceof Error ? e.message : String(e)))
    }
  }

  if (loading) {
    return (
      <AdminShell
        title="Настройки"
        headerExtra={<BusinessSelector businesses={businesses} selectedId={businessId} onSelect={selectBusiness} />}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, paddingTop: 4 }}>
          <div className="skeleton" style={{ height: 32, width: 192, borderRadius: 8 }} />
          {[1, 2, 3].map((i) => (
            <div key={i} className="skeleton" style={{ height: 128, borderRadius: 14 }} />
          ))}
        </div>
      </AdminShell>
    )
  }

  return (
    <AdminShell
      title="Настройки"
      headerExtra={<BusinessSelector businesses={businesses} selectedId={businessId} onSelect={selectBusiness} />}
    >
      <section className="ds-card" style={{ padding: 14, marginBottom: 12 }}><h2 className="ds-h3">Вход без Telegram</h2><p>Привяжите подтверждённую почту к своему аккаунту.</p><Link to="/account" className="ds-btn ds-btn--primary">Настроить вход по email</Link></section>
      {/* Success toast */}
      {toast && (
        <div
          style={{
            position: 'fixed',
            top: 16,
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 80,
            background: 'var(--s-success)',
            color: '#fff',
            padding: '10px 18px',
            borderRadius: 14,
            boxShadow: 'var(--sh-pop)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 14,
            fontWeight: 600,
            whiteSpace: 'nowrap',
          }}
        >
          <Check size={16} />
          {toast}
        </div>
      )}

      <div className="ds-context">
        <div className="ds-context-eyebrow">Ваш бизнес</div>
        <div className="ds-context-headline">{form.name || businesses.find((b) => b.id === businessId)?.name || 'Профиль'}</div>
        <div className="ds-context-strip">
          {form.working_hours_start && form.working_hours_end && (
            <div><strong>{String(form.working_hours_start).slice(0, 5)}–{String(form.working_hours_end).slice(0, 5)}</strong><span>график работы</span></div>
          )}
          {form.booking_interval_minutes ? <div><strong>{form.booking_interval_minutes} мин</strong><span>шаг записи</span></div> : null}
          {form.prepayment_enabled && <div><strong>{form.prepayment_percent ?? 100}%</strong><span>предоплата</span></div>}
        </div>
      </div>

      <div className="ds-form-narrow" style={{ display: 'flex', flexDirection: 'column', gap: 4, paddingTop: 0 }}>
        <section style={{ padding: '10px 2px 14px', borderBottom: '1px solid var(--s-line)' }}>
          <h2 className="ds-h3" style={{ marginBottom: 6 }}>
            Уведомления о записях
          </h2>
          <p className="ds-sub" style={{ marginBottom: 10, lineHeight: 1.5 }}>
            Новая подтверждённая запись всегда отправляется клиенту и причастным сотрудникам. Ниже настраиваются только дополнительные напоминания перед визитом.
          </p>
          <NotificationSetup staff />

          {([
            {
              title: 'Владельцы и совладельцы',
              enabledField: 'owner_reminders_enabled' as const,
              minutesField: 'owner_reminder_minutes' as const,
              custom: ownerCustomMinutes,
              setCustom: setOwnerCustomMinutes,
            },
            {
              title: 'Мастера / ответственные',
              enabledField: 'master_reminders_enabled' as const,
              minutesField: 'master_reminder_minutes' as const,
              custom: masterCustomMinutes,
              setCustom: setMasterCustomMinutes,
            },
          ]).map((group) => (
            <div key={group.title} style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--s-border)' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                <div>
                  <div className="ds-label">{group.title}</div>
                  <div className="ds-caption">Telegram и PWA, если уведомления разрешены на устройстве</div>
                </div>
                <Toggle
                  checked={form[group.enabledField] ?? true}
                  onChange={(value) => updateField(group.enabledField, value)}
                />
              </div>

              {(form[group.enabledField] ?? true) && (
                <>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginTop: 10 }}>
                    {REMINDER_PRESETS.map((minutes) => {
                      const selected = (form[group.minutesField] || []).includes(minutes)
                      return (
                        <button
                          key={minutes}
                          type="button"
                          className={selected ? 'ds-btn ds-btn--on choice-accent selected' : 'ds-btn'}
                          onClick={() => toggleReminderOffset(group.minutesField, minutes)}
                          style={{ minHeight: 34, padding: '6px 10px' }}
                        >
                          {reminderLabel(minutes)}
                        </button>
                      )
                    })}
                    {(form[group.minutesField] || [])
                      .filter((minutes) => !REMINDER_PRESETS.includes(minutes))
                      .map((minutes) => (
                        <button
                          key={minutes}
                          type="button"
                          className="ds-btn ds-btn--on choice-accent selected"
                          onClick={() => toggleReminderOffset(group.minutesField, minutes)}
                          style={{ minHeight: 34, padding: '6px 10px' }}
                        >
                          {reminderLabel(minutes)} ×
                        </button>
                      ))}
                  </div>
                  <div style={{ display: 'flex', gap: 8, marginTop: 9 }}>
                    <input
                      type="number"
                      min={5}
                      max={10080}
                      value={group.custom}
                      onChange={(e) => group.setCustom(e.target.value)}
                      placeholder="Своё время, мин"
                      className="ds-input"
                    />
                    <button
                      type="button"
                      className="ds-btn"
                      onClick={() => addCustomReminder(group.minutesField, group.custom, () => group.setCustom(''))}
                    >
                      Добавить
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}

          <button
            onClick={handleSaveReminders}
            disabled={saving}
            className="ds-btn ds-btn--primary ds-btn--block"
            style={{ marginTop: 14 }}
          >
            {saving ? 'Сохранение...' : 'Сохранить уведомления'}
          </button>
        </section>

        {/* === 1. Business Profile === */}
        <section style={{ padding: '10px 2px 14px', borderBottom: '1px solid var(--s-line)' }}>
          <h2 className="ds-h3" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
            <Building2 size={18} style={{ color: 'var(--s-accent)' }} />
            Профиль бизнеса
          </h2>

          <div className="ds-field">
            <label className="ds-label">Название</label>
            <input
              type="text"
              value={form.name || ''}
              onChange={(e) => updateField('name', e.target.value)}
              className="ds-input"
            />
          </div>

          <div className="ds-field">
            <label className="ds-label">Описание</label>
            <textarea
              value={form.description || ''}
              onChange={(e) => updateField('description', e.target.value)}
              rows={3}
              className="ds-input"
            />
          </div>

          <div className="ds-field">
            <label className="ds-label">Адрес</label>
            <input
              type="text"
              value={form.address || ''}
              onChange={(e) => updateField('address', e.target.value)}
              className="ds-input"
            />
          </div>

          <div className="ds-field">
            <label className="ds-label">Телефон</label>
            <input
              type="text"
              value={form.phone || ''}
              onChange={(e) => updateField('phone', e.target.value)}
              placeholder="+7 (900) 123-45-67"
              className="ds-input"
            />
          </div>

          <div className="ds-field">
            <label className="ds-label">Время работы</label>
            <div className="ds-field-row" style={{ alignItems: 'center' }}>
              <span className="ds-sub" style={{ flex: 'none' }}>с</span>
              <input
                type="time"
                value={form.working_hours_start || ''}
                onChange={(e) => updateField('working_hours_start', e.target.value)}
                className="ds-input"
              />
              <span className="ds-sub" style={{ flex: 'none' }}>до</span>
              <input
                type="time"
                value={form.working_hours_end || ''}
                onChange={(e) => updateField('working_hours_end', e.target.value)}
                className="ds-input"
              />
            </div>
          </div>

          <div className="ds-toggle-row">
            <div>
              <div className="t1">Есть перерыв</div>
              <div className="t2">{breakEnabled ? 'В это время клиенты записаться не смогут' : 'Выключено: записаться можно в любое рабочее время'}</div>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={breakEnabled}
              aria-label="Есть перерыв"
              className={`ds-toggle ${breakEnabled ? 'on' : ''}`}
              onClick={() => setBreakEnabled((v) => !v)}
            />
          </div>
          {breakEnabled && (
            <div className="ds-field">
              <label className="ds-label">Время перерыва</label>
              <div className="ds-field-row" style={{ alignItems: 'center' }}>
                <span className="ds-sub" style={{ flex: 'none' }}>с</span>
                <input
                  type="time"
                  value={form.break_start || ''}
                  onChange={(e) => updateField('break_start', e.target.value)}
                  className="ds-input"
                />
                <span className="ds-sub" style={{ flex: 'none' }}>до</span>
                <input
                  type="time"
                  value={form.break_end || ''}
                  onChange={(e) => updateField('break_end', e.target.value)}
                  className="ds-input"
                />
              </div>
            </div>
          )}

          <div className="ds-field">
            <label className="ds-label">Интервал записи</label>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="number"
                value={form.booking_interval_minutes ?? ''}
                onChange={(e) =>
                  updateField('booking_interval_minutes', e.target.value ? Number(e.target.value) : undefined)
                }
                placeholder="30"
                className="ds-input"
                style={{ width: 96 }}
              />
              <span className="ds-sub">мин</span>
            </div>
          </div>

          <div className="ds-field" style={{ marginBottom: 0 }}>
            <label className="ds-label">Как добраться</label>
            <textarea
              value={form.directions || ''}
              onChange={(e) => updateField('directions', e.target.value)}
              rows={3}
              className="ds-input"
            />
          </div>

          <button
            onClick={handleSaveProfile}
            disabled={saving}
            className="ds-btn ds-btn--primary ds-btn--block"
            style={{ marginTop: 14 }}
          >
            {saving ? 'Сохранение...' : 'Сохранить профиль'}
          </button>
        </section>

        {/* === 2. Booking flow === */}
        <section style={{ padding: '10px 2px 14px', borderBottom: '1px solid var(--s-line)' }}>
          <h2 className="ds-h3" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <DoorOpen size={18} style={{ color: 'var(--s-accent)' }} />
            Сценарий записи
          </h2>
          <p className="ds-sub" style={{ marginBottom: 14 }}>
            Здесь настраиваются только названия шагов записи на сайте. Имена мастеров и названия услуг сюда
            писать не нужно: их вы добавляете в разделах «Сотрудники» и «Услуги». Оставьте поле пустым,
            и клиент увидит стандартное название.
          </p>

          {([
            {
              key: 'resource',
              placeholder: isVenueRental ? 'Зал' : 'Мастер',
              label: isVenueRental ? 'Как называется зал или помещение' : 'Как называется мастер (сотрудник)',
              hint: isVenueRental
                ? 'Одно слово: «Зал», «Яхта», «Кабинет». Клиент увидит его на кнопке выбора и в заголовке шага.'
                : 'Одно слово: «Мастер», «Специалист», «Врач». Клиент увидит его на кнопке выбора и в заголовке шага. Это не имя человека.',
              preview: (v: string) => choosePhrase('Выбрать', v || (isVenueRental ? 'Зал' : 'Мастер')),
            },
            {
              key: 'service',
              placeholder: 'Услуга',
              label: 'Как называется услуга',
              hint: 'Одно слово: «Услуга», «Процедура», «Занятие». Клиент увидит его на кнопке выбора и в заголовке шага. Это не название конкретной услуги.',
              preview: (v: string) => choosePhrase('Выбрать', v || 'Услуга'),
            },
            {
              key: 'additional_services',
              placeholder: 'Доп. услуги',
              label: 'Как называются дополнительные услуги',
              hint: 'Заголовок шага с допами после выбора времени. Показывается, только если у вас есть дополнительные услуги.',
            },
            {
              key: 'date',
              placeholder: 'Дата',
              label: 'Как называется дата',
              hint: 'Первое слово заголовка шага с календарём: «Дата и время».',
            },
            {
              key: 'time',
              placeholder: 'Время',
              label: 'Как называется время',
              hint: 'Второе слово заголовка шага с календарём и подпись над кнопками времени.',
            },
          ] as const).map((field) => (
            <div className="ds-field" key={field.key}>
              <label className="ds-label" htmlFor={`booking-label-${field.key}`}>{field.label}</label>
              <input
                id={`booking-label-${field.key}`}
                type="text"
                maxLength={80}
                value={form.booking_labels?.[field.key] || ''}
                placeholder={field.placeholder}
                onChange={(e) =>
                  updateField('booking_labels', {
                    ...(form.booking_labels || {}),
                    [field.key]: e.target.value,
                  })
                }
                className="ds-input"
              />
              <p className="ds-hint">{field.hint}</p>
              {'preview' in field && (
                <p className="ds-hint">
                  Клиент увидит: <strong>{field.preview(form.booking_labels?.[field.key]?.trim() || '')}</strong>
                </p>
              )}
            </div>
          ))}

          <div className="ds-field" style={{ marginBottom: 0 }}>
            <label className="ds-label">Что клиент выбирает сначала</label>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              {([
                ['service_first', 'Сначала услугу'],
                ['resource_first', isVenueRental ? 'Сначала зал / яхту' : 'Сначала мастера'],
              ] as const).map(([value, label]) => {
                const selected = (form.booking_flow_order || 'service_first') === value
                return (
                  <button
                    key={value}
                    type="button"
                    onClick={() => updateField('booking_flow_order', value)}
                    className={selected ? 'ds-btn ds-btn--on choice-accent selected' : 'ds-btn'}
                    aria-pressed={selected}
                    style={{ minHeight: 42 }}
                  >
                    {label}
                  </button>
                )
              })}
            </div>
            <p className="ds-hint">
              {isVenueRental
                ? 'Порядок шагов у клиента: сначала услуга, потом зал, либо наоборот.'
                : 'Порядок шагов у клиента. «Сначала услугу»: клиент выбирает услугу, а мастер подставляется сам, если её делает один человек. «Сначала мастера»: клиент выбирает мастера и видит только его услуги.'}
            </p>
          </div>

          <button
            onClick={handleSaveBookingFlow}
            disabled={saving}
            className="ds-btn ds-btn--primary ds-btn--block"
            style={{ marginTop: 14 }}
          >
            {saving ? 'Сохранение...' : 'Сохранить сценарий'}
          </button>
        </section>

        {/* === 3. Photos === */}
        <section style={{ padding: '10px 2px 14px', borderBottom: '1px solid var(--s-line)' }}>
          <h2 className="ds-h3" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <Camera size={18} style={{ color: 'var(--s-accent)' }} />
            Фотографии
          </h2>

          <p className="ds-caption" style={{ marginBottom: 12 }}>
            Первое фото будет показано на главной странице
          </p>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
            {photos.map((fileId, index) => (
              <div
                key={index}
                style={{
                  position: 'relative',
                  aspectRatio: '1',
                  borderRadius: 10,
                  overflow: 'hidden',
                  background: 'var(--s-surface-2)',
                }}
              >
                <img
                  src={`/api/telegram-photo/${encodeURIComponent(fileId)}`}
                  alt={`Фото ${index + 1}`}
                  style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                />
                <button
                  onClick={() => handlePhotoDelete(index)}
                  style={{
                    position: 'absolute',
                    top: 6,
                    right: 6,
                    padding: 4,
                    borderRadius: '50%',
                    background: 'rgba(0,0,0,0.55)',
                    border: 'none',
                    color: '#fff',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <Trash2 size={13} />
                </button>
                {index === 0 && (
                  <span
                    style={{
                      position: 'absolute',
                      bottom: 6,
                      left: 6,
                      fontSize: 10,
                      background: 'var(--s-accent)',
                      color: 'var(--s-accent-ink)',
                      padding: '2px 6px',
                      borderRadius: 6,
                      fontWeight: 700,
                    }}
                  >
                    Главная
                  </span>
                )}
              </div>
            ))}

            {/* Upload button */}
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadingPhoto}
              style={{
                aspectRatio: '1',
                borderRadius: 10,
                border: '2px dashed var(--s-line-2)',
                background: 'transparent',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 4,
                color: 'var(--s-muted)',
                cursor: 'pointer',
                fontSize: 11,
                fontWeight: 600,
                opacity: uploadingPhoto ? 0.5 : 1,
              }}
            >
              <Plus size={22} />
              {uploadingPhoto ? 'Загрузка...' : 'Добавить'}
            </button>
          </div>

          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            onChange={handlePhotoUpload}
            style={{ display: 'none' }}
          />
        </section>

        {/* === 2.5. Employee / Room Photos === */}
        <section style={{ padding: '10px 2px 14px', borderBottom: '1px solid var(--s-line)' }}>
          <h2 className="ds-h3" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            {isVenueRental ? <DoorOpen size={18} style={{ color: 'var(--s-accent)' }} /> : <UserIcon size={18} style={{ color: 'var(--s-accent)' }} />}
            {isVenueRental ? 'Фото залов' : 'Фото сотрудников'}
          </h2>
          <p className="ds-caption" style={{ marginBottom: 14 }}>
            Прикрепите фото для каждого {isVenueRental ? 'зала' : 'сотрудника'} — оно появится в карточке при выборе клиентом.
          </p>

          {employeesLoading ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {[1, 2].map((i) => (
                <div key={i} className="skeleton" style={{ height: 96, borderRadius: 10 }} />
              ))}
            </div>
          ) : employeesError ? (
            <div className="ds-error">
              Не удалось загрузить список: {employeesError}
            </div>
          ) : employees.length === 0 ? (
            <div
              style={{
                background: 'var(--s-surface-2)',
                borderRadius: 10,
                padding: 12,
                fontSize: 13,
                color: 'var(--s-muted)',
              }}
            >
              В бизнесе{' '}
              <span style={{ fontWeight: 600, color: 'var(--s-text)' }}>
                «{selectedBusiness?.name ?? '—'}»
              </span>{' '}
              пока нет {isVenueRental ? 'залов' : 'сотрудников'}. Добавьте их в разделе{' '}
              <a href="/admin/employees" style={{ color: 'var(--s-accent)' }}>Сотрудники</a>{' '}
              или через бота. Если вы уверены, что они есть — проверьте, что выбран правильный бизнес выше.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {employees.map((emp) => {
                const empPhotos = parsePhotos(emp.photos)
                const isUploading = empPhotoUploadingId === emp.id
                return (
                  <div
                    key={emp.id}
                    style={{
                      border: '1px solid var(--s-line)',
                      borderRadius: 10,
                      padding: 10,
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                      <div style={{ minWidth: 0 }}>
                        <p className="ds-text" style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {emp.name}
                        </p>
                        {emp.specialization && (
                          <p className="ds-caption" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {emp.specialization}
                          </p>
                        )}
                      </div>
                      <span className="ds-caption" style={{ flexShrink: 0, marginLeft: 8 }}>
                        {empPhotos.length} фото
                      </span>
                    </div>

                    <input
                      ref={(el) => {
                        empFileInputs.current[emp.id] = el
                      }}
                      type="file"
                      accept="image/*"
                      style={{ display: 'none' }}
                      onChange={(e) => {
                        const f = e.target.files?.[0]
                        if (f) handleEmpPhotoUpload(emp.id, f)
                      }}
                    />

                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
                      {empPhotos.map((fid, idx) => (
                        <div
                          key={`${fid}-${idx}`}
                          style={{
                            position: 'relative',
                            aspectRatio: '1',
                            borderRadius: 8,
                            overflow: 'hidden',
                            background: 'var(--s-surface-2)',
                            outline: emp.primary_photo === fid ? '2px solid var(--s-accent)' : 'none',
                            outlineOffset: emp.primary_photo === fid ? 1 : 0,
                          }}
                        >
                          <img
                            src={`/api/telegram-photo/${encodeURIComponent(fid)}`}
                            alt={`${emp.name} ${idx + 1}`}
                            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                            loading="lazy"
                          />
                          {emp.primary_photo === fid ? (
                            <span
                              style={{
                                position: 'absolute',
                                left: 4,
                                bottom: 4,
                                padding: '3px 6px',
                                borderRadius: 999,
                                background: 'var(--s-accent)',
                                color: '#fff',
                                fontSize: 9,
                                fontWeight: 700,
                              }}
                            >
                              Главное
                            </span>
                          ) : (
                            <button
                              type="button"
                              onClick={() => handleSetEmpPrimaryPhoto(emp.id, fid)}
                              style={{
                                position: 'absolute',
                                left: 4,
                                bottom: 4,
                                padding: '3px 6px',
                                borderRadius: 999,
                                border: 'none',
                                background: 'rgba(0,0,0,0.65)',
                                color: '#fff',
                                fontSize: 9,
                                fontWeight: 700,
                                cursor: 'pointer',
                              }}
                            >
                              Сделать главной
                            </button>
                          )}
                          <button
                            onClick={() => handleEmpPhotoDelete(emp.id, idx)}
                            aria-label="Удалить"
                            style={{
                              position: 'absolute',
                              top: 4,
                              right: 4,
                              padding: 3,
                              borderRadius: '50%',
                              background: 'rgba(0,0,0,0.6)',
                              border: 'none',
                              color: '#fff',
                              cursor: 'pointer',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                            }}
                          >
                            <Trash2 size={11} />
                          </button>
                        </div>
                      ))}

                      <button
                        onClick={() => empFileInputs.current[emp.id]?.click()}
                        disabled={isUploading}
                        style={{
                          aspectRatio: '1',
                          borderRadius: 8,
                          border: '2px dashed var(--s-line-2)',
                          background: 'transparent',
                          display: 'flex',
                          flexDirection: 'column',
                          alignItems: 'center',
                          justifyContent: 'center',
                          gap: 3,
                          color: 'var(--s-muted)',
                          cursor: 'pointer',
                          fontSize: 9,
                          fontWeight: 600,
                          opacity: isUploading ? 0.5 : 1,
                        }}
                      >
                        {isUploading ? (
                          <Loader2 size={15} className="animate-spin" />
                        ) : (
                          <>
                            <ImagePlus size={15} />
                            <span>Добавить</span>
                          </>
                        )}
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </section>

        {/* === Legal documents === */}
        <section style={{ padding: '10px 2px 14px', borderBottom: '1px solid var(--s-line)' }}>
          <h2 className="ds-h3" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <ShieldCheck size={18} style={{ color: 'var(--s-accent)' }} />
            Юридические документы и персональные данные
          </h2>
          <p className="ds-sub" style={{ marginBottom: 14, lineHeight: 1.5 }}>
            Эти реквизиты подставляются в Политику обработки персональных данных, отдельное согласие на обработку ПД и условия записи. Не включайте документы, пока не указаны реальные данные оператора.
          </p>

          <div className="ds-field">
            <label className="ds-label">Официальное наименование / ФИО ИП или самозанятого</label>
            <input className="ds-input" value={form.legal_name || ''} onChange={(e) => updateField('legal_name', e.target.value)} placeholder="ИП Иванов Иван Иванович / ООО «…»" />
          </div>
          <div className="ds-field-row">
            <div className="ds-field">
              <label className="ds-label">ИНН</label>
              <input className="ds-input" value={form.legal_inn || ''} onChange={(e) => updateField('legal_inn', e.target.value)} />
            </div>
            <div className="ds-field">
              <label className="ds-label">ОГРН / ОГРНИП</label>
              <input className="ds-input" value={form.legal_ogrn || ''} onChange={(e) => updateField('legal_ogrn', e.target.value)} />
            </div>
          </div>
          <div className="ds-field">
            <label className="ds-label">Адрес оператора</label>
            <input className="ds-input" value={form.legal_address || ''} onChange={(e) => updateField('legal_address', e.target.value)} />
          </div>
          <div className="ds-field">
            <label className="ds-label">Email для обращений по персональным данным</label>
            <input type="email" className="ds-input" value={form.legal_email || ''} onChange={(e) => updateField('legal_email', e.target.value)} placeholder="privacy@example.ru" />
          </div>

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginTop: 6 }}>
            <div>
              <div className="ds-text" style={{ fontWeight: 600 }}>Показывать документы и требовать отдельные согласия</div>
              <p className="ds-caption" style={{ marginTop: 3 }}>После включения клиент не сможет завершить запись без двух самостоятельных подтверждений.</p>
            </div>
            <Toggle checked={form.legal_documents_enabled ?? false} onChange={(v) => updateField('legal_documents_enabled', v)} />
          </div>

          {businessId && (
            <div className="ds-sub" style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 12 }}>
              <a href={`/legal/privacy/${businessId}`} target="_blank" rel="noreferrer" className="ds-accent">Политика ПД</a>
              <a href={`/legal/consent/${businessId}`} target="_blank" rel="noreferrer" className="ds-accent">Согласие на ПД</a>
              <a href={`/legal/terms/${businessId}`} target="_blank" rel="noreferrer" className="ds-accent">Условия записи</a>
            </div>
          )}

          <button onClick={handleSaveLegal} disabled={saving} className="ds-btn ds-btn--primary ds-btn--block" style={{ marginTop: 14 }}>
            {saving ? 'Сохранение...' : 'Сохранить юридические данные'}
          </button>
        </section>

        {/* === Маркетинг: доступ администраторов в студию === */}
        <section style={{ padding: '10px 2px 14px', borderBottom: '1px solid var(--s-line)' }}>
          <h2 className="ds-h3" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
            <Sparkles size={18} style={{ color: 'var(--s-accent)' }} />
            Маркетинг (AI Growth OS)
          </h2>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
            <div>
              <div className="ds-text" style={{ fontWeight: 600 }}>Пускать администраторов в маркетинговую студию</div>
              <p className="ds-caption" style={{ marginTop: 3 }}>Администраторы заведения смогут работать с постами, контент-планом и видео. Управлять доступом и удалять компанию можете только вы.</p>
            </div>
            <Toggle checked={form.studio_admin_access ?? false} onChange={handleStudioAccess} />
          </div>
          {businessId && <p className="ds-caption" style={{ marginTop: 10 }}>Номер вашего заведения для подключения к студии: <b>{businessId}</b></p>}
        </section>

        {/* === 3. Prepayment Settings === */}
        <section style={{ padding: '10px 2px 14px', borderBottom: '1px solid var(--s-line)' }}>
          <h2 className="ds-h3" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
            <CreditCard size={18} style={{ color: 'var(--s-accent)' }} />
            Предоплата
          </h2>

          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: 14,
            }}
          >
            <span className="ds-text" style={{ fontWeight: 600 }}>Включить предоплату</span>
            <Toggle
              checked={form.prepayment_enabled ?? false}
              onChange={(v) => updateField('prepayment_enabled', v)}
            />
          </div>

          {form.prepayment_enabled && (
            <>
              <div className="ds-field">
                <label className="ds-label">Процент предоплаты</label>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <input
                    type="number"
                    min={1}
                    max={100}
                    value={form.prepayment_percent ?? ''}
                    onChange={(e) =>
                      updateField('prepayment_percent', e.target.value ? Number(e.target.value) : undefined)
                    }
                    placeholder="50"
                    className="ds-input"
                    style={{ width: 96 }}
                  />
                  <span className="ds-sub">%</span>
                </div>
              </div>

              <div className="ds-field">
                <label className="ds-label">Телефон для оплаты</label>
                <input
                  type="text"
                  value={form.payment_phone || ''}
                  onChange={(e) => updateField('payment_phone', e.target.value)}
                  placeholder="+7 (900) 123-45-67"
                  className="ds-input"
                />
              </div>

              <div className="ds-field">
                <label className="ds-label">Реквизиты</label>
                <textarea
                  value={form.payment_details || ''}
                  onChange={(e) => updateField('payment_details', e.target.value)}
                  rows={3}
                  placeholder="Номер карты, банк, получатель..."
                  className="ds-input"
                />
              </div>

              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  marginBottom: 14,
                }}
              >
                <span className="ds-text" style={{ fontWeight: 600 }}>Требовать чек</span>
                <Toggle
                  checked={form.require_receipt_for_prepayment ?? false}
                  onChange={(v) => updateField('require_receipt_for_prepayment', v)}
                />
              </div>

              <div className="ds-field" style={{ marginBottom: 0 }}>
                <label className="ds-label">Условия отмены</label>
                <textarea
                  value={form.cancellation_policy_text || ''}
                  onChange={(e) => updateField('cancellation_policy_text', e.target.value)}
                  rows={3}
                  placeholder="Опишите условия отмены и возврата предоплаты..."
                  className="ds-input"
                />
              </div>
            </>
          )}

          <button
            onClick={handleSavePrepayment}
            disabled={saving}
            className="ds-btn ds-btn--primary ds-btn--block"
            style={{ marginTop: 14 }}
          >
            {saving ? 'Сохранение...' : 'Сохранить настройки предоплаты'}
          </button>
        </section>

        {/* === 4. Co-Owners === */}
        <section style={{ padding: '10px 2px 14px', borderBottom: '1px solid var(--s-line)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <h2 className="ds-h3" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Users size={18} style={{ color: 'var(--s-accent)' }} />
              Совладельцы
            </h2>
            <button
              onClick={() => {
                setOwnerField('')
                setOwnerModalOpen(true)
              }}
              className="ds-btn ds-btn--primary ds-btn--sm"
              style={{ gap: 6 }}
            >
              <Plus size={14} />
              Добавить
            </button>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {owners.length === 0 && (
              <p className="ds-sub" style={{ padding: '8px 0' }}>Нет совладельцев</p>
            )}
            {owners.map((owner) => (
              <div
                key={owner.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '10px 12px',
                  borderRadius: 10,
                  background: 'var(--s-surface-2)',
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span
                      className="ds-text"
                      style={{
                        fontWeight: 600,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {owner.user_name}
                    </span>
                    <span
                      className={`ds-badge ${owner.role === 'owner' ? 'ds-badge--prepay' : 'ds-badge--confirmed'}`}
                    >
                      {owner.role === 'owner' ? 'Владелец' : 'Совладелец'}
                    </span>
                  </div>
                  {owner.user_phone && (
                    <p className="ds-caption" style={{ marginTop: 2 }}>{owner.user_phone}</p>
                  )}
                  {owner.telegram_id && owner.telegram_id > 0 && (
                    <p className="ds-caption">ID: {owner.telegram_id}</p>
                  )}
                </div>
                {owner.role !== 'owner' && (
                  <button
                    onClick={() => handleRemoveOwner(owner.id)}
                    className="ds-icon-btn"
                    style={{ marginLeft: 8, color: 'var(--s-danger)' }}
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
            ))}
          </div>
        </section>

        {/* === 5. QR Codes === */}
        <section style={{ padding: '10px 2px 14px', borderBottom: '1px solid var(--s-line)' }}>
          <h2 className="ds-h3" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
            <QrCode size={18} style={{ color: 'var(--s-accent)' }} />
            QR-коды
          </h2>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            {businessId && (
              <>
                {[
                  { label: 'QR для Telegram', src: qrTelegram, filename: 'qr-telegram.png' },
                  { label: 'QR для WebApp', src: qrWebapp, filename: 'qr-webapp.png' },
                ].map(({ label, src, filename }) => (
                  <div
                    key={label}
                    style={{
                      border: '1px solid var(--s-line)',
                      borderRadius: 10,
                      padding: 12,
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                    }}
                  >
                    <p className="ds-sub" style={{ fontWeight: 600, marginBottom: 10, color: 'var(--s-text)' }}>{label}</p>
                    {src ? (
                      <img src={src} alt={label} style={{ width: 140, height: 140, borderRadius: 8 }} />
                    ) : (
                      <div
                        style={{
                          width: 140,
                          height: 140,
                          borderRadius: 8,
                          background: 'var(--s-surface-2)',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                        }}
                      >
                        <Loader2 size={24} className="animate-spin" style={{ color: 'var(--s-muted)' }} />
                      </div>
                    )}
                    {src && (
                      <button
                        type="button"
                        onClick={() => {
                          // Programmatic click avoids Telegram webview swallowing
                          // the <a download> attribute. Fallback to opening the
                          // blob in a new tab if the click is also blocked.
                          try {
                            const link = document.createElement('a')
                            link.href = src
                            link.download = filename
                            link.rel = 'noopener'
                            document.body.appendChild(link)
                            link.click()
                            document.body.removeChild(link)
                          } catch {
                            window.open(src, '_blank', 'noopener')
                          }
                        }}
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 6,
                          marginTop: 10,
                          fontSize: 13,
                          color: 'var(--s-accent)',
                          background: 'none',
                          border: 'none',
                          cursor: 'pointer',
                          fontWeight: 600,
                        }}
                      >
                        <Download size={14} />
                        Скачать
                      </button>
                    )}
                  </div>
                ))}
              </>
            )}
          </div>
        </section>
      </div>

      {/* Add Owner Modal */}
      {ownerModalOpen && (
        <div className="ds-center-ov">
          <div className="ds-dialog">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <h3>Добавить совладельца</h3>
              <button
                onClick={() => setOwnerModalOpen(false)}
                className="ds-icon-btn"
              >
                <X size={18} />
              </button>
            </div>

            <div className="ds-field">
              <label className="ds-label">Email, Telegram ID или @username</label>
              <input
                type="text"
                value={ownerField}
                onChange={(e) => setOwnerField(e.target.value)}
                placeholder="owner@example.com или @username"
                className="ds-input"
              />
              <p className="ds-hint">Для доступа без Telegram пользователь должен сначала зарегистрировать и подтвердить email. Совладелец получает права управления бизнесом.</p>
            </div>

            <div className="ds-dialog-actions" style={{ marginTop: 16 }}>
              <button
                onClick={() => setOwnerModalOpen(false)}
                className="ds-btn ds-btn--ghost"
              >
                Отмена
              </button>
              <button
                onClick={handleAddOwner}
                disabled={!ownerField.trim()}
                className="ds-btn ds-btn--primary"
              >
                Добавить
              </button>
            </div>
          </div>
        </div>
      )}
    </AdminShell>
  )
}
