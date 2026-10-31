import { useState, useEffect, type ReactNode } from 'react'
import { useParams, useSearchParams, Link } from 'react-router-dom'
import { MapPin, Phone, Clock, Navigation, DoorOpen, X, ChevronRight, ArrowRight, Sparkles } from 'lucide-react'
import { api, type BusinessDetail, type BlogArticleSummary, type EmployeeWithPricing } from '../../api/client'
import { AirFrame, HeroArt } from './AirFrame'
import { parsePhotoList, photoSrc, photoThumbSrc } from './airPhotos'
import { applySeo, shortText } from '../../utils/seo'

function withSeoRef(path: string, reference: string | null): string {
  return reference ? path + (path.includes('?') ? '&' : '?') + 'seo_ref=' + encodeURIComponent(reference) : path
}

function linkifyText(text: string): ReactNode {
  const urlPattern = /(https?:\/\/[^\s]+|(?:vk\.com|t\.me)\/[^\s]+)/gi
  const parts = text.split(urlPattern)
  if (parts.length === 1) return text
  return parts.map((part, i) => {
    if (/^(https?:\/\/|vk\.com\/|t\.me\/)/i.test(part)) {
      const href = part.startsWith('http') ? part : `https://${part}`
      return <a key={i} href={href} target="_blank" rel="noopener noreferrer">{part}</a>
    }
    return part
  })
}

function formatWorkingHours(start?: string, end?: string) {
  if (!start || !end) return null
  const fmt = (t: string) => t.substring(0, 5)
  return `${fmt(start)}–${fmt(end)}`
}

function resourcePhotos(emp: EmployeeWithPricing): string[] {
  const extra = (emp as { photo_url?: string }).photo_url
  return Array.from(new Set([emp.primary_photo, ...parsePhotoList(emp.photos), extra].filter((p): p is string => !!p && p.trim().length > 0)))
}

function formatPrice(n: number) {
  return `${n.toLocaleString('ru-RU')} ₽`
}

function ResourceSheet({ emp, isVenue, businessId, seoRef, onClose }: { emp: EmployeeWithPricing; isVenue: boolean; businessId: number; seoRef: string | null; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const photos = resourcePhotos(emp)
  const details = [
    emp.capacity != null ? `До ${emp.capacity} чел.` : null,
    emp.area_sqm != null ? `${emp.area_sqm} м²` : null,
  ].filter(Boolean)
  return (
    <div className="bf-sheet-ov" role="presentation" onClick={onClose}>
      <div className="bf-sheet" role="dialog" aria-modal="true" aria-label={emp.name} onClick={(e) => e.stopPropagation()}>
        <div className="bf-sheet-grip" />
        <div className="bf-sheet-head">
          <h3>{emp.name}</h3>
          <button type="button" className="bf-sheet-close" onClick={onClose} aria-label="Закрыть"><X size={17} /></button>
        </div>
        {photos.length > 0 && (
          <div className="bf-gallery" aria-label={`Фотографии: ${emp.name}`}>
            {photos.map((photo, i) => (
              <a key={`${photo}-${i}`} href={photoSrc(photo)} target="_blank" rel="noopener noreferrer" aria-label={`Открыть фото ${i + 1} из ${photos.length}`}>
                <img src={photoSrc(photo)} alt={`${emp.name} — фото ${i + 1}`} loading={i === 0 ? 'eager' : 'lazy'} />
              </a>
            ))}
          </div>
        )}
        {emp.specialization && <div className="bf-sheet-sub">{emp.specialization}</div>}
        {emp.description?.trim() && <p>{linkifyText(emp.description)}</p>}
        {details.length > 0 && <div className="details">{details.map((d) => <div key={d}>{d}</div>)}</div>}
        <Link to={withSeoRef(`/book/${businessId}?employee=${emp.id}`, seoRef)} className="bf-primary bf-sheet-cta">
          {isVenue ? 'Забронировать' : 'Записаться'} <ArrowRight />
        </Link>
      </div>
    </div>
  )
}

export function BusinessPage() {
  const { id } = useParams<{ id: string }>()
  const [searchParams] = useSearchParams()
  const candidate = searchParams.get('seo_ref')
  // The server verifies the signature; the browser only limits URL propagation.
  const seoRef = candidate && /^[A-Za-z0-9_.-]{1,512}$/.test(candidate) ? candidate : null
  const [business, setBusiness] = useState<BusinessDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [selectedEmp, setSelectedEmp] = useState<EmployeeWithPricing | null>(null)
  const [aboutOpen, setAboutOpen] = useState(false)
  const [blogArticles, setBlogArticles] = useState<BlogArticleSummary[]>([])

  useEffect(() => {
    if (!id) return
    api
      .getBusiness(Number(id))
      .then(setBusiness)
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [id])

  useEffect(() => {
    if (!id) return
    const path = `/business/${id}`
    if (error) {
      applySeo({ path, noindex: true })
      return
    }
    if (!business) return
    const where = business.address?.trim()
    const fallback = `Онлайн-запись в «${business.name}»${where ? `, ${where}` : ''}: выберите услугу и удобное время без звонков.`
    applySeo({
      path,
      title: `${business.name} — онлайн-запись | ServGo`,
      description: shortText(business.description?.trim() || fallback),
    })
  }, [id, business, error])


  useEffect(() => {
    if (!id || !Number.isSafeInteger(Number(id))) return
    let active = true
    api.getBusinessBlog(Number(id))
      .then((result) => { if (active) setBlogArticles(result.articles) })
      .catch(() => { if (active) setBlogArticles([]) })
    return () => { active = false }
  }, [id])

  if (loading) {
    return (
      <AirFrame label="Загрузка" backTo="/">
        <section className="bf-screen bf-screen--business" aria-busy="true">
          <div className="bf-page-hero bf-skel" />
          <div className="bf-inner bf-page">
            <span className="bf-skel" style={{ width: '58%', height: 20, marginTop: 14 }} />
            <span className="bf-skel" style={{ width: '40%', height: 12, marginTop: 8 }} />
            {[1, 2, 3].map((i) => <span key={i} className="bf-skel" style={{ height: 48, marginTop: 12 }} />)}
          </div>
        </section>
      </AirFrame>
    )
  }

  if (error || !business) {
    return (
      <AirFrame label="Место не найдено" backTo="/">
        <section className="bf-screen">
          <div className="bf-fallback">
            <span className="bf-stage-symbol"><MapPin /></span>
            <h2 className="bf-h2">Место не найдено</h2>
            <p className="bf-intro">Возможно, ссылка устарела или запись временно закрыта.</p>
            <Link to="/" className="bf-primary" style={{ flex: 'none', width: '100%' }}>На главную</Link>
          </div>
        </section>
      </AirFrame>
    )
  }

  const isVenue = business.business_type === 'venue_rental'
  const photos = parsePhotoList(business.photos)
  const cover = photos[0] || business.photo_url
  const workingHours = business.working_hours || formatWorkingHours(business.working_hours_start, business.working_hours_end)
  const services = (business.services || []).filter((s) => !s.is_additional)
  const employees = business.employees || []
  const plainName = business.name.replace(/[«»"']/g, '').trim()
  const description = business.description?.trim() || ''
  const longDescription = description.length > 180

  return (
    <AirFrame
      label={business.name}
      backTo="/"
      overlay={selectedEmp && (
        <ResourceSheet emp={selectedEmp} isVenue={isVenue} businessId={business.id} seoRef={seoRef} onClose={() => setSelectedEmp(null)} />
      )}
    >
      <section className="bf-screen bf-screen--business" aria-label={business.name}>
        <div className="bf-inner bf-page bf-page--flush">
          <div className="bf-page-hero">
            {cover ? <img src={photoSrc(cover)} alt={business.name} /> : <HeroArt />}
            <div className="bf-hero-fade" />
          </div>
          <div className="bf-panel bf-panel--page">
            <div className="bf-logo" aria-hidden="true">
              {photos[1] ? (
                <img src={photoSrc(photos[1])} alt="" />
              ) : (
                <>
                  <div className="sym">{plainName.charAt(0).toUpperCase()}</div>
                  <div className="cap">{plainName.toUpperCase()}</div>
                </>
              )}
            </div>
            <h1 className="bf-h1">{business.name}</h1>

            {description && (
              <p className={`bf-desc${longDescription && !aboutOpen ? ' is-clamped' : ''}`}>{linkifyText(description)}</p>
            )}
            {longDescription && (
              <button type="button" className="bf-about" onClick={() => setAboutOpen((v) => !v)} aria-expanded={aboutOpen}>
                {aboutOpen ? 'Свернуть' : 'Читать полностью'}
              </button>
            )}

            <div className="bf-facts">
              {business.address && (
                <a className="bf-fact" href={`https://yandex.ru/maps/?text=${encodeURIComponent(business.address)}`} target="_blank" rel="noopener noreferrer">
                  <span className="bf-choice-icon"><MapPin /></span>
                  <span className="bf-choice-body"><span className="bf-choice-name">{business.address}</span><span className="bf-choice-details">Открыть на карте</span></span>
                  <ChevronRight className="bf-chevron" />
                </a>
              )}
              {workingHours && (
                <div className="bf-fact">
                  <span className="bf-choice-icon"><Clock /></span>
                  <span className="bf-choice-body"><span className="bf-choice-name">{workingHours}</span><span className="bf-choice-details">Часы работы</span></span>
                </div>
              )}
              {business.phone && (
                <a className="bf-fact" href={`tel:${business.phone}`}>
                  <span className="bf-choice-icon"><Phone /></span>
                  <span className="bf-choice-body"><span className="bf-choice-name">{business.phone}</span><span className="bf-choice-details">Позвонить</span></span>
                  <ChevronRight className="bf-chevron" />
                </a>
              )}
              {business.directions && (
                <div className="bf-fact bf-fact--text">
                  <span className="bf-choice-icon"><Navigation /></span>
                  <span className="bf-choice-body"><span className="bf-choice-details">Как добраться</span><span className="bf-fact-text">{linkifyText(business.directions)}</span></span>
                </div>
              )}
            </div>

            {services.length > 0 && (
              <section className="bf-block" aria-label="Услуги">
                <div className="bf-kicker">{isVenue ? 'Форматы аренды' : 'Услуги'} · {services.length}</div>
                <div className="bf-list">
                  {services.slice(0, 6).map((s) => (
                    <Link key={s.id} to={withSeoRef(`/book/${business.id}?service=${s.id}`, seoRef)} className="bf-option bf-option--link">
                      <span className="bf-option-data">
                        <span className="bf-option-title">{s.name}</span>
                        <span className="bf-option-meta">
                          {[s.duration_minutes ? `${s.duration_minutes} мин` : null, s.price ? formatPrice(s.price) + (s.is_hourly_price ? '/ч' : '') : null].filter(Boolean).join(' · ') || 'Цена уточняется'}
                        </span>
                      </span>
                      <ChevronRight className="bf-chevron" />
                    </Link>
                  ))}
                </div>
                {services.length > 6 && (
                  <Link to={withSeoRef(`/book/${business.id}`, seoRef)} className="bf-about" style={{ display: 'inline-block', textDecoration: 'none' }}>Все услуги — {services.length}</Link>
                )}
              </section>
            )}


            {blogArticles.length > 0 && (
              <section className="bf-block" aria-label="Статьи и советы">
                <div className="bf-kicker">Статьи и советы</div>
                <div className="bf-list">
                  {blogArticles.slice(0, 3).map((item) => (
                    <Link key={item.slug} to={`/business/${business.id}/blog/${item.slug}`} className="bf-option bf-option--link">
                      <span className="bf-option-data">
                        <span className="bf-option-title">{item.title}</span>
                        <span className="bf-option-meta">{item.description || item.lead}</span>
                      </span>
                      <ChevronRight className="bf-chevron" />
                    </Link>
                  ))}
                </div>
                <Link to={`/business/${business.id}/blog`} className="bf-about" style={{ display: 'inline-block', textDecoration: 'none' }}>
                  Все статьи
                </Link>
              </section>
            )}

            {employees.length > 0 && (
              <section className="bf-block" aria-label={isVenue ? 'Залы' : 'Специалисты'}>
                <div className="bf-kicker">{isVenue ? 'Залы' : 'Специалисты'} · {employees.length}</div>
                <div className="bf-list">
                  {employees.map((emp) => {
                    const p = resourcePhotos(emp)[0]
                    return (
                      <button key={emp.id} type="button" className="bf-option" onClick={() => setSelectedEmp(emp)}>
                        <span className={`bf-portrait${isVenue ? ' is-venue' : ''}`} aria-hidden="true">
                          {p ? <img src={photoThumbSrc(p)} alt="" loading="lazy" decoding="async" /> : isVenue ? <DoorOpen size={18} /> : emp.name.charAt(0).toUpperCase()}
                        </span>
                        <span className="bf-option-data">
                          <span className="bf-option-title">{emp.name}</span>
                          {(emp.specialization || emp.capacity != null) && (
                            <span className="bf-option-meta">
                              {emp.specialization || ''}{emp.capacity != null ? `${emp.specialization ? ' · ' : ''}до ${emp.capacity} чел.` : ''}
                            </span>
                          )}
                        </span>
                        <ChevronRight className="bf-chevron" />
                      </button>
                    )
                  })}
                </div>
              </section>
            )}
          </div>
        </div>
        <div className="bf-bottombar">
          <Link to={`/chat?business_id=${business.id}`} className="bf-secondary"><Sparkles />Помочь выбрать</Link>
          <Link to={`/book/${business.id}`} className="bf-primary">{isVenue ? 'Забронировать' : 'Записаться'} <ArrowRight /></Link>
        </div>
      </section>
    </AirFrame>
  )
}
