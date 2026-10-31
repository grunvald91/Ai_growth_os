import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { BookOpen } from 'lucide-react'
import { api, type SeoConversionSummary } from '../api/client'

/** Explicitly shows booked versus confirmed visits. Never labels them as search traffic. */
export function SeoAttributionCard({ businessId, days }: { businessId: number; days: number }) {
  const [data, setData] = useState<SeoConversionSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    let mounted = true
    setLoading(true)
    setError(false)
    setData(null)
    api.getSeoConversions(businessId, days)
      .then((result) => { if (mounted) setData(result) })
      .catch(() => { if (mounted) setError(true) })
      .finally(() => { if (mounted) setLoading(false) })
    return () => { mounted = false }
  }, [businessId, days])

  return (
    <section className="ds-card" aria-label="Записи из SEO-статей" style={{ padding: 14, marginTop: 12, marginBottom: 14 }}>
      <h2 className="ds-h3" style={{ margin: '0 0 8px', display: 'flex', gap: 8, alignItems: 'center' }}>
        <BookOpen size={17} /> Записи из статей
      </h2>
      {loading ? (
        <p className="ds-sub" role="status">Загружаем источники записей…</p>
      ) : error ? (
        <p className="ds-sub">Статистика статей пока недоступна. Остальные показатели работают независимо.</p>
      ) : !data || data.articles.length === 0 ? (
        <p className="ds-sub">За выбранный период подтверждённых переходов из статей в запись пока нет.</p>
      ) : (
        <>
          <p className="ds-sub" style={{ margin: '0 0 12px' }}>
            Создано: <strong>{data.total_created}</strong> · Подтверждено или завершено: <strong>{data.total_confirmed}</strong>
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {data.articles.map((item) => (
              <div key={item.article_slug} style={{ borderTop: '1px solid var(--s-surface-3)', paddingTop: 10 }}>
                <Link to={`/business/${businessId}/blog/${item.article_slug}`}
                  style={{ color: 'var(--s-accent)', fontSize: 13, fontWeight: 650, overflowWrap: 'anywhere' }}>
                  {item.article_slug.replace(/-/g, ' ')}
                </Link>
                <p className="ds-caption" style={{ margin: '5px 0 0' }}>
                  Создано: {item.bookings_created} · Подтверждено: {item.bookings_confirmed}
                </p>
              </div>
            ))}
          </div>
          <p className="ds-caption" style={{ margin: '12px 0 0' }}>
            Учитываются записи с подтверждённым источником статьи, а не все посещения поисковых систем.
          </p>
        </>
      )}
    </section>
  )
}
