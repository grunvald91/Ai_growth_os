import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowRight, BookOpen, ChevronRight } from 'lucide-react'
import { api, type BusinessDetail, type BusinessBlogFeed, type BusinessBlogArticle } from '../../api/client'
import { AirFrame } from './AirFrame'
import { applySeo, shortText } from '../../utils/seo'

/** Published articles belong to the existing BookingBot business profile, not a second company directory. */
export function BusinessBlogPage() {
  const { id, slug } = useParams<{ id: string; slug?: string }>()
  const businessId = Number(id)
  const base = `/business/${id}`
  const [business, setBusiness] = useState<BusinessDetail | null>(null)
  const [feed, setFeed] = useState<BusinessBlogFeed | null>(null)
  const [article, setArticle] = useState<BusinessBlogArticle | null>(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const companyLink = slug && article?.company_url ? article.company_url : base
  const bookingLink = slug && article?.cta_url ? article.cta_url : `/book/${businessId}`

  useEffect(() => {
    let active = true
    setLoading(true)
    setNotFound(false)
    setFeed(null)
    setArticle(null)
    if (!Number.isSafeInteger(businessId) || businessId <= 0) {
      setNotFound(true)
      setLoading(false)
      return
    }
    async function load() {
      try {
        const [company, content] = await Promise.all([
          api.getBusiness(businessId),
          slug ? api.getBusinessBlogArticle(businessId, slug) : api.getBusinessBlog(businessId),
        ])
        if (!active) return
        setBusiness(company)
        if (slug) setArticle(content as BusinessBlogArticle)
        else setFeed(content as BusinessBlogFeed)
      } catch {
        if (active) setNotFound(true)
      } finally {
        if (active) setLoading(false)
      }
    }
    void load()
    return () => { active = false }
  }, [businessId, slug])

  useEffect(() => {
    const path = slug ? `${base}/blog/${slug}` : `${base}/blog`
    if (notFound) {
      applySeo({ path, noindex: true })
      return
    }
    if (!business || (slug ? !article : !feed)) return
    applySeo({
      path,
      title: slug ? `${article!.title} — ${business.name} | ServGo` : `Статьи и советы — ${business.name} | ServGo`,
      description: shortText(slug ? article!.description : `Статьи и советы компании «${business.name}»`),
    })
  }, [base, slug, business, feed, article, notFound])

  if (loading) return (
    <AirFrame label="Статьи" backTo={base}>
      <section className="bf-screen"><div className="bf-inner bf-page"><p className="bf-intro" role="status">Загружаем статьи…</p></div></section>
    </AirFrame>
  )

  if (notFound || !business || (slug ? !article : !feed)) return (
    <AirFrame label="Статьи" backTo={base}>
      <section className="bf-screen"><div className="bf-inner bf-page">
        <h1 className="bf-h2">Статьи пока недоступны</h1>
        <p className="bf-intro">Вернитесь на страницу компании, чтобы посмотреть услуги и записаться.</p>
        <Link to={base} className="bf-primary">О компании <ArrowRight /></Link>
      </div></section>
    </AirFrame>
  )

  return (
    <AirFrame label={business.name} backTo={slug ? `${base}/blog` : base}>
      <section className="bf-screen bf-screen--business" aria-label={slug ? article!.title : 'Блог компании'}>
        <div className="bf-inner bf-page">
          <div className="bf-panel bf-panel--page" style={{ padding: '20px 16px', marginTop: 16 }}>
            <Link to={companyLink} className="bf-about" style={{ textDecoration: 'none' }}>{business.name}</Link>
            {slug && article ? (
              <article>
                <h1 className="bf-h1">{article.title}</h1>
                <p className="bf-intro">{article.lead}</p>
                {article.direct_answer && <p>{article.direct_answer}</p>}
                {article.sections.map((section, index) => (
                  <section key={index} className="bf-block">
                    <h2 className="bf-h2">{section.title}</h2>
                    {section.paragraphs.map((paragraph, i) => <p key={i}>{paragraph}</p>)}
                    {section.bullets.length > 0 && <ul>{section.bullets.map((bullet, i) => <li key={i}>{bullet}</li>)}</ul>}
                  </section>
                ))}
                {article.faq.length > 0 && <section className="bf-block" aria-label="Частые вопросы">
                  <h2 className="bf-h2">Частые вопросы</h2>
                  {article.faq.map((item, i) => <div key={i}><h3>{item.question}</h3><p>{item.answer}</p></div>)}
                </section>}
                {article.closing && <p>{article.closing}</p>}
                <section className="bf-block" aria-label="Запись на услуги">
                  <h2 className="bf-h2">{article.cta || 'Запишитесь на услугу'}</h2>
                  <p className="bf-intro">Выберите услугу и подходящее время в {business.name}.</p>
                  <Link to={companyLink} className="bf-secondary">О компании</Link>
                  <Link to={bookingLink} className="bf-primary">Записаться <ArrowRight /></Link>
                </section>
                <Link to={`${base}/blog`} className="bf-about">Все статьи</Link>
              </article>
            ) : (
              <section aria-label="Статьи компании">
                <div className="bf-kicker"><BookOpen size={17} /> Статьи и советы</div>
                <h1 className="bf-h1">Блог {business.name}</h1>
                {feed!.articles.length === 0 ? <p className="bf-intro">Статьи ещё не опубликованы.</p> : (
                  <div className="bf-list">
                    {feed!.articles.map((item) => (
                      <Link key={item.slug} to={`${base}/blog/${item.slug}`} className="bf-option bf-option--link">
                        <span className="bf-option-data">
                          <span className="bf-option-title">{item.title}</span>
                          <span className="bf-option-meta">{item.description || item.lead}</span>
                        </span>
                        <ChevronRight className="bf-chevron" />
                      </Link>
                    ))}
                  </div>
                )}
              </section>
            )}
          </div>
        </div>
        <div className="bf-bottombar">
          <Link to={companyLink} className="bf-secondary">О компании</Link>
          <Link to={bookingLink} className="bf-primary">Записаться <ArrowRight /></Link>
        </div>
      </section>
    </AirFrame>
  )
}
