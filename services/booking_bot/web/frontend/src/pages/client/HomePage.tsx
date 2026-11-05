import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Briefcase, ChevronRight, Bell } from 'lucide-react'
import { api, type Business } from '../../api/client'
import { NotificationSetup } from '../../components/NotificationSetup'
import { AirFrame } from './AirFrame'
import { parsePhotoList, photoThumbSrc } from './airPhotos'

const nichePresentations = [
  { label: 'Бани и сауны', href: 'https://promo.servgo.ru/banya' },
  { label: 'Барбершопы', href: 'https://promo.servgo.ru/barbershop' },
  { label: 'Салоны красоты', href: 'https://promo.servgo.ru/beauty' },
  { label: 'Аренда залов и фотостудий', href: 'https://promo.servgo.ru/hall-rental' },
  { label: 'Психологи и коучи', href: 'https://promo.servgo.ru/psy' },
  { label: 'Шиномонтаж', href: 'https://promo.servgo.ru/shinomontazh' },
]

function formatWorkingHours(biz: Business) {
  if (biz.working_hours) return biz.working_hours
  if (biz.working_hours_start && biz.working_hours_end) {
    const fmt = (t: string) => t.substring(0, 5)
    return `${fmt(biz.working_hours_start)}–${fmt(biz.working_hours_end)}`
  }
  return null
}

function servicesLabel(n: number) {
  const m10 = n % 10, m100 = n % 100
  const word = m10 === 1 && m100 !== 11 ? 'услуга' : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? 'услуги' : 'услуг'
  return `${n} ${word}`
}

export function HomePage() {
  const [businesses, setBusinesses] = useState<Business[]>([])
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [failedPhotos, setFailedPhotos] = useState<Set<number>>(new Set())
  const isAuthenticated = Boolean(localStorage.getItem('token'))

  useEffect(() => {
    let active = true
    // Ошибка загрузки не должна выглядеть как «мест нет»: показываем её и даём повторить.
    let timer = 0
    const timeout = new Promise<never>((_, reject) => { timer = window.setTimeout(() => reject(new Error('timeout')), 20000) })
    Promise.race([api.getBusinesses(), timeout])
      .then((list) => { if (active) setBusinesses(list) })
      .catch(() => { if (active) setLoadFailed(true) })
      .finally(() => { window.clearTimeout(timer); if (active) setLoading(false) })
    return () => { active = false; window.clearTimeout(timer) }
  }, [attempt])

  const retry = () => {
    setLoadFailed(false)
    setLoading(true)
    setAttempt((n) => n + 1)
  }

  return (
    <AirFrame label="ServGo — онлайн-запись" tabs>
      <section className="bf-screen bf-screen--home" aria-label="Главная">
        <div className="bf-inner bf-page">
          <h1 className="bf-h2">Онлайн-запись<br /><span className="bf-accent">на услуги</span></h1>
          <p className="bf-intro">Выберите место и запишитесь на удобное время — без звонков.</p>

          <div className="bf-kicker">{loading ? 'Загружаем…' : !loadFailed && businesses.length > 0 ? `Места для записи · ${businesses.length}` : 'Места для записи'}</div>

          {loading ? (
            <div className="bf-list" aria-busy="true">
              {[1, 2, 3].map((i) => (
                <div key={i} className="bf-biz is-skeleton">
                  <span className="bf-thumb bf-skel" />
                  <span className="bf-biz-body">
                    <span className="bf-skel" style={{ width: '62%', height: 13 }} />
                    <span className="bf-skel" style={{ width: '88%', height: 10, marginTop: 6 }} />
                  </span>
                </div>
              ))}
            </div>
          ) : loadFailed ? (
            <div className="bf-empty-state" role="alert">
              <span className="bf-stage-symbol"><Briefcase /></span>
              <strong>Не удалось загрузить места</strong>
              <span>Проверьте интернет и попробуйте ещё раз</span>
              <button type="button" className="bf-chip" onClick={retry}>Повторить</button>
            </div>
          ) : businesses.length === 0 ? (
            <div className="bf-empty-state">
              <span className="bf-stage-symbol"><Briefcase /></span>
              <strong>Пока нет мест для записи</strong>
              <span>Скоро здесь появятся салоны и студии</span>
            </div>
          ) : (
            <nav className="bf-list" aria-label="Места для записи">
              {businesses.map((biz, index) => {
                const hours = formatWorkingHours(biz)
                const photos = parsePhotoList(biz.photos)
                const cover = photos.length > 0 && !failedPhotos.has(biz.id) ? photos[0] : null
                const meta = [hours, biz.services_count ? servicesLabel(biz.services_count) : null].filter(Boolean).join(' · ')
                return (
                  <Link key={biz.id} to={`/business/${biz.id}`} className="bf-biz">
                    <span className="bf-thumb" aria-hidden="true">
                      {cover ? (
                        <img
                          src={photoThumbSrc(cover)}
                          alt=""
                          width={68}
                          height={68}
                          loading={index < 4 ? 'eager' : 'lazy'}
                          decoding="async"
                          onError={() => setFailedPhotos((prev) => new Set(prev).add(biz.id))}
                        />
                      ) : (
                        biz.name.replace(/[«»"']/g, '').trim().charAt(0).toUpperCase()
                      )}
                    </span>
                    <span className="bf-biz-body">
                      <span className="bf-option-title">{biz.name}</span>
                      {biz.address && <span className="bf-biz-line">{biz.address}</span>}
                      {meta && <span className="bf-biz-line">{meta}</span>}
                    </span>
                    <ChevronRight className="bf-chevron" />
                  </Link>
                )
              })}
            </nav>
          )}

          {isAuthenticated && (
            <details className="bf-fold">
              <summary><Bell />Уведомления на этом устройстве</summary>
              <p>Включите их, чтобы получать напоминания о записях. Подходит и клиентам, и сотрудникам.</p>
              <NotificationSetup />
            </details>
          )}

          <footer className="bf-foot">
            <div className="bf-kicker">ServGo для бизнеса</div>
            <p className="bf-foot-pitch">
              ИИ-платформа роста для бизнеса услуг: онлайн-запись, заполнение пустых окон, смены и зарплата
              сотрудников, контент и ответы клиентам в соцсетях, разведка конкурентов и аналитика — в одном месте.
            </p>
            <nav className="bf-chips" aria-label="Презентации ServGo по нишам">
              {nichePresentations.map((item) => (
                <a key={item.href} href={item.href} target="_blank" rel="noopener noreferrer" className="bf-chip">{item.label}</a>
              ))}
            </nav>
            <div className="bf-contacts">
              <span>Связаться</span>
              <a href="mailto:grunvaldlab@gmail.com">grunvaldlab@gmail.com</a>
              <a href="tel:+79023911399">8 902 391-13-99</a>
              <a href="https://t.me/servgo_smena_bot" target="_blank" rel="noopener noreferrer">Смены и зарплата — @servgo_smena_bot</a>
            </div>
          </footer>
        </div>
      </section>
    </AirFrame>
  )
}
