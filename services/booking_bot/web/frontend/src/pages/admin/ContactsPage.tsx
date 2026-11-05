import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { CalendarClock, Check, ChevronLeft, ChevronRight, Mail, Phone, Plus, Search, StickyNote, Trash2, UserRound, Users, X } from 'lucide-react'
import { api, type CRMContact, type CRMContactDetail, type CRMContactInput } from '../../api/client'
import { AdminShell } from '../../components/AdminShell'
import { BusinessSelector } from '../../components/BusinessSelector'
import { useAdminBusiness } from '../../hooks/useAdminBusiness'
import './contacts.css'

type Stage = CRMContact['stage']
type Filter = Stage | 'all' | 'follow_up'
type EditorMode = 'new' | 'edit' | null
type ContactDraft = {
  name: string
  phone: string
  email: string
  notes: string
  stage: Stage
  follow_up_on: string
}

const STAGES: { value: Stage; label: string }[] = [
  { value: 'new', label: 'Новый' },
  { value: 'active', label: 'В работе' },
  { value: 'vip', label: 'Постоянный' },
  { value: 'paused', label: 'Неактивный' },
]
const FILTERS: { value: Filter; label: string }[] = [
  { value: 'all', label: 'Все' },
  { value: 'follow_up', label: 'Связаться' },
  { value: 'new', label: 'Новые' },
  { value: 'active', label: 'В работе' },
  { value: 'vip', label: 'Постоянные' },
  { value: 'paused', label: 'Неактивные' },
]
const EMPTY: ContactDraft = { name: '', phone: '', email: '', notes: '', stage: 'new', follow_up_on: '' }
const STATUS: Record<string, string> = {
  pending: 'Ожидает подтверждения',
  confirmed: 'Подтверждена',
  completed: 'Завершена',
  cancelled: 'Отменена',
  pending_prepayment: 'Ожидает предоплаты',
  prepayment_sent: 'Ожидает проверки оплаты',
  prepayment_confirmed: 'Предоплата подтверждена',
}

function day(date: string | null | undefined): string {
  if (!date) return '—'
  const d = new Date(date.slice(0, 10) + 'T12:00:00')
  return Number.isNaN(d.getTime()) ? date : d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' })
}
function stageLabel(stage: Stage) {
  return STAGES.find(s => s.value === stage)?.label ?? stage
}
function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]?.toLocaleUpperCase('ru-RU')).join('') || '•'
}
function formFor(contact?: CRMContact): ContactDraft {
  if (!contact) return { ...EMPTY }
  return { name: contact.name, phone: contact.phone || '', email: contact.email || '',
    notes: contact.notes || '', stage: contact.stage, follow_up_on: contact.follow_up_on || '' }
}
function payload(form: ContactDraft): CRMContactInput {
  return { name: form.name.trim(), phone: form.phone.trim() || null,
    email: form.email.trim() || null, notes: form.notes.trim(),
    stage: form.stage, follow_up_on: form.follow_up_on || null }
}
function errorText(e: unknown) {
  if (!(e instanceof Error)) return 'Не удалось выполнить действие'
  try {
    const parsed: { detail?: string | Array<{ msg?: string }> } = JSON.parse(e.message)
    if (typeof parsed.detail === 'string') return parsed.detail
    if (Array.isArray(parsed.detail)) return parsed.detail.map(v => v.msg).filter(Boolean).join('; ')
  } catch { /* Standard transport error. */ }
  return e.message || 'Не удалось выполнить действие'
}

function Editor({ mode, current, busy, onClose, onSave, onDelete }: {
  mode: Exclude<EditorMode, null>;
  current?: CRMContactDetail | null;
  busy: boolean;
  onClose: () => void;
  onSave: (body: CRMContactInput) => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const [form, setForm] = useState<ContactDraft>(() => formFor(mode === 'edit' ? current || undefined : undefined))
  const [error, setError] = useState('')
  const update = (part: Partial<ContactDraft>) => setForm(f => ({ ...f, ...part }))
  const save = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    if (!form.name.trim()) { setError('Введите имя клиента'); return }
    try { await onSave(payload(form)) } catch (err) { setError(errorText(err)) }
  }
  return (
    <div className="crm-overlay" onMouseDown={e => { if (e.target === e.currentTarget && !busy) onClose() }}>
      <section className="crm-dialog" role="dialog" aria-modal="true" aria-labelledby="crm-editor-title">
        <div className="crm-dialog-header">
          <div>
            <h2 className="ds-h2" id="crm-editor-title">{mode === 'new' ? 'Новый контакт' : 'Карточка клиента'}</h2>
            <div className="ds-sub">{mode === 'new' ? 'Контакт без записи — для будущей связи' : 'Изменения видны только этому бизнесу'}</div>
          </div>
          <button className="ds-icon-btn" disabled={busy} aria-label="Закрыть" onClick={onClose}><X size={20} /></button>
        </div>
        <form onSubmit={save} className="crm-editor-form">
          <label className="ds-field"><span className="ds-label">Имя *</span>
            <input className="ds-input" autoFocus required maxLength={255} value={form.name} onChange={e => update({ name: e.target.value })} />
          </label>
          <div className="crm-form-pair">
            <label className="ds-field"><span className="ds-label">Телефон</span>
              <input className="ds-input" type="tel" maxLength={32} placeholder="+7…" value={form.phone} onChange={e => update({ phone: e.target.value })} />
            </label>
            <label className="ds-field"><span className="ds-label">Email</span>
              <input className="ds-input" type="email" maxLength={320} placeholder="example@mail.ru" value={form.email} onChange={e => update({ email: e.target.value })} />
            </label>
          </div>
          <div className="crm-form-pair">
            <label className="ds-field"><span className="ds-label">Статус</span>
              <select className="ds-input" value={form.stage} onChange={e => update({ stage: e.target.value as Stage })}>
                {STAGES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </label>
            <label className="ds-field"><span className="ds-label">Связаться до / в дату</span>
              <input className="ds-input" type="date" value={form.follow_up_on} onChange={e => update({ follow_up_on: e.target.value })} />
            </label>
          </div>
          <label className="ds-field"><span className="ds-label">Заметка для команды</span>
            <textarea className="ds-input crm-notes-input" rows={4} maxLength={5000}
              placeholder="Предпочтения, договорённости, что обсудить при следующем обращении…"
              value={form.notes} onChange={e => update({ notes: e.target.value })} />
          </label>
          {error && <p className="crm-error" role="alert">{error}</p>}
          <div className="crm-editor-actions">
            {mode === 'edit' && current?.key.startsWith('m:') && (
              <button type="button" className="ds-btn ds-btn--danger" disabled={busy}
                onClick={async () => {
                  if (!window.confirm('Удалить этот контакт? Действие нельзя отменить.')) return
                  try { await onDelete() } catch (err) { setError(errorText(err)) }
                }}><Trash2 size={15} /> Удалить</button>
            )}
            <button type="button" className="ds-btn ds-btn--ghost" disabled={busy} onClick={onClose}>Отмена</button>
            <button type="submit" className="ds-btn ds-btn--primary" disabled={busy || !form.name.trim()}>
              <Check size={15} />{busy ? 'Сохраняем…' : 'Сохранить'}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}

function ContactDetail({ contact, loading, error, onEdit, onBack, onRetry }: {
  contact: CRMContactDetail | null;
  loading: boolean;
  error: string;
  onEdit: () => void;
  onBack: () => void;
  onRetry: () => void;
}) {
  const back = <button className="crm-back" onClick={onBack}><ChevronLeft size={18} /> Все клиенты</button>
  if (loading) return <div className="crm-detail-pad">{back}<div className="ds-sub" role="status">Загружаем историю клиента…</div></div>
  if (error) return <div className="crm-detail-pad">{back}<p role="alert" className="crm-error">{error}</p>
    <button className="ds-btn ds-btn--ghost" onClick={onRetry}>Повторить</button></div>
  if (!contact) return <div className="crm-blank">
    <UserRound size={34} strokeWidth={1.4} />
    <strong>Карточка клиента</strong>
    <p>Выберите контакт, чтобы увидеть записи, договорённости и следующие действия.</p>
  </div>
  const tel = contact.phone?.replace(/[^+\d]/g, '') || ''
  return <div className="crm-detail-pad">
    {back}
    <div className="crm-profile-header">
      <span className="crm-avatar crm-avatar--large">{initials(contact.name)}</span>
      <div className="crm-profile-name">
        <h2 className="ds-h2">{contact.name}</h2>
        <span className={'crm-stage crm-stage--' + contact.stage}>{stageLabel(contact.stage)}</span>
      </div>
      <button className="ds-btn ds-btn--ghost ds-btn--sm" onClick={onEdit}>Изменить</button>
    </div>
    <div className="crm-links">
      {tel && <a className="ds-btn ds-btn--primary" href={'tel:' + tel}><Phone size={15} /> Позвонить</a>}
      {contact.email && <a className="ds-btn ds-btn--ghost" href={'mailto:' + contact.email}><Mail size={15} /> Email</a>}
      {!tel && !contact.email && <p className="ds-sub">Контакты ещё не указаны</p>}
    </div>
    <div className="crm-stat-grid">
      <div className="ds-tile"><span className="num">{contact.bookings_count}</span><span className="lbl">Записей</span></div>
      <div className="ds-tile"><span className="num">{contact.completed_count}</span><span className="lbl">Завершено</span></div>
      <div className="ds-tile"><span className="num">{day(contact.next_booking)}</span><span className="lbl">Ближайшая запись</span></div>
    </div>
    {contact.follow_up_on && <div className="crm-info-card crm-remind">
      <CalendarClock size={17} /><div><strong>Следующий контакт</strong><span>{day(contact.follow_up_on)}</span></div>
    </div>}
    <div className="crm-section-heading"><StickyNote size={16} /> Заметки</div>
    <div className="crm-info-card crm-note">
      {contact.notes ? <p>{contact.notes}</p> : <p className="crm-muted">Нет заметок. Добавьте предпочтения или договорённости с клиентом.</p>}
      <button className="crm-inline-action" onClick={onEdit}>{contact.notes ? 'Редактировать' : 'Добавить заметку'}</button>
    </div>
    <div className="crm-section-heading"><CalendarClock size={16} /> История записей</div>
    {contact.history.length === 0 ? (
      <div className="crm-info-card crm-muted">Записей пока нет.</div>
    ) : (
      <div className="crm-history">
        {contact.history.map(booking => <div className="crm-history-row" key={booking.id}>
          <div className="crm-history-date"><strong>{day(booking.date)}</strong><span>{booking.time}</span></div>
          <div className="crm-history-body">
            <strong>{booking.service}</strong>
            <span>{booking.employee || 'Мастер не назначен'}</span>
          </div>
          <div className="crm-history-state">{STATUS[booking.status] || booking.status}</div>
        </div>)}
        {contact.history_has_more && <div className="ds-sub" style={{ padding: 12 }}>
          Показаны последние 100 записей.
        </div>}
      </div>
    )}
  </div>
}

function ContactsWorkspace({ businessId }: { businessId: number }) {
  const [search, setSearch] = useState('')
  const [term, setTerm] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [page, setPage] = useState(1)
  const [items, setItems] = useState<CRMContact[]>([])
  const [total, setTotal] = useState(0)
  const [listLoading, setListLoading] = useState(true)
  const [listError, setListError] = useState('')
  const [active, setActive] = useState<string | null>(null)
  const [contact, setContact] = useState<CRMContactDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const [editor, setEditor] = useState<EditorMode>(null)
  const [saving, setSaving] = useState(false)
  const listRequestId = useRef(0)
  const detailRequestId = useRef(0)

  useEffect(() => {
    const timer = window.setTimeout(() => { setTerm(search); setPage(1) }, 250)
    return () => window.clearTimeout(timer)
  }, [search])

  const loadList = useCallback(async () => {
    const requestId = ++listRequestId.current
    setListLoading(true); setListError('')
    try {
      const data = await api.getCRMContacts(businessId, term, filter, page)
      if (requestId === listRequestId.current) {
        setItems(data.items); setTotal(data.total)
        const lastPage = Math.max(1, Math.ceil(data.total / data.page_size))
        if (page > lastPage) setPage(lastPage)
      }
    } catch (err) {
      if (requestId === listRequestId.current) { setItems([]); setListError(errorText(err)) }
    } finally {
      if (requestId === listRequestId.current) setListLoading(false)
    }
  }, [businessId, term, filter, page])

  useEffect(() => { void loadList() }, [loadList])

  const loadDetail = useCallback(async (key: string) => {
    const requestId = ++detailRequestId.current
    setDetailLoading(true); setDetailError('')
    try {
      const data = await api.getCRMContact(businessId, key)
      if (requestId === detailRequestId.current) setContact(data)
    } catch (err) {
      if (requestId === detailRequestId.current) { setContact(null); setDetailError(errorText(err)) }
    } finally {
      if (requestId === detailRequestId.current) setDetailLoading(false)
    }
  }, [businessId])

  useEffect(() => {
    if (active) { setContact(null); void loadDetail(active) }
    else { detailRequestId.current++; setContact(null); setDetailLoading(false); setDetailError('') }
  }, [active, loadDetail])

  const save = async (body: CRMContactInput) => {
    setSaving(true)
    try {
      if (editor === 'new') {
        const created = await api.createCRMContact(businessId, body)
        await loadList()
        setActive(created.key)
      } else if (active) {
        const updated = await api.updateCRMContact(businessId, active, body)
        setContact(updated)
        await loadList()
      }
      setEditor(null)
    } finally { setSaving(false) }
  }
  const remove = async () => {
    if (!active) return
    setSaving(true)
    try {
      await api.deleteCRMContact(businessId, active)
      setEditor(null); setActive(null)
      await loadList()
    } finally { setSaving(false) }
  }
  const setStageFilter = (value: Filter) => { setFilter(value); setPage(1) }
  return <>
    <div className={'crm-workspace' + (active ? ' crm-has-active' : '')}>
      <section className="crm-list-panel" aria-label="Список клиентов">
        <div className="crm-toolbar">
          <div className="crm-search">
            <Search size={17} />
            <input aria-label="Поиск по имени, телефону или email" placeholder="Имя, телефон или email"
              value={search} onChange={e => setSearch(e.target.value)} />
            {search && <button aria-label="Очистить поиск" onClick={() => setSearch('')}><X size={16} /></button>}
          </div>
          <button className="ds-btn ds-btn--primary crm-add" onClick={() => setEditor('new')}><Plus size={17} /><span>Добавить</span></button>
        </div>
        <div className="crm-filters" role="group" aria-label="Фильтр клиентов">
          {FILTERS.map(f => <button key={f.value} className={'ds-chip' + (filter === f.value ? ' active' : '')}
            aria-pressed={filter === f.value} onClick={() => setStageFilter(f.value)}>{f.label}</button>)}
        </div>
        <div className="crm-list-heading"><span>{listLoading ? 'Загружаем…' : total + ' контактов'}</span>
          <span>Контакты доступны только вашему бизнесу</span></div>
        {listError && <div role="alert" className="crm-error">{listError}
          <button onClick={() => void loadList()} className="crm-inline-action">Повторить</button></div>}
        {!listError && !listLoading && !items.length && <div className="crm-empty">
          <Users size={27} strokeWidth={1.4} />
          <strong>{term ? 'Ничего не найдено' : 'Пока нет контактов'}</strong>
          <span>{term ? 'Измените запрос или фильтр.' : 'Клиенты из записей появятся здесь автоматически. Можно добавить контакт вручную.'}</span>
        </div>}
        <div className="crm-list" aria-live="polite">
          {items.map(item => <button key={item.key} className={'crm-contact-row' + (active === item.key ? ' is-active' : '')}
            onClick={() => setActive(item.key)}>
            <span className="crm-avatar">{initials(item.name)}</span>
            <span className="crm-contact-text">
              <strong>{item.name}</strong>
              <small>{item.phone || item.email || 'Контакты не указаны'}</small>
              <span className="crm-contact-foot">
                <span className={'crm-stage crm-stage--' + item.stage}>{stageLabel(item.stage)}</span>
                <small>{item.bookings_count ? item.bookings_count + ' зап.' : 'Без записей'}</small>
                {item.follow_up_on && <CalendarClock size={13} aria-label={'Связаться: ' + day(item.follow_up_on)} />}
              </span>
            </span><ChevronRight className="crm-chevron" size={17} />
          </button>)}
        </div>
        {total > 50 && <div className="crm-pager">
          <button className="ds-btn ds-btn--ghost ds-btn--sm" disabled={page === 1 || listLoading} onClick={() => setPage(v => v - 1)}><ChevronLeft size={16} /> Назад</button>
          <span className="ds-sub">Страница {page} из {Math.ceil(total / 50)}</span>
          <button className="ds-btn ds-btn--ghost ds-btn--sm" disabled={page * 50 >= total || listLoading} onClick={() => setPage(v => v + 1)}>Далее <ChevronRight size={16} /></button>
        </div>}
      </section>
      <section className="crm-detail-panel" aria-label="Информация о клиенте">
        <ContactDetail contact={contact} loading={detailLoading} error={detailError}
          onEdit={() => setEditor('edit')} onBack={() => setActive(null)}
          onRetry={() => active && void loadDetail(active)} />
      </section>
    </div>
    {editor && <Editor key={editor + (active || '')} mode={editor} current={editor === 'edit' ? contact : null}
      busy={saving} onClose={() => setEditor(null)} onSave={save} onDelete={remove} />}
  </>
}

export function ContactsPage() {
  const { businesses, selectedId, selectBusiness, loading } = useAdminBusiness()
  return <AdminShell title="Клиенты"
    headerExtra={<BusinessSelector businesses={businesses} selectedId={selectedId} onSelect={selectBusiness} />}
    pageClassName="crm-page">
    <div className="ds-context">
      <div className="ds-context-eyebrow">ServGo · Клиентская база</div>
      <div className="ds-context-help">Контакты, история записей, договорённости и следующий контакт — в одном месте.</div>
    </div>
    {selectedId ? <ContactsWorkspace key={selectedId} businessId={selectedId} /> :
      <div className="crm-empty">{loading ? 'Загружаем список бизнесов…' : 'У вас пока нет бизнеса'}</div>}
  </AdminShell>
}
