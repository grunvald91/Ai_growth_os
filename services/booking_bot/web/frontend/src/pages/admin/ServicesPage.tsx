import { useState, useEffect } from 'react'
import {
  Scissors,
  Plus,
  Pencil,
  Trash2,
  X,
  Clock,
  ChevronUp,
  ChevronDown,
  ChevronRight,
} from 'lucide-react'
import { api, type Service, type GroupPricingTier } from '../../api/client'
import { useAdminBusiness } from '../../hooks/useAdminBusiness'
import { BusinessSelector } from '../../components/BusinessSelector'
import { AdminShell } from '../../components/AdminShell'

const placeholderServices: Service[] = [
  { id: 1, name: 'Стрижка женская', description: 'Стрижка с укладкой', price: 2500, duration_minutes: 60, is_active: true, sort_order: 0 },
  { id: 2, name: 'Окрашивание', description: 'Однотонное окрашивание', price: 5000, duration_minutes: 120, is_active: true, sort_order: 1 },
  { id: 3, name: 'Маникюр', description: 'Классический маникюр с покрытием', price: 2000, duration_minutes: 90, is_active: true, sort_order: 2 },
  { id: 4, name: 'Укладка', description: 'Профессиональная укладка', price: 1500, duration_minutes: 45, is_active: true, sort_order: 3 },
  { id: 5, name: 'Мужская стрижка', description: 'Стрижка машинкой и ножницами', price: 1500, duration_minutes: 40, is_active: true, sort_order: 4 },
  { id: 6, name: 'Педикюр', description: 'Классический педикюр', price: 2500, duration_minutes: 75, is_active: false, sort_order: 5 },
]

function formatPrice(price?: number | null, isHourly?: boolean) {
  if (price == null) return '—'
  const formatted = new Intl.NumberFormat('ru-RU').format(price)
  return isHourly ? `${formatted} ₽/ч` : `${formatted} ₽`
}

function formatDuration(minutes?: number | null) {
  if (!minutes || minutes <= 0) return '—'
  if (minutes < 60) return `${minutes} мин`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m ? `${h} ч ${m} мин` : `${h} ч`
}

interface ServiceFormData {
  name: string
  description: string
  price: string
  duration: string
  is_additional: boolean
  is_hourly_price: boolean
  pricing_type: string
  group_pricing: GroupPricingTier[]
}

function ToggleSwitch({
  checked,
  onChange,
  label,
}: {
  checked: boolean
  onChange: (val: boolean) => void
  label: string
}) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }}>
      <div style={{ position: 'relative', flexShrink: 0 }}>
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
          style={{ position: 'absolute', opacity: 0, width: 0, height: 0 }}
        />
        <div
          style={{
            width: 44,
            height: 24,
            borderRadius: 999,
            background: checked ? 'var(--s-accent)' : 'var(--s-surface-3)',
            transition: 'background 0.2s ease',
            border: '1px solid var(--s-line)',
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: checked ? 22 : 2,
            top: 2,
            width: 20,
            height: 20,
            borderRadius: '50%',
            background: 'var(--s-surface)',
            boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
            transition: 'left 0.2s ease',
          }}
        />
      </div>
      <span className="ds-text">{label}</span>
    </label>
  )
}

export function ServicesPage() {
  const { businesses, selectedId: businessId, selectBusiness } = useAdminBusiness()
  const [services, setServices] = useState<Service[]>([])
  const [loading, setLoading] = useState(true)
  const [editingService, setEditingService] = useState<Service | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<Service | null>(null)
  const [allowMultiple, setAllowMultiple] = useState<boolean | null>(null)
  const [isVenue, setIsVenue] = useState(false)
  const [savingMultiple, setSavingMultiple] = useState(false)
  const [form, setForm] = useState<ServiceFormData>({
    name: '',
    description: '',
    price: '',
    duration: '',
    is_additional: false,
    is_hourly_price: false,
    pricing_type: 'fixed',
    group_pricing: [],
  })

  useEffect(() => {
    if (!businessId) return
    setLoading(true)
    api
      .getServices(businessId)
      .then(setServices)
      .catch(() => setServices(placeholderServices))
      .finally(() => setLoading(false))
  }, [businessId])

  // Настройка записи: можно ли выбрать несколько услуг в одной записи
  useEffect(() => {
    if (!businessId) return
    let ignore = false
    setAllowMultiple(null)
    api
      .getBusinessDetail(businessId)
      .then((biz) => {
        if (ignore) return
        setAllowMultiple(!!biz.allow_multiple_services)
        setIsVenue(biz.business_type === 'venue_rental')
      })
      // При ошибке значение неизвестно — переключатель остаётся недоступным.
      .catch(() => {})
    return () => { ignore = true }
  }, [businessId])

  const toggleAllowMultiple = async () => {
    if (!businessId || allowMultiple === null) return
    const next = !allowMultiple
    setAllowMultiple(next)
    setSavingMultiple(true)
    try {
      await api.updateBusiness(businessId, { allow_multiple_services: next })
    } catch (e) {
      setAllowMultiple(!next)
      alert('Не удалось сохранить настройку: ' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setSavingMultiple(false)
    }
  }

  const openCreate = () => {
    setForm({ name: '', description: '', price: '', duration: '', is_additional: false, is_hourly_price: false, pricing_type: 'fixed', group_pricing: [] })
    setEditingService(null)
    setIsCreating(true)
  }

  const openEdit = (service: Service) => {
    setForm({
      name: service.name,
      description: service.description || '',
      price: String(service.price ?? ''),
      duration: String(service.duration_minutes ?? service.duration ?? ''),
      is_additional: service.is_additional ?? false,
      is_hourly_price: service.is_hourly_price ?? false,
      pricing_type: service.pricing_type || 'fixed',
      group_pricing: service.group_pricing || [],
    })
    setEditingService(service)
    setIsCreating(true)
  }

  const closeModal = () => {
    setIsCreating(false)
    setEditingService(null)
  }

  const handleSave = async () => {
    const data: Partial<Service> = {
      name: form.name,
      description: form.description,
      price: form.price ? Number(form.price) : undefined,
      duration_minutes: form.duration ? Number(form.duration) : undefined,
      is_active: editingService?.is_active ?? true,
      is_additional: form.is_additional,
      is_hourly_price: form.is_hourly_price,
      pricing_type: form.pricing_type,
      group_pricing: form.pricing_type === 'per_person' && form.group_pricing.length > 0 ? form.group_pricing : null,
    }

    try {
      if (editingService) {
        const updated = await api.updateService(businessId!, editingService.id, data)
        setServices((prev) =>
          prev.map((s) => (s.id === editingService.id ? updated : s))
        )
      } else {
        const created = await api.createService(businessId!, data)
        setServices((prev) => [...prev, created])
      }
      closeModal()
    } catch (e) {
      alert('Ошибка при сохранении услуги: ' + (e instanceof Error ? e.message : String(e)))
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await api.deleteService(businessId!, id)
      setServices((prev) => prev.filter((s) => s.id !== id))
      setDeleteTarget(null)
    } catch (e) {
      alert('Ошибка при удалении услуги: ' + (e instanceof Error ? e.message : String(e)))
    }
  }

  const toggleActive = async (id: number) => {
    const service = services.find((s) => s.id === id)
    if (!service) return
    try {
      const updated = await api.updateService(businessId!, id, { is_active: !service.is_active })
      setServices((prev) =>
        prev.map((s) => (s.id === id ? updated : s))
      )
    } catch (e) {
      alert('Ошибка при переключении статуса: ' + (e instanceof Error ? e.message : String(e)))
    }
  }

  // Sort services by sort_order, then by id as fallback
  const sortedServices = [...services].sort((a, b) => {
    const orderA = a.sort_order ?? 9999
    const orderB = b.sort_order ?? 9999
    if (orderA !== orderB) return orderA - orderB
    return a.id - b.id
  })

  const mainServices = sortedServices.filter((s) => !s.is_additional)
  const additionalServices = sortedServices.filter((s) => s.is_additional)
  const minActivePrice = mainServices.reduce<number | null>(
    (min, s) => (s.is_active && s.price != null && s.price > 0 && (min == null || s.price < min) ? s.price : min),
    null
  )

  const handleMoveUp = async (service: Service, group: Service[]) => {
    const idx = group.findIndex((s) => s.id === service.id)
    if (idx <= 0) return

    const newGroup = [...group]
    ;[newGroup[idx - 1], newGroup[idx]] = [newGroup[idx], newGroup[idx - 1]]

    // Build the full ordered list: main first, then additional
    const isAdditionalGroup = service.is_additional
    const otherGroup = isAdditionalGroup ? mainServices : additionalServices
    const fullOrder = isAdditionalGroup
      ? [...otherGroup, ...newGroup]
      : [...newGroup, ...otherGroup]

    // Update sort_order locally
    const updatedServices = fullOrder.map((s, i) => ({ ...s, sort_order: i }))
    setServices(updatedServices)

    // Persist to server
    try {
      await api.reorderServices(businessId!, fullOrder.map((s) => s.id))
    } catch (e) {
      alert('Ошибка при сортировке: ' + (e instanceof Error ? e.message : String(e)))
      // Reload on error
      api.getServices(businessId!).then(setServices).catch(() => {})
    }
  }

  const handleMoveDown = async (service: Service, group: Service[]) => {
    const idx = group.findIndex((s) => s.id === service.id)
    if (idx < 0 || idx >= group.length - 1) return

    const newGroup = [...group]
    ;[newGroup[idx], newGroup[idx + 1]] = [newGroup[idx + 1], newGroup[idx]]

    // Build the full ordered list: main first, then additional
    const isAdditionalGroup = service.is_additional
    const otherGroup = isAdditionalGroup ? mainServices : additionalServices
    const fullOrder = isAdditionalGroup
      ? [...otherGroup, ...newGroup]
      : [...newGroup, ...otherGroup]

    // Update sort_order locally
    const updatedServices = fullOrder.map((s, i) => ({ ...s, sort_order: i }))
    setServices(updatedServices)

    // Persist to server
    try {
      await api.reorderServices(businessId!, fullOrder.map((s) => s.id))
    } catch (e) {
      alert('Ошибка при сортировке: ' + (e instanceof Error ? e.message : String(e)))
      api.getServices(businessId!).then(setServices).catch(() => {})
    }
  }

  const renderServiceCard = (service: Service, group: Service[], idx: number) => (
    <div
      key={service.id}
      style={{
        padding: '10px 2px 9px',
        borderBottom: '1px solid var(--s-line)',
        opacity: service.is_active ? 1 : 0.55,
        transition: 'opacity 0.2s ease',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span className="ds-h3">{service.name}</span>
            {service.is_additional && (
              <span className="ds-badge ds-badge--confirmed">Доп. услуга</span>
            )}
            {!service.is_active && (
              <span className="ds-badge ds-badge--cancelled">Неактивна</span>
            )}
          </div>
          {service.description && (
            <p className="ds-sub" style={{ marginTop: 2 }}>{service.description}</p>
          )}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 6 }}>
            <span style={{ fontSize: 12.5, fontWeight: 550, color: 'var(--s-text)' }}>
              {formatPrice(service.price, service.is_hourly_price)}
              {service.pricing_type === 'per_person' && (
                <span className="ds-sub" style={{ fontWeight: 400, marginLeft: 2 }}>/чел</span>
              )}
            </span>
            <span className="ds-sub" style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <Clock size={13} />
              {formatDuration(service.duration_minutes ?? service.duration)}
            </span>
            {service.pricing_type === 'per_person' && service.group_pricing && service.group_pricing.length > 0 && (
              <span className="ds-badge ds-badge--prepay">
                {service.group_pricing.length} тариф{service.group_pricing.length > 1 ? (service.group_pricing.length < 5 ? 'а' : 'ов') : ''}
              </span>
            )}
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
          {/* Reorder buttons */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2, marginRight: 2 }}>
            <button
              onClick={() => handleMoveUp(service, group)}
              disabled={idx === 0}
              className="ds-icon-btn"
              style={{ width: 26, height: 26 }}
              title="Переместить вверх"
            >
              <ChevronUp size={13} />
            </button>
            <button
              onClick={() => handleMoveDown(service, group)}
              disabled={idx === group.length - 1}
              className="ds-icon-btn"
              style={{ width: 26, height: 26 }}
              title="Переместить вниз"
            >
              <ChevronDown size={13} />
            </button>
          </div>

          <button
            onClick={() => toggleActive(service.id)}
            className={`ds-btn ds-btn--sm${service.is_active ? '' : ' ds-btn--ghost'}`}
            style={service.is_active ? { background: 'var(--st-completed-bg)', color: 'var(--s-success)', borderColor: 'transparent' } : {}}
          >
            {service.is_active ? 'Вкл' : 'Выкл'}
          </button>
          <button
            onClick={() => openEdit(service)}
            className="ds-icon-btn"
            title="Редактировать"
          >
            <Pencil size={15} />
          </button>
          <button
            onClick={() => setDeleteTarget(service)}
            className="ds-icon-btn"
            style={{ color: 'var(--s-danger)' }}
            title="Удалить"
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>
    </div>
  )

  if (loading) {
    return (
      <AdminShell
        title="Услуги"
        headerExtra={<BusinessSelector businesses={businesses} selectedId={businessId} onSelect={selectBusiness} />}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 4 }}>
          <div className="skeleton" style={{ height: 28, width: 160, borderRadius: 8 }} />
          {[1, 2, 3].map((i) => (
            <div key={i} className="skeleton" style={{ height: 90, borderRadius: 14 }} />
          ))}
        </div>
      </AdminShell>
    )
  }

  return (
    <AdminShell
      title="Услуги"
      headerExtra={<BusinessSelector businesses={businesses} selectedId={businessId} onSelect={selectBusiness} />}
    >
      {/* Каталог: краткая сводка по реальному списку */}
      <div className="ds-context">
        <div className="ds-context-eyebrow">Ваши услуги</div>
        <div className="ds-context-headline">Каталог услуг</div>
        <div className="ds-context-strip">
          <div><strong>{mainServices.filter((s) => s.is_active).length}</strong><span>активных</span></div>
          {services.some((s) => !s.is_active) && (
            <div><strong>{services.filter((s) => !s.is_active).length}</strong><span>неактивных</span></div>
          )}
          {minActivePrice != null && <div><strong>{formatPrice(minActivePrice)}</strong><span>минимальная цена</span></div>}
        </div>
      </div>

      {/* Список — прокручивается в середине экрана */}
      {/* Режим выбора услуг клиентом — компактно, над списком */}
      <div className="ds-toggle-row ds-toggle-row--compact"
        title={isVenue ? 'Для аренды залов клиент всегда может выбрать несколько услуг' : 'Клиент сможет выбрать несколько услуг в одной записи'}>
        <span className="t1">{isVenue ? 'Несколько услуг в записи · всегда для залов' : 'Несколько услуг в одной записи'}</span>
        <button
          type="button"
          role="switch"
          aria-checked={isVenue || !!allowMultiple}
          aria-label="Разрешить несколько услуг при записи"
          className={`ds-toggle ds-toggle--sm ${isVenue || allowMultiple ? 'on' : ''}`}
          disabled={isVenue || allowMultiple === null || savingMultiple}
          onClick={toggleAllowMultiple}
        />
      </div>
      <div className="ds-list-scroll">
      {/* Main services section */}
      {mainServices.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <p className="ds-eyebrow">
            Основные услуги
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
            {mainServices.map((service, idx) =>
              renderServiceCard(service, mainServices, idx)
            )}
          </div>
        </div>
      )}

      {/* Additional services section */}
      {additionalServices.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <p className="ds-eyebrow">
            Дополнительные услуги
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
            {additionalServices.map((service, idx) =>
              renderServiceCard(service, additionalServices, idx)
            )}
          </div>
        </div>
      )}

      {/* Empty state */}
      {services.length === 0 && (
        <div style={{ textAlign: 'center', padding: '48px 0', color: 'var(--s-muted)' }}>
          <Scissors size={40} style={{ margin: '0 auto 10px', opacity: 0.3, display: 'block' }} />
          <p className="ds-sub">Нет услуг. Нажмите «Добавить услугу» чтобы создать первую.</p>
        </div>
      )}
      </div>

      {/* Управление — внизу, у большого пальца */}
      <div className="ds-bottom-controls">
      {businessId && (
        <div style={{ margin: '0 0 4px' }}>
          <a href={`/book/${businessId}`} target="_blank" rel="noopener noreferrer" className="ds-accent" style={{ fontSize: 11.5, display: 'inline-flex', alignItems: 'center', gap: 4, textDecoration: 'none' }}>
            Как увидит клиент <ChevronRight size={14} />
          </a>
        </div>
      )}
      {/* Add button */}
      <div style={{ marginTop: 6 }}>
        <button onClick={openCreate} className="ds-btn ds-btn--primary ds-btn--block">
          <Plus size={15} />
          Добавить услугу
        </button>
      </div>
      </div>

      {/* Create / Edit sheet */}
      {isCreating && (
        <div className="ds-sheet-ov" onClick={closeModal}>
          <div className="ds-sheet" onClick={(e) => e.stopPropagation()}>
            <div className="ds-sheet-grip" />
            <div className="ds-sheet-head">
              <div>
                <h3>{editingService ? 'Редактировать услугу' : 'Новая услуга'}</h3>
              </div>
              <button className="ds-icon-btn" onClick={closeModal} aria-label="Закрыть">
                <X size={18} />
              </button>
            </div>

            {/* Name */}
            <div className="ds-field">
              <label className="ds-label">Название</label>
              <input
                type="text"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Например: Стрижка женская"
                className="ds-input"
              />
            </div>

            {/* Description */}
            <div className="ds-field">
              <label className="ds-label">Описание</label>
              <input
                type="text"
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder="Краткое описание"
                className="ds-input"
              />
            </div>

            {/* Price + Duration */}
            <div className="ds-field-row">
              <div className="ds-field" style={{ marginBottom: 0 }}>
                <label className="ds-label">Цена, ₽</label>
                <input
                  type="number"
                  value={form.price}
                  onChange={(e) => setForm({ ...form, price: e.target.value })}
                  placeholder="2500"
                  className="ds-input"
                />
              </div>
              <div className="ds-field" style={{ marginBottom: 0 }}>
                <label className="ds-label">Длительность, мин</label>
                <input
                  type="number"
                  value={form.duration}
                  onChange={(e) => setForm({ ...form, duration: e.target.value })}
                  placeholder="60"
                  className="ds-input"
                />
              </div>
            </div>

            {/* Toggles */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12, margin: '14px 0' }}>
              <ToggleSwitch
                checked={form.is_additional}
                onChange={(val) => setForm({ ...form, is_additional: val })}
                label="Дополнительная услуга"
              />
              <ToggleSwitch
                checked={form.is_hourly_price}
                onChange={(val) => setForm({ ...form, is_hourly_price: val })}
                label="Почасовая цена"
              />
            </div>

            {/* Pricing type */}
            <div className="ds-field" style={{ borderTop: '1px solid var(--s-line)', paddingTop: 14 }}>
              <label className="ds-label">Тип ценообразования</label>
              <select
                value={form.pricing_type}
                onChange={(e) => setForm({ ...form, pricing_type: e.target.value })}
                className="ds-input"
                style={{ appearance: 'auto' }}
              >
                <option value="fixed">Фиксированная цена</option>
                <option value="per_person">По количеству человек</option>
              </select>
            </div>

            {/* Group pricing tiers */}
            {form.pricing_type === 'per_person' && (
              <div className="ds-field">
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                  <label className="ds-label" style={{ marginBottom: 0 }}>Тарифы по кол-ву человек</label>
                  <button
                    type="button"
                    onClick={() => {
                      const lastMax = form.group_pricing.length > 0
                        ? form.group_pricing[form.group_pricing.length - 1].max_people + 1
                        : 1
                      setForm({
                        ...form,
                        group_pricing: [
                          ...form.group_pricing,
                          { min_people: lastMax, max_people: lastMax + 4, price: Number(form.price) || 0 },
                        ],
                      })
                    }}
                    className="ds-btn ds-btn--sm"
                    style={{ background: 'var(--s-accent-soft)', color: 'var(--s-accent)', border: 'none', gap: 4 }}
                  >
                    <Plus size={12} />
                    Добавить тариф
                  </button>
                </div>
                {form.group_pricing.length === 0 && (
                  <p className="ds-hint">Добавьте тарифы для разного количества человек</p>
                )}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {form.group_pricing.map((tier, idx) => (
                    <div
                      key={idx}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        background: 'var(--s-surface-2)', borderRadius: 'var(--r-md)',
                        padding: '8px 10px', border: '1px solid var(--s-line)',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1 }}>
                        <input
                          type="number"
                          min={1}
                          value={tier.min_people}
                          onChange={(e) => {
                            const newTiers = [...form.group_pricing]
                            newTiers[idx] = { ...newTiers[idx], min_people: Number(e.target.value) || 1 }
                            setForm({ ...form, group_pricing: newTiers })
                          }}
                          className="ds-input"
                          style={{ width: 52, padding: '6px 8px', textAlign: 'center', fontSize: 13 }}
                        />
                        <span className="ds-sub">—</span>
                        <input
                          type="number"
                          min={1}
                          value={tier.max_people}
                          onChange={(e) => {
                            const newTiers = [...form.group_pricing]
                            newTiers[idx] = { ...newTiers[idx], max_people: Number(e.target.value) || 1 }
                            setForm({ ...form, group_pricing: newTiers })
                          }}
                          className="ds-input"
                          style={{ width: 52, padding: '6px 8px', textAlign: 'center', fontSize: 13 }}
                        />
                        <span className="ds-sub" style={{ flexShrink: 0 }}>чел</span>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <input
                          type="number"
                          value={tier.price}
                          onChange={(e) => {
                            const newTiers = [...form.group_pricing]
                            newTiers[idx] = { ...newTiers[idx], price: Number(e.target.value) || 0 }
                            setForm({ ...form, group_pricing: newTiers })
                          }}
                          className="ds-input"
                          style={{ width: 80, padding: '6px 8px', textAlign: 'right', fontSize: 13 }}
                        />
                        <span className="ds-sub">₽</span>
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          const newTiers = form.group_pricing.filter((_, i) => i !== idx)
                          setForm({ ...form, group_pricing: newTiers })
                        }}
                        className="ds-icon-btn"
                        style={{ width: 28, height: 28, color: 'var(--s-danger)', flexShrink: 0 }}
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Actions */}
            <div style={{ display: 'flex', gap: 10, marginTop: 6 }}>
              <button onClick={closeModal} className="ds-btn ds-btn--ghost ds-btn--block">
                Отмена
              </button>
              <button
                onClick={handleSave}
                disabled={!form.name || !form.price || !form.duration}
                className="ds-btn ds-btn--primary ds-btn--block"
              >
                {editingService ? 'Сохранить' : 'Создать'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete confirm dialog */}
      {deleteTarget && (
        <div className="ds-center-ov" onClick={() => setDeleteTarget(null)}>
          <div className="ds-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>Удалить услугу?</h3>
            <p>«{deleteTarget.name}» будет удалена без возможности восстановления.</p>
            <div className="ds-dialog-actions">
              <button onClick={() => setDeleteTarget(null)} className="ds-btn ds-btn--ghost">
                Отмена
              </button>
              <button onClick={() => handleDelete(deleteTarget.id)} className="ds-btn ds-btn--danger">
                Удалить
              </button>
            </div>
          </div>
        </div>
      )}
    </AdminShell>
  )
}
