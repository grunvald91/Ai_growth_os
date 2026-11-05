import { useState, useEffect } from 'react'
import { Plus, Pencil, Trash2, X, Package, Clock, DollarSign } from 'lucide-react'
import { api, type Service, type SubscriptionAdmin, type SubscriptionCreateData } from '../../api/client'
import { useAdminBusiness } from '../../hooks/useAdminBusiness'
import { BusinessSelector } from '../../components/BusinessSelector'
import { AdminShell } from '../../components/AdminShell'

function formatPrice(price?: number | null) {
  if (price == null) return '—'
  return new Intl.NumberFormat('ru-RU').format(price)
}

interface SubscriptionFormData {
  name: string
  description: string
  price: string
  validity_days: string
  rules: string
  services: { service_id: number; quantity: number }[]
}

const emptyForm: SubscriptionFormData = {
  name: '',
  description: '',
  price: '',
  validity_days: '',
  rules: '',
  services: [],
}

export function SubscriptionsAdminPage() {
  const { businesses, selectedId: businessId, selectBusiness } = useAdminBusiness()
  const [subscriptions, setSubscriptions] = useState<SubscriptionAdmin[]>([])
  const [loading, setLoading] = useState(true)
  const [editingSub, setEditingSub] = useState<SubscriptionAdmin | null>(null)
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [form, setForm] = useState<SubscriptionFormData>(emptyForm)
  const [availableServices, setAvailableServices] = useState<Service[]>([])
  const [servicesLoading, setServicesLoading] = useState(false)

  useEffect(() => {
    if (!businessId) return
    setLoading(true)
    api
      .getAdminSubscriptions(businessId)
      .then((list) => {
        const sorted = [...list].sort((a, b) => {
          if (a.is_active !== b.is_active) return a.is_active ? -1 : 1
          return a.name.localeCompare(b.name, 'ru')
        })
        setSubscriptions(sorted)
      })
      .catch(() => setSubscriptions([]))
      .finally(() => setLoading(false))
  }, [businessId])

  const loadServices = () => {
    if (!businessId) return
    setServicesLoading(true)
    api
      .getServices(businessId)
      .then(setAvailableServices)
      .catch(() => setAvailableServices([]))
      .finally(() => setServicesLoading(false))
  }

  const openCreate = () => {
    setForm({ ...emptyForm, services: [] })
    setEditingSub(null)
    setIsModalOpen(true)
    loadServices()
  }

  const openEdit = (sub: SubscriptionAdmin) => {
    setForm({
      name: sub.name,
      description: sub.description || '',
      price: String(sub.price),
      validity_days: String(sub.validity_days),
      rules: sub.rules || '',
      services: sub.services.map((s) => ({ service_id: s.id, quantity: s.quantity })),
    })
    setEditingSub(sub)
    setIsModalOpen(true)
    loadServices()
  }

  const closeModal = () => {
    setIsModalOpen(false)
    setEditingSub(null)
  }

  const toggleServiceInForm = (serviceId: number) => {
    setForm((prev) => {
      const exists = prev.services.find((s) => s.service_id === serviceId)
      if (exists) {
        return { ...prev, services: prev.services.filter((s) => s.service_id !== serviceId) }
      }
      return { ...prev, services: [...prev.services, { service_id: serviceId, quantity: 1 }] }
    })
  }

  const setServiceQuantity = (serviceId: number, quantity: number) => {
    setForm((prev) => ({
      ...prev,
      services: prev.services.map((s) =>
        s.service_id === serviceId ? { ...s, quantity: Math.max(1, quantity) } : s
      ),
    }))
  }

  const handleSave = async () => {
    if (!businessId) return
    const data: SubscriptionCreateData = {
      name: form.name,
      description: form.description || undefined,
      price: Number(form.price),
      validity_days: Number(form.validity_days),
      rules: form.rules || undefined,
      services: form.services,
    }

    try {
      if (editingSub) {
        const updated = await api.updateSubscription(businessId, editingSub.id, data)
        setSubscriptions((prev) =>
          prev
            .map((s) => (s.id === editingSub.id ? updated : s))
            .sort((a, b) => {
              if (a.is_active !== b.is_active) return a.is_active ? -1 : 1
              return a.name.localeCompare(b.name, 'ru')
            })
        )
      } else {
        const created = await api.createSubscription(businessId, data)
        setSubscriptions((prev) =>
          [...prev, created].sort((a, b) => {
            if (a.is_active !== b.is_active) return a.is_active ? -1 : 1
            return a.name.localeCompare(b.name, 'ru')
          })
        )
      }
      closeModal()
    } catch (e) {
      alert('Ошибка при сохранении абонемента: ' + (e instanceof Error ? e.message : String(e)))
    }
  }

  const handleDelete = async (id: number) => {
    if (!businessId) return
    if (!confirm('Удалить абонемент?')) return
    try {
      await api.deleteSubscription(businessId, id)
      setSubscriptions((prev) => prev.filter((s) => s.id !== id))
    } catch (e) {
      alert('Ошибка при удалении абонемента: ' + (e instanceof Error ? e.message : String(e)))
    }
  }

  const toggleActive = async (id: number) => {
    if (!businessId) return
    const sub = subscriptions.find((s) => s.id === id)
    if (!sub) return
    try {
      const updated = await api.updateSubscription(businessId, id, { is_active: !sub.is_active })
      setSubscriptions((prev) =>
        prev
          .map((s) => (s.id === id ? updated : s))
          .sort((a, b) => {
            if (a.is_active !== b.is_active) return a.is_active ? -1 : 1
            return a.name.localeCompare(b.name, 'ru')
          })
      )
    } catch (e) {
      alert('Ошибка при переключении статуса: ' + (e instanceof Error ? e.message : String(e)))
    }
  }

  return (
    <AdminShell
      title="Абонементы"
      headerExtra={<BusinessSelector businesses={businesses} selectedId={businessId} onSelect={selectBusiness} />}
    >
      {loading ? (
        <div style={{ marginTop: 4 }}>
          <div className="skeleton" style={{ height: 32, width: 192, borderRadius: 10, marginBottom: 16 }} />
          {[1, 2, 3].map((i) => (
            <div key={i} className="skeleton" style={{ height: 96, borderRadius: 14, marginBottom: 10 }} />
          ))}
        </div>
      ) : (
        <>
          {/* Add button */}
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 14, marginTop: 4 }}>
            <button className="ds-btn ds-btn--primary ds-btn--sm" onClick={openCreate}>
              <Plus size={15} />
              Добавить
            </button>
          </div>

          {/* Empty state */}
          {subscriptions.length === 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '64px 0', color: 'var(--s-muted)' }}>
              <Package size={48} style={{ marginBottom: 14, opacity: 0.4 }} />
              <p style={{ fontSize: 16, fontWeight: 600, color: 'var(--s-text-2)', marginBottom: 4 }}>Нет абонементов</p>
              <p className="ds-sub">Создайте первый абонемент</p>
            </div>
          )}

          {/* Subscriptions list */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {subscriptions.map((sub) => (
              <div
                key={sub.id}
                className="ds-card"
                style={{ padding: '14px 14px', opacity: sub.is_active ? 1 : 0.6 }}
              >
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                  {/* Main content */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <span style={{ fontSize: 14, fontWeight: 650, color: 'var(--s-text)' }}>{sub.name}</span>
                      {!sub.is_active && (
                        <span className="ds-badge ds-badge--cancelled">Неактивен</span>
                      )}
                    </div>
                    {sub.description && (
                      <p className="ds-sub" style={{ marginTop: 2 }}>{sub.description}</p>
                    )}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 6 }}>
                      <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 13, fontWeight: 650, color: 'var(--s-text)' }}>
                        <DollarSign size={13} />
                        {formatPrice(sub.price)} ₽
                      </span>
                      <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 13, color: 'var(--s-muted)' }}>
                        <Clock size={13} />
                        {sub.validity_days} дней
                      </span>
                    </div>
                    {sub.services.length > 0 && (
                      <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 2 }}>
                        {sub.services.map((svc) => (
                          <p key={svc.id} className="ds-caption" style={{ color: 'var(--s-muted)' }}>
                            — {svc.name} x{svc.quantity}
                          </p>
                        ))}
                      </div>
                    )}
                    {sub.rules && (
                      <p className="ds-caption" style={{ marginTop: 6, color: 'var(--s-muted)', overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
                        {sub.rules}
                      </p>
                    )}
                  </div>

                  {/* Actions */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
                    <button
                      onClick={() => toggleActive(sub.id)}
                      className={`ds-btn ds-btn--sm ${sub.is_active ? 'ds-btn--ghost' : 'ds-btn--ghost'}`}
                      style={{
                        color: sub.is_active ? 'var(--s-success)' : 'var(--s-muted)',
                        fontSize: 12,
                        padding: '6px 10px',
                      }}
                    >
                      {sub.is_active ? 'Вкл' : 'Выкл'}
                    </button>
                    <button
                      onClick={() => openEdit(sub)}
                      className="ds-icon-btn"
                      aria-label="Редактировать"
                    >
                      <Pencil size={16} />
                    </button>
                    <button
                      onClick={() => handleDelete(sub.id)}
                      className="ds-icon-btn"
                      style={{ color: 'var(--s-danger)' }}
                      aria-label="Удалить"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* Create / Edit sheet */}
      {isModalOpen && (
        <div className="ds-sheet-ov" onClick={closeModal}>
          <div className="ds-sheet" style={{ maxHeight: '90vh', overflowY: 'auto' }} onClick={(e) => e.stopPropagation()}>
            <div className="ds-sheet-grip" />
            <div className="ds-sheet-head">
              <div>
                <h3>{editingSub ? 'Редактировать абонемент' : 'Новый абонемент'}</h3>
              </div>
              <button className="ds-icon-btn" onClick={closeModal} aria-label="Закрыть">
                <X size={20} />
              </button>
            </div>

            {/* Name */}
            <div className="ds-field">
              <label className="ds-label">Название</label>
              <input
                type="text"
                className="ds-input"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Например: Безлимит на месяц"
              />
            </div>

            {/* Description */}
            <div className="ds-field">
              <label className="ds-label">Описание</label>
              <textarea
                className="ds-input"
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder="Краткое описание абонемента"
                rows={2}
              />
            </div>

            {/* Price + Validity */}
            <div className="ds-field-row">
              <div className="ds-field">
                <label className="ds-label">Цена, ₽</label>
                <input
                  type="number"
                  className="ds-input"
                  value={form.price}
                  onChange={(e) => setForm({ ...form, price: e.target.value })}
                  placeholder="5000"
                />
              </div>
              <div className="ds-field">
                <label className="ds-label">Срок действия, дней</label>
                <input
                  type="number"
                  className="ds-input"
                  value={form.validity_days}
                  onChange={(e) => setForm({ ...form, validity_days: e.target.value })}
                  placeholder="30"
                />
              </div>
            </div>

            {/* Rules */}
            <div className="ds-field">
              <label className="ds-label">Правила</label>
              <textarea
                className="ds-input"
                value={form.rules}
                onChange={(e) => setForm({ ...form, rules: e.target.value })}
                placeholder="Условия использования абонемента"
                rows={2}
              />
            </div>

            {/* Services picker */}
            <div className="ds-field">
              <label className="ds-label">Включённые услуги</label>
              {servicesLoading ? (
                <div className="skeleton" style={{ height: 80, borderRadius: 12 }} />
              ) : availableServices.length === 0 ? (
                <p className="ds-sub" style={{ padding: '12px 0' }}>Нет доступных услуг</p>
              ) : (
                <div
                  className="ds-input"
                  style={{ padding: '8px 10px', maxHeight: 192, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8 }}
                >
                  {availableServices
                    .filter((svc) => svc.is_active)
                    .map((svc) => {
                      const selected = form.services.find((s) => s.service_id === svc.id)
                      return (
                        <div key={svc.id} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                          <input
                            type="checkbox"
                            checked={!!selected}
                            onChange={() => toggleServiceInForm(svc.id)}
                            style={{ width: 16, height: 16, flexShrink: 0, accentColor: 'var(--s-accent)' }}
                          />
                          <span style={{ flex: 1, fontSize: 13, color: 'var(--s-text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {svc.name}
                            {svc.price != null && (
                              <span style={{ color: 'var(--s-muted)', marginLeft: 4 }}>
                                ({formatPrice(svc.price)} ₽)
                              </span>
                            )}
                          </span>
                          {selected && (
                            <input
                              type="number"
                              min={1}
                              value={selected.quantity}
                              onChange={(e) =>
                                setServiceQuantity(svc.id, parseInt(e.target.value) || 1)
                              }
                              className="ds-input"
                              style={{ width: 60, padding: '4px 8px', textAlign: 'center', fontSize: 13 }}
                            />
                          )}
                        </div>
                      )
                    })}
                </div>
              )}
            </div>

            {/* Actions */}
            <div style={{ display: 'flex', gap: 10, marginTop: 6 }}>
              <button className="ds-btn ds-btn--ghost ds-btn--block" onClick={closeModal}>
                Отмена
              </button>
              <button
                className="ds-btn ds-btn--primary ds-btn--block"
                onClick={handleSave}
                disabled={!form.name || !form.price || !form.validity_days}
              >
                {editingSub ? 'Сохранить' : 'Создать'}
              </button>
            </div>
          </div>
        </div>
      )}
    </AdminShell>
  )
}
