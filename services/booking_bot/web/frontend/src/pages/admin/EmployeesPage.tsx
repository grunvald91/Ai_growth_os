import { useState, useEffect, useCallback, useRef } from 'react'
import { Plus, Pencil, Trash2, X, User, Shield, Clock, MapPin, Users as UsersIcon, Calendar, ChevronLeft, ChevronRight, ImagePlus, ImageIcon } from 'lucide-react'
import { api, type Employee, type Service } from '../../api/client'
import { errorMessage } from '../../authUtils'
import { useAdminBusiness } from '../../hooks/useAdminBusiness'
import { BusinessSelector } from '../../components/BusinessSelector'
import { AdminShell } from '../../components/AdminShell'

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

const placeholderEmployees: Employee[] = [
  { id: 1, name: 'Анна Иванова', role: 'master', specialization: 'Парикмахер-стилист', is_active: true },
  { id: 2, name: 'Мария Петрова', role: 'master', specialization: 'Мастер маникюра', is_active: true },
  { id: 3, name: 'Елена Сидорова', role: 'master', specialization: 'Колорист', is_active: true },
  { id: 4, name: 'Ольга Козлова', role: 'admin', specialization: 'Администратор', is_active: true },
  { id: 5, name: 'Дарья Новикова', role: 'master', specialization: 'Косметолог', is_active: false },
]

const roleBadgeVariant: Record<string, { label: string; variant: string }> = {
  master: { label: 'Мастер', variant: 'ds-badge--confirmed' },
  admin: { label: 'Администратор', variant: 'ds-badge--prepay' },
}

function getTodayStr(): string {
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

interface EmployeeForm {
  name: string
  role: string
  specialization: string
  description: string
  telegram_id: string
  access_email: string
  booking_interval_minutes: string
  capacity: string
  area_sqm: string
  min_booking_minutes: string
  solo_price_per_hour: string
}

const emptyForm: EmployeeForm = {
  name: '',
  role: 'master',
  specialization: '',
  description: '',
  telegram_id: '',
  access_email: '',
  booking_interval_minutes: '',
  capacity: '',
  area_sqm: '',
  min_booking_minutes: '',
  solo_price_per_hour: '',
}

function employeeCover(emp: Employee): string | null {
  if (emp.primary_photo) return emp.primary_photo
  try {
    const parsed = emp.photos ? JSON.parse(emp.photos) : []
    if (Array.isArray(parsed) && typeof parsed[0] === 'string') return parsed[0]
  } catch { /* noop */ }
  return emp.photo_url || null
}

function photoUrl(photo: string) {
  return /^https?:\/\//i.test(photo) || photo.startsWith('/') ? photo : `/api/telegram-photo/${encodeURIComponent(photo)}`
}

function employeeInitials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join('')
}

export function EmployeesPage() {
  const { businesses, selectedId: businessId, selectBusiness } = useAdminBusiness()
  const [employees, setEmployees] = useState<Employee[]>([])
  const [loading, setLoading] = useState(true)
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [editingEmployee, setEditingEmployee] = useState<Employee | null>(null)
  const [form, setForm] = useState<EmployeeForm>({ ...emptyForm })

  // Services for employee-service linking
  const [allServices, setAllServices] = useState<Service[]>([])
  const [selectedServiceIds, setSelectedServiceIds] = useState<number[]>([])
  const [servicesLoading, setServicesLoading] = useState(false)

  // Photos for the employee being edited
  const [employeePhotos, setEmployeePhotos] = useState<string[]>([])
  const [photoUploading, setPhotoUploading] = useState(false)
  const photoInputRef = useRef<HTMLInputElement>(null)

  // Duty toggling state
  const [dutyLoading, setDutyLoading] = useState<number | null>(null)

  // Duty schedule modal
  const [dutyModalEmp, setDutyModalEmp] = useState<Employee | null>(null)
  const [dutyDates, setDutyDates] = useState<Set<string>>(new Set())
  const [dutyMonthOffset, setDutyMonthOffset] = useState(0)
  const [dutySaving, setDutySaving] = useState(false)

  const isVenueRental = businesses.find(b => b.id === businessId)?.business_type === 'venue_rental'

  useEffect(() => {
    if (!businessId) return
    setLoading(true)
    api
      .getEmployees(businessId)
      .then(setEmployees)
      .catch(() => setEmployees(placeholderEmployees))
      .finally(() => setLoading(false))
  }, [businessId])

  // Load services when modal opens
  const loadServices = useCallback(async (empId?: number) => {
    if (!businessId) return
    setServicesLoading(true)
    try {
      const svcs = await api.getServices(businessId)
      setAllServices(svcs)

      if (empId) {
        // Load linked services for this employee
        const linked = await api.getEmployeeServices(businessId, empId)
        setSelectedServiceIds(linked.map(l => l.service_id))
      } else {
        setSelectedServiceIds([])
      }
    } catch {
      setAllServices([])
      setSelectedServiceIds([])
    } finally {
      setServicesLoading(false)
    }
  }, [businessId])

  const openCreate = () => {
    setForm({ ...emptyForm })
    setEditingEmployee(null)
    setSelectedServiceIds([])
    setAllServices([])
    setEmployeePhotos([])
    setIsModalOpen(true)
    loadServices()
  }

  const openEdit = (emp: Employee) => {
    setForm({
      name: emp.name,
      role: emp.role || 'master',
      specialization: emp.specialization || '',
      description: emp.description || '',
      telegram_id: emp.telegram_id ? String(emp.telegram_id) : '',
      access_email: emp.access_email || '',
      booking_interval_minutes: emp.booking_interval_minutes ? String(emp.booking_interval_minutes) : '',
      capacity: emp.capacity ? String(emp.capacity) : '',
      area_sqm: emp.area_sqm ? String(emp.area_sqm) : '',
      min_booking_minutes: emp.min_booking_minutes ? String(emp.min_booking_minutes) : '',
      solo_price_per_hour: emp.solo_price_per_hour ? String(emp.solo_price_per_hour) : '',
    })
    setEditingEmployee(emp)
    setSelectedServiceIds(emp.service_ids || [])
    setAllServices([])
    setEmployeePhotos(parsePhotos(emp.photos))
    setIsModalOpen(true)
    loadServices(emp.id)
  }

  const closeModal = () => {
    setIsModalOpen(false)
    setEditingEmployee(null)
    setEmployeePhotos([])
  }

  const handlePhotoUpload = async (file: File) => {
    if (!businessId || !editingEmployee) {
      alert('Сначала сохраните сотрудника, затем добавьте фото.')
      return
    }
    setPhotoUploading(true)
    try {
      const res = await api.uploadEmployeePhoto(businessId, editingEmployee.id, file)
      setEmployeePhotos(res.photos)
      setEmployees((prev) =>
        prev.map((e) =>
          e.id === editingEmployee.id ? { ...e, photos: JSON.stringify(res.photos) } : e
        )
      )
    } catch (e) {
      alert('Ошибка загрузки фото: ' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setPhotoUploading(false)
      if (photoInputRef.current) photoInputRef.current.value = ''
    }
  }

  const handlePhotoDelete = async (index: number) => {
    if (!businessId || !editingEmployee) return
    if (!confirm('Удалить это фото?')) return
    try {
      const res = await api.deleteEmployeePhoto(businessId, editingEmployee.id, index)
      setEmployeePhotos(res.photos)
      setEmployees((prev) =>
        prev.map((e) =>
          e.id === editingEmployee.id
            ? { ...e, photos: res.photos.length > 0 ? JSON.stringify(res.photos) : undefined }
            : e
        )
      )
    } catch (e) {
      alert('Ошибка удаления фото: ' + (e instanceof Error ? e.message : String(e)))
    }
  }

  const handleSave = async () => {
    const data: Partial<Employee> = {
      name: form.name,
      role: form.role,
      specialization: form.specialization,
      description: form.description || undefined,
      is_active: editingEmployee?.is_active ?? true,
      // Пустое поле стирает Telegram ID (например, когда мастер больше не работает с вами).
      telegram_id: form.telegram_id ? parseInt(form.telegram_id) : null,
      // Пустое значение стирает доступ мастера по email.
      access_email: form.access_email.trim(),
      booking_interval_minutes: form.booking_interval_minutes ? parseInt(form.booking_interval_minutes) : undefined,
    }

    if (isVenueRental) {
      data.capacity = form.capacity ? parseInt(form.capacity) : undefined
      data.area_sqm = form.area_sqm ? parseFloat(form.area_sqm) : undefined
      data.min_booking_minutes = form.min_booking_minutes ? parseInt(form.min_booking_minutes) : undefined
      data.solo_price_per_hour = form.solo_price_per_hour ? parseFloat(form.solo_price_per_hour) : undefined
    }

    try {
      let savedEmp: Employee
      if (editingEmployee) {
        savedEmp = await api.updateEmployee(businessId!, editingEmployee.id, data)
        setEmployees((prev) =>
          prev.map((e) => (e.id === editingEmployee.id ? { ...savedEmp, service_ids: selectedServiceIds } : e))
        )
      } else {
        savedEmp = await api.createEmployee(businessId!, data)
        setEmployees((prev) => [...prev, { ...savedEmp, service_ids: selectedServiceIds }])
      }

      // Save employee-service links
      if (businessId && savedEmp.id) {
        try {
          await api.setEmployeeServices(businessId, savedEmp.id, selectedServiceIds)
        } catch {
          // Non-critical: don't block save
        }
      }

      closeModal()
    } catch (e) {
      alert('Ошибка при сохранении сотрудника: ' + errorMessage(e))
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Удалить сотрудника?')) return
    try {
      await api.deleteEmployee(businessId!, id)
      setEmployees((prev) => prev.filter((e) => e.id !== id))
    } catch (e) {
      alert('Ошибка при удалении сотрудника: ' + (e instanceof Error ? e.message : String(e)))
    }
  }

  const toggleActive = async (id: number) => {
    const emp = employees.find((e) => e.id === id)
    if (!emp) return
    try {
      const updated = await api.updateEmployee(businessId!, id, { is_active: !emp.is_active })
      setEmployees((prev) =>
        prev.map((e) => (e.id === id ? updated : e))
      )
    } catch (e) {
      alert('Ошибка при переключении статуса: ' + (e instanceof Error ? e.message : String(e)))
    }
  }

  const handleToggleDuty = async (empId: number) => {
    if (!businessId || dutyLoading !== null) return
    setDutyLoading(empId)
    try {
      const result = await api.toggleDuty(businessId, empId)
      setEmployees((prev) =>
        prev.map((e) =>
          e.id === empId
            ? { ...e, on_duty_date: result.on_duty ? result.date : undefined }
            : e
        )
      )
    } catch (e) {
      alert('Ошибка при переключении дежурства: ' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setDutyLoading(null)
    }
  }

  const openDutySchedule = async (emp: Employee) => {
    setDutyModalEmp(emp)
    setDutyMonthOffset(0)
    try {
      const result = await api.getDutySchedule(businessId!, emp.id)
      setDutyDates(new Set(result.dates))
    } catch {
      setDutyDates(new Set())
    }
  }

  const toggleDutyDate = (dateStr: string) => {
    setDutyDates((prev) => {
      const next = new Set(prev)
      if (next.has(dateStr)) {
        next.delete(dateStr)
      } else {
        next.add(dateStr)
      }
      return next
    })
  }

  const saveDutySchedule = async () => {
    if (!businessId || !dutyModalEmp) return
    setDutySaving(true)
    try {
      await api.setDutySchedule(businessId, dutyModalEmp.id, Array.from(dutyDates))
      setDutyModalEmp(null)
    } catch (e) {
      alert('Ошибка сохранения графика: ' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setDutySaving(false)
    }
  }

  const toggleServiceLink = (serviceId: number) => {
    setSelectedServiceIds((prev) =>
      prev.includes(serviceId)
        ? prev.filter((id) => id !== serviceId)
        : [...prev, serviceId]
    )
  }

  const today = getTodayStr()

  return (
    <AdminShell
      title={isVenueRental ? "Залы" : "Мастера"}
      headerExtra={
        <BusinessSelector businesses={businesses} selectedId={businessId} onSelect={selectBusiness} />
      }
    >
      {/* Команда: кто на дежурстве сегодня */}
      {!loading && employees.length > 0 && (() => {
        const onDuty = employees.filter((e) => e.on_duty_date === today)
        const lead = onDuty[0] ?? employees[0]
        return (
          <div className="ds-context">
            <div className="ds-context-eyebrow">{isVenueRental ? 'Помещения' : 'Команда'}</div>
            <div className="ds-context-headline">{isVenueRental ? 'Залы и ресурсы' : 'Мастера'} · {employees.filter((e) => e.is_active !== false).length} активных</div>
            <div className="ds-crew">
              {employees.slice(0, 3).map((e) => {
                const photo = employeeCover(e)
                return (
                  <span key={e.id} className="ds-crew-pic">
                    {photo ? <img src={photoUrl(photo)} alt="" /> : employeeInitials(e.name)}
                  </span>
                )
              })}
              <div>
                <strong>{onDuty.length > 0 ? `${onDuty.length} на дежурстве` : 'Сегодня никто не дежурит'}</strong>
                <span>{lead.name}{lead.specialization ? ` · ${lead.specialization}` : ''}</span>
              </div>
            </div>
          </div>
        )
      })()}

      {/* Список — прокручивается в середине экрана */}
      <div className="ds-list-scroll">
      {/* Loading skeletons */}
      {loading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div className="skeleton" style={{ height: 32, width: 160, borderRadius: 8, marginBottom: 4 }} />
          {[1, 2, 3].map((i) => (
            <div key={i} className="skeleton" style={{ height: 72, borderRadius: 14 }} />
          ))}
        </div>
      ) : (
        /* Employees list */
        <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
          {employees.map((emp) => {
            const badge = roleBadgeVariant[emp.role || 'master'] || roleBadgeVariant.master
            const isOnDuty = emp.on_duty_date === today
            return (
              <div
                key={emp.id}
                style={{ padding: '10px 3px 11px', borderBottom: '1px solid var(--s-line)', opacity: emp.is_active === false ? 0.6 : 1 }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  {/* Avatar */}
                  <div style={{
                    width: 44, height: 44, borderRadius: isVenueRental ? 13 : '50%', flexShrink: 0, overflow: 'hidden',
                    background: 'radial-gradient(circle at 45% 26%, #fff, #d5e8fa)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color: '#4f76a4', fontSize: 12, fontWeight: 500,
                  }}>
                    {employeeCover(emp)
                      ? <img src={photoUrl(employeeCover(emp)!)} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                      : (employeeInitials(emp.name) || <User size={18} />)}
                  </div>

                  {/* Name + spec */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="ds-h3" style={{ fontSize: 13.5, fontWeight: 550, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {emp.name}
                    </div>
                    <div className="ds-sub" style={{ fontSize: 12, marginTop: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {emp.specialization || badge.label}
                    </div>
                    {emp.telegram_id && (
                      <div className="ds-caption" style={{ marginTop: 1 }}>
                        TG ID: {emp.telegram_id}
                      </div>
                    )}
                    {emp.access_email && (
                      <div className="ds-caption" style={{ marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        Вход: {emp.access_email}
                      </div>
                    )}
                  </div>

                  {/* Action controls */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
                    <button
                      onClick={() => toggleActive(emp.id)}
                      className={`ds-btn ds-btn--sm ${emp.is_active !== false ? 'ds-badge--completed' : 'ds-badge--cancelled'}`}
                      style={{ padding: '4px 8px', fontSize: 11, fontWeight: 700, border: 'none', cursor: 'pointer', borderRadius: 'var(--r-pill)' }}
                    >
                      {emp.is_active !== false ? 'Вкл' : 'Выкл'}
                    </button>
                    <button
                      className="ds-icon-btn"
                      onClick={() => openEdit(emp)}
                      aria-label="Редактировать"
                      style={{ width: 30, height: 30 }}
                    >
                      <Pencil size={13} />
                    </button>
                    <button
                      className="ds-icon-btn"
                      onClick={() => handleDelete(emp.id)}
                      aria-label="Удалить"
                      style={{ width: 30, height: 30, color: 'var(--s-danger)' }}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>

                {/* Info badges row */}
                <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 5, marginTop: 9, marginLeft: 54 }}>
                  {/* Role badge */}
                  <span className={`ds-badge ${badge.variant}`}>{badge.label}</span>

                  {/* Duty toggle */}
                  <button
                    onClick={() => handleToggleDuty(emp.id)}
                    disabled={dutyLoading === emp.id}
                    className={`ds-badge ${isOnDuty ? 'ds-badge--completed' : 'ds-badge--cancelled'}`}
                    style={{ cursor: 'pointer', border: 'none', opacity: dutyLoading === emp.id ? 0.5 : 1, display: 'inline-flex', alignItems: 'center', gap: 4 }}
                  >
                    <Shield size={9} />
                    {isOnDuty ? 'На дежурстве' : 'Не дежурит'}
                  </button>

                  {/* Schedule button */}
                  <button
                    onClick={() => openDutySchedule(emp)}
                    className="ds-badge ds-badge--prepay"
                    style={{ cursor: 'pointer', border: 'none', display: 'inline-flex', alignItems: 'center', gap: 4 }}
                  >
                    <Calendar size={9} />
                    График
                  </button>

                  {/* Booking interval */}
                  {emp.booking_interval_minutes && (
                    <span className="ds-badge ds-badge--confirmed" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                      <Clock size={9} />
                      {emp.booking_interval_minutes} мин
                    </span>
                  )}

                  {/* Venue rental badges */}
                  {isVenueRental && emp.capacity && (
                    <span className="ds-badge ds-badge--pending" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                      <UsersIcon size={9} />
                      {emp.capacity} чел
                    </span>
                  )}
                  {isVenueRental && emp.area_sqm && (
                    <span className="ds-badge ds-badge--pending" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                      <MapPin size={9} />
                      {emp.area_sqm} м²
                    </span>
                  )}
                </div>

                {emp.is_active === false && (
                  <div className="ds-caption" style={{ color: 'var(--s-danger)', marginTop: 4, marginLeft: 50 }}>
                    Неактивен
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
      </div>

      {/* Управление — внизу, у большого пальца */}
      <div className="ds-bottom-controls">
        <button className="ds-btn ds-btn--primary ds-btn--block" onClick={openCreate}>
          <Plus size={15} />
          {isVenueRental ? 'Добавить зал' : 'Добавить мастера'}
        </button>
      </div>

      {/* Create / Edit sheet */}
      {isModalOpen && (
        <div className="ds-sheet-ov">
          <div className="ds-sheet">
            <div className="ds-sheet-grip" />
            <div className="ds-sheet-head">
              <div>
                <h3>{editingEmployee ? 'Редактировать' : 'Новый сотрудник'}</h3>
              </div>
              <button className="ds-icon-btn" onClick={closeModal} aria-label="Закрыть">
                <X size={18} />
              </button>
            </div>

            {/* Name */}
            <div className="ds-field">
              <label className="ds-label">Имя</label>
              <input
                type="text"
                className="ds-input"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Иванова Анна"
              />
            </div>

            {/* Role */}
            <div className="ds-field">
              <label className="ds-label">Роль</label>
              <select
                className="ds-input"
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}
              >
                <option value="master">Мастер</option>
                <option value="admin">Администратор</option>
              </select>
            </div>

            {/* Specialization */}
            <div className="ds-field">
              <label className="ds-label">Специализация</label>
              <input
                type="text"
                className="ds-input"
                value={form.specialization}
                onChange={(e) => setForm({ ...form, specialization: e.target.value })}
                placeholder="Парикмахер-стилист"
              />
            </div>

            {/* Description */}
            <div className="ds-field">
              <label className="ds-label">Описание</label>
              <textarea
                className="ds-input"
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder="Описание сотрудника..."
                rows={3}
              />
            </div>

            {/* Вход мастера на сайт */}
            {!isVenueRental && (
              <div className="ds-field">
                <label className="ds-label" htmlFor="employee-access-email">Email для входа мастера на сайт</label>
                <input
                  id="employee-access-email"
                  type="email"
                  inputMode="email"
                  autoComplete="off"
                  className="ds-input"
                  value={form.access_email}
                  onChange={(e) => setForm({ ...form, access_email: e.target.value })}
                  placeholder="master@example.com"
                />
                <p className="ds-hint">
                  Мастер входит на сайт по этому email (с паролем или через Яндекс ID с этим же адресом) и видит только
                  свои записи: время, услугу, имя и телефон клиента. Цен, аналитики и настроек у него нет.
                  Оставьте пустым, если вход на сайт не нужен.
                </p>
              </div>
            )}

            {/* Telegram ID */}
            <div className="ds-field">
              <label className="ds-label" htmlFor="employee-telegram-id">Telegram ID (для входа через Telegram)</label>
              <input
                id="employee-telegram-id"
                type="number"
                className="ds-input"
                value={form.telegram_id}
                onChange={(e) => setForm({ ...form, telegram_id: e.target.value })}
                placeholder="123456789"
              />
              <p className="ds-hint">
                Это число, а не @имя. Сотрудник узнаёт его у бота @userinfobot. По нему он входит на сайт через
                Telegram и получает режим мастера в боте. Можно указать и email, и Telegram ID, подойдёт любой способ входа.
              </p>
            </div>

            {/* Booking interval */}
            <div className="ds-field">
              <label className="ds-label">Интервал записи, мин</label>
              <input
                type="number"
                className="ds-input"
                value={form.booking_interval_minutes}
                onChange={(e) => setForm({ ...form, booking_interval_minutes: e.target.value })}
                placeholder="30"
              />
            </div>

            {/* Venue rental specific fields */}
            {isVenueRental && (
              <>
                <div style={{ borderTop: '1px solid var(--s-line)', paddingTop: 12, marginBottom: 10 }}>
                  <span className="ds-caption" style={{ fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    Параметры площадки
                  </span>
                </div>

                <div className="ds-field-row">
                  <div className="ds-field">
                    <label className="ds-label">Вместимость, чел</label>
                    <input
                      type="number"
                      className="ds-input"
                      value={form.capacity}
                      onChange={(e) => setForm({ ...form, capacity: e.target.value })}
                      placeholder="10"
                    />
                  </div>
                  <div className="ds-field">
                    <label className="ds-label">Площадь, м²</label>
                    <input
                      type="number"
                      className="ds-input"
                      value={form.area_sqm}
                      onChange={(e) => setForm({ ...form, area_sqm: e.target.value })}
                      placeholder="50"
                    />
                  </div>
                </div>

                <div className="ds-field">
                  <label className="ds-label">Мин. время бронирования, мин</label>
                  <input
                    type="number"
                    className="ds-input"
                    value={form.min_booking_minutes}
                    onChange={(e) => setForm({ ...form, min_booking_minutes: e.target.value })}
                    placeholder="60"
                  />
                </div>

                <div className="ds-field">
                  <label className="ds-label">Тариф соло, руб/ч</label>
                  <input
                    type="number"
                    className="ds-input"
                    value={form.solo_price_per_hour}
                    onChange={(e) => setForm({ ...form, solo_price_per_hour: e.target.value })}
                    placeholder="500"
                  />
                </div>
              </>
            )}

            {/* Photos section */}
            <div style={{ borderTop: '1px solid var(--s-line)', paddingTop: 12, marginBottom: 10 }}>
              <span className="ds-caption" style={{ fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Фото {isVenueRental ? 'зала' : 'сотрудника'}
              </span>
            </div>

            {!editingEmployee ? (
              <p className="ds-hint">
                Сохраните {isVenueRental ? 'зал' : 'сотрудника'}, затем добавьте фото.
              </p>
            ) : (
              <>
                <input
                  ref={photoInputRef}
                  type="file"
                  accept="image/*"
                  style={{ display: 'none' }}
                  onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (f) handlePhotoUpload(f)
                  }}
                />
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, marginBottom: 8 }}>
                  {employeePhotos.map((fid, idx) => (
                    <div
                      key={`${fid}-${idx}`}
                      style={{ position: 'relative', aspectRatio: '1', borderRadius: 'var(--r-md)', overflow: 'hidden', background: 'var(--s-surface-2)' }}
                    >
                      <img
                        src={`/api/telegram-photo/${encodeURIComponent(fid)}`}
                        alt={`Фото ${idx + 1}`}
                        style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                      />
                      <button
                        type="button"
                        onClick={() => handlePhotoDelete(idx)}
                        style={{
                          position: 'absolute', top: 4, right: 4, width: 22, height: 22,
                          background: 'var(--s-danger)', color: '#fff', borderRadius: '50%', border: 'none',
                          display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
                        }}
                        aria-label="Удалить фото"
                      >
                        <Trash2 size={11} />
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={() => photoInputRef.current?.click()}
                    disabled={photoUploading}
                    style={{
                      aspectRatio: '1', borderRadius: 'var(--r-md)',
                      border: '2px dashed var(--s-line-2)', background: 'transparent',
                      color: 'var(--s-muted)', display: 'flex', flexDirection: 'column',
                      alignItems: 'center', justifyContent: 'center', gap: 4,
                      cursor: 'pointer', opacity: photoUploading ? 0.5 : 1,
                    }}
                  >
                    {photoUploading ? (
                      <div className="animate-spin" style={{ width: 20, height: 20, border: '2px solid var(--s-accent)', borderTopColor: 'transparent', borderRadius: '50%' }} />
                    ) : (
                      <>
                        <ImagePlus size={20} />
                        <span style={{ fontSize: 10, fontWeight: 600 }}>Добавить</span>
                      </>
                    )}
                  </button>
                </div>
                {employeePhotos.length === 0 && !photoUploading && (
                  <p className="ds-hint" style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                    <ImageIcon size={12} />
                    Фото пока нет
                  </p>
                )}
              </>
            )}

            {/* Employee-Service links */}
            <div style={{ borderTop: '1px solid var(--s-line)', paddingTop: 12, marginBottom: 10, marginTop: 4 }}>
              <span className="ds-caption" style={{ fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Услуги
              </span>
            </div>

            {servicesLoading ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {[1, 2, 3].map((i) => (
                  <div key={i} className="skeleton" style={{ height: 28, borderRadius: 8 }} />
                ))}
              </div>
            ) : allServices.length === 0 ? (
              <p className="ds-hint">Нет доступных услуг</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 180, overflowY: 'auto' }}>
                {allServices.map((svc) => (
                  <label
                    key={svc.id}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer',
                      padding: '6px 8px', borderRadius: 'var(--r-sm)',
                      background: selectedServiceIds.includes(svc.id) ? 'var(--s-accent-soft)' : 'transparent',
                    }}
                  >
                    <div style={{ position: 'relative', flexShrink: 0 }}>
                      <input
                        type="checkbox"
                        checked={selectedServiceIds.includes(svc.id)}
                        onChange={() => toggleServiceLink(svc.id)}
                        style={{ position: 'absolute', opacity: 0, width: 0, height: 0 }}
                      />
                      <div style={{
                        width: 18, height: 18, border: `2px solid ${selectedServiceIds.includes(svc.id) ? 'var(--s-accent)' : 'var(--s-line-2)'}`,
                        borderRadius: 5, background: selectedServiceIds.includes(svc.id) ? 'var(--s-accent)' : 'transparent',
                        display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'all 0.15s',
                      }}>
                        {selectedServiceIds.includes(svc.id) && (
                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M5 13l4 4L19 7" />
                          </svg>
                        )}
                      </div>
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <span className="ds-text" style={{ display: 'block', fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {svc.name}
                      </span>
                      {svc.price !== undefined && (
                        <span className="ds-caption">{svc.price} руб</span>
                      )}
                    </div>
                  </label>
                ))}
              </div>
            )}

            {/* Footer buttons */}
            <div style={{ display: 'flex', gap: 10, marginTop: 18 }}>
              <button className="ds-btn ds-btn--ghost" style={{ flex: 1 }} onClick={closeModal}>
                Отмена
              </button>
              <button
                className="ds-btn ds-btn--primary"
                style={{ flex: 1 }}
                onClick={handleSave}
                disabled={!form.name || !form.specialization}
              >
                {editingEmployee ? 'Сохранить' : 'Добавить'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Duty Schedule dialog */}
      {dutyModalEmp && (
        <div className="ds-center-ov">
          <div className="ds-dialog" style={{ maxWidth: 360 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
              <h3 style={{ margin: 0, fontSize: 16 }}>График: {dutyModalEmp.name}</h3>
              <button className="ds-icon-btn" onClick={() => setDutyModalEmp(null)} aria-label="Закрыть">
                <X size={16} />
              </button>
            </div>

            {(() => {
              const baseDate = new Date()
              baseDate.setDate(1)
              baseDate.setMonth(baseDate.getMonth() + dutyMonthOffset)
              const year = baseDate.getFullYear()
              const month = baseDate.getMonth()
              const monthName = baseDate.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' })

              const firstDay = new Date(year, month, 1)
              const lastDay = new Date(year, month + 1, 0)
              let startDow = firstDay.getDay()
              if (startDow === 0) startDow = 7
              const daysInMonth = lastDay.getDate()
              const todayStr = getTodayStr()

              const cells: (number | null)[] = []
              for (let i = 1; i < startDow; i++) cells.push(null)
              for (let d = 1; d <= daysInMonth; d++) cells.push(d)

              return (
                <>
                  {/* Month nav */}
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                    <button className="ds-icon-btn" onClick={() => setDutyMonthOffset(o => o - 1)}>
                      <ChevronLeft size={15} />
                    </button>
                    <span className="ds-text" style={{ fontWeight: 600, textTransform: 'capitalize' }}>{monthName}</span>
                    <button className="ds-icon-btn" onClick={() => setDutyMonthOffset(o => o + 1)}>
                      <ChevronRight size={15} />
                    </button>
                  </div>

                  {/* Day headers */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 3, marginBottom: 4 }}>
                    {['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].map((d) => (
                      <div key={d} className="ds-caption" style={{ textAlign: 'center', fontWeight: 700, textTransform: 'uppercase' }}>{d}</div>
                    ))}
                  </div>

                  {/* Calendar grid */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 3 }}>
                    {cells.map((day, idx) => {
                      if (day === null) return <div key={`empty-${idx}`} />
                      const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
                      const isDuty = dutyDates.has(dateStr)
                      const isToday = dateStr === todayStr
                      return (
                        <button
                          key={dateStr}
                          onClick={() => toggleDutyDate(dateStr)}
                          style={{
                            aspectRatio: '1', borderRadius: 'var(--r-sm)', fontSize: 12, fontWeight: 600,
                            display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
                            border: isToday && !isDuty ? '1px solid var(--s-accent)' : '1px solid transparent',
                            background: isDuty ? 'var(--s-success)' : isToday ? 'var(--s-accent-soft)' : 'var(--s-surface-2)',
                            color: isDuty ? '#fff' : isToday ? 'var(--s-accent)' : 'var(--s-text)',
                            transition: 'all 0.12s',
                          }}
                        >
                          {day}
                        </button>
                      )
                    })}
                  </div>

                  <p className="ds-hint" style={{ textAlign: 'center', marginTop: 8 }}>
                    Нажмите на день, чтобы отметить дежурство. Зелёные — дежурные дни.
                  </p>
                </>
              )
            })()}

            <div className="ds-dialog-actions" style={{ marginTop: 14 }}>
              <button className="ds-btn ds-btn--ghost" onClick={() => setDutyModalEmp(null)}>
                Отмена
              </button>
              <button className="ds-btn ds-btn--primary" onClick={saveDutySchedule} disabled={dutySaving}>
                {dutySaving ? 'Сохранение...' : 'Сохранить'}
              </button>
            </div>
          </div>
        </div>
      )}
    </AdminShell>
  )
}
