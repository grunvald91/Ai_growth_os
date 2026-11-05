import { useState, type FormEvent } from 'react'
import { X } from 'lucide-react'
import { api } from '../api/client'
import { errorMessage } from '../authUtils'
import { rememberCreatedBusiness } from '../hooks/useAdminBusiness'

// Те же типы, что в кнопках бота («Добавить бизнес»). Часы работы, перерыв и шаг записи
// подставляются из шаблона типа, потом правятся в «Настройках».
const BUSINESS_TYPES: { value: string; label: string }[] = [
  { value: 'salon', label: 'Салон красоты' },
  { value: 'psychologist', label: 'Психолог' },
  { value: 'auto_service', label: 'Автосервис' },
  { value: 'tire_service', label: 'Шиномонтаж' },
  { value: 'spa', label: 'Массаж/СПА' },
  { value: 'medical', label: 'Медицинская клиника' },
  { value: 'education', label: 'Обучение' },
  { value: 'venue_rental', label: 'Аренда помещений' },
  { value: 'other', label: 'Другое' },
]

/**
 * Нижняя шторка «Новый бизнес» для админки: название, тип, по желанию адрес и телефон.
 * Поля и правила те же, что при создании бизнеса в боте.
 */
export function CreateBusinessSheet({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState('')
  const [type, setType] = useState('')
  const [address, setAddress] = useState('')
  const [phone, setPhone] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (busy) return
    if (!name.trim()) { setError('Введите название бизнеса'); return }
    if (!type) { setError('Выберите тип бизнеса'); return }
    setBusy(true)
    setError('')
    try {
      const created = await api.createBusiness({
        name: name.trim(),
        business_type: type,
        address: address.trim() || undefined,
        phone: phone.trim() || undefined,
      })
      rememberCreatedBusiness(created.id)
      // Перезагружаем админку на настройках нового бизнеса: список бизнесов обновится везде.
      window.location.assign('/admin/settings')
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }

  return (
    <div className="ds-sheet-ov" onClick={onClose}>
      <form className="ds-sheet" role="dialog" aria-label="Новый бизнес" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <div className="ds-sheet-grip" />
        <div className="ds-sheet-head">
          <div>
            <h3>Новый бизнес</h3>
            <p>Часы работы и шаг записи подставятся по типу — потом их можно поменять в настройках.</p>
          </div>
          <button type="button" className="ds-icon-btn" onClick={onClose} aria-label="Закрыть"><X size={18} /></button>
        </div>

        <div className="ds-field">
          <label htmlFor="new-biz-name">Название</label>
          <input id="new-biz-name" className="ds-input" value={name} maxLength={255}
            onChange={(e) => setName(e.target.value)} placeholder="Например, Шиномонтаж на Мира" />
        </div>

        <div className="ds-field">
          <span className="ds-label">Тип бизнеса</span>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 6 }}>
            {BUSINESS_TYPES.map((t) => (
              <button key={t.value} type="button"
                className={type === t.value ? 'ds-btn ds-btn--on choice-accent selected' : 'ds-btn'}
                aria-pressed={type === t.value} onClick={() => setType(t.value)}>
                {t.label}
              </button>
            ))}
          </div>
        </div>

        <div className="ds-field">
          <label htmlFor="new-biz-address">Адрес (необязательно)</label>
          <input id="new-biz-address" className="ds-input" value={address} maxLength={500}
            onChange={(e) => setAddress(e.target.value)} />
        </div>

        <div className="ds-field">
          <label htmlFor="new-biz-phone">Телефон (необязательно)</label>
          <input id="new-biz-phone" className="ds-input" type="tel" inputMode="tel" value={phone} maxLength={30}
            onChange={(e) => setPhone(e.target.value)} placeholder="+7XXXXXXXXXX" />
        </div>

        {error && <p role="alert" style={{ color: 'var(--s-danger)', fontSize: 13, margin: '0 0 10px' }}>{error}</p>}

        <button type="submit" className="ds-btn ds-btn--primary ds-btn--block" disabled={busy}>
          {busy ? 'Создаём…' : 'Создать бизнес'}
        </button>
      </form>
    </div>
  )
}
