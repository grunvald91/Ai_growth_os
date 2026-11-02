import { useState, useEffect, useMemo } from 'react'
import {
  Calendar,
  Wallet,
  Receipt,
  Activity,
  TrendingUp,
  TrendingDown,
  Scissors,
  User,
} from 'lucide-react'
import { api, type AnalyticsOverview, type AnalyticsDailyPoint } from '../../api/client'
import { useAdminBusiness } from '../../hooks/useAdminBusiness'
import { BusinessSelector } from '../../components/BusinessSelector'
import { AdminShell } from '../../components/AdminShell'
import { SeoAttributionCard } from '../../components/SeoAttributionCard'

// ── Helpers ──
const PERIOD_DAYS: Record<string, number> = { week: 7, month: 30, year: 365 }

function formatRub(n: number): string {
  return `${new Intl.NumberFormat('ru-RU').format(Math.round(n))} ₽`
}

function aggregateByWeek(daily: AnalyticsDailyPoint[]): AnalyticsDailyPoint[] {
  const weeks: AnalyticsDailyPoint[] = []
  for (let i = 0; i < daily.length; i += 7) {
    const slice = daily.slice(i, i + 7)
    weeks.push({
      date: slice[0].date,
      bookings: slice.reduce((s, d) => s + d.bookings, 0),
      revenue: slice.reduce((s, d) => s + d.revenue, 0),
    })
  }
  return weeks
}

// ── Delta badge ──
function Delta({ cur, prev, isPoints }: { cur: number; prev: number; isPoints?: boolean }) {
  if (cur === 0 && prev === 0) return null
  let value: number
  let label: string
  if (isPoints) {
    value = cur - prev
    label = `${value >= 0 ? '+' : ''}${value.toFixed(1)} п.п.`
  } else if (prev === 0 && cur > 0) {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2, fontSize: 10, fontWeight: 600, color: 'var(--s-success)' }}>
        <TrendingUp size={10} /> новое
      </span>
    )
  } else {
    value = ((cur - prev) / prev) * 100
    label = `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`
  }
  const positive = value >= 0
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2, fontSize: 10, fontWeight: 600, color: positive ? 'var(--s-success)' : 'var(--s-danger)' }}>
      {positive ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
      {label}
    </span>
  )
}

// ── KPI card ──
function KpiCard({ label, value, icon, color, cur, prev, isPoints }: {
  label: string; value: string; icon: React.ReactNode; color: string; cur: number; prev: number; isPoints?: boolean
}) {
  return (
    <div className="ds-card" style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span className="ds-sub">{label}</span>
        <span style={{ width: 32, height: 32, borderRadius: 9, flexShrink: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: 'var(--s-accent-soft)', color }}>
          {icon}
        </span>
      </div>
      <p style={{ fontSize: 20, fontWeight: 750, color: 'var(--s-text)', lineHeight: 1.1, margin: 0 }}>{value}</p>
      <Delta cur={cur} prev={prev} isPoints={isPoints} />
    </div>
  )
}

function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few
  return many
}

function formatBookings(n: number): string {
  return `${n} ${plural(n, 'запись', 'записи', 'записей')}`
}

function dayLabel(iso: string): string {
  const [, m, d] = iso.split('-')
  return `${Number(d)}.${m}`
}

// ── Bar chart: one measure, one scale ──
// Расчётная стоимость и записи — разные единицы, поэтому это два отдельных графика, а не
// одна картинка с двумя шкалами. Весь период помещается в ширину экрана;
// касание столбика показывает значение дня в заголовке.
function DayBars({ title, points, weekly, value, format }: {
  title: string
  points: AnalyticsDailyPoint[]
  weekly: boolean
  value: (p: AnalyticsDailyPoint) => number
  format: (n: number) => string
}) {
  const [active, setActive] = useState<number | null>(null)
  const values = points.map(value)
  const max = Math.max(0, ...values)
  const total = values.reduce((s, v) => s + v, 0)
  const n = points.length
  const ticks = n <= 1 ? [0] : [...new Set([0, Math.round((n - 1) / 3), Math.round((2 * (n - 1)) / 3), n - 1])]
  const periodName = (i: number) => (weekly ? `Неделя с ${dayLabel(points[i].date)}` : dayLabel(points[i].date))

  return (
    <div className="ds-card" style={{ padding: 14 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10, marginBottom: 12 }}>
        <h2 className="ds-h3" style={{ margin: 0 }}>{title}</h2>
        <span className="ds-sub" style={{ whiteSpace: 'nowrap' }} aria-live="polite">
          {active != null ? `${periodName(active)}: ${format(values[active])}` : `Всего ${format(total)}`}
        </span>
      </div>
      {max === 0 ? (
        <div className="ds-sub" style={{ textAlign: 'center', padding: '28px 0' }}>Нет данных за выбранный период</div>
      ) : (
        <>
          <div style={{ position: 'relative', height: 120, borderTop: '1px dashed var(--s-surface-3)', borderBottom: '1px solid var(--s-surface-3)' }}>
            <span className="ds-caption" style={{ position: 'absolute', top: 2, left: 0, lineHeight: 1 }}>{format(max)}</span>
            <div style={{ position: 'absolute', inset: '16px 0 0', display: 'flex', alignItems: 'flex-end', gap: n > 40 ? 1 : 2 }}>
              {points.map((p, i) => {
                const v = values[i]
                return (
                  <button
                    key={p.date}
                    type="button"
                    aria-label={`${periodName(i)}: ${format(v)}`}
                    onClick={() => setActive(active === i ? null : i)}
                    onPointerEnter={(e) => { if (e.pointerType === 'mouse') setActive(i) }}
                    onPointerLeave={(e) => { if (e.pointerType === 'mouse') setActive(null) }}
                    style={{ flex: 1, minWidth: 0, height: '100%', display: 'flex', alignItems: 'flex-end', padding: 0, border: 0, background: 'none', cursor: 'pointer' }}
                  >
                    <span style={{
                      display: 'block', width: '100%',
                      height: v > 0 ? `max(3px, ${(v / max) * 100}%)` : 0,
                      background: 'var(--s-accent)',
                      opacity: active == null || active === i ? 1 : 0.35,
                      borderRadius: '4px 4px 0 0',
                    }} />
                  </button>
                )
              })}
            </div>
          </div>
          <div style={{ position: 'relative', height: 14, marginTop: 5 }}>
            {ticks.map((i) => (
              <span key={i} className="ds-caption" style={{
                position: 'absolute', top: 0, lineHeight: 1, whiteSpace: 'nowrap',
                left: `${((i + 0.5) / n) * 100}%`,
                transform: i === 0 ? 'translateX(-4px)' : i === n - 1 ? 'translateX(calc(-100% + 4px))' : 'translateX(-50%)',
              }}>{dayLabel(points[i].date)}</span>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function DailyCharts({ daily }: { daily: AnalyticsDailyPoint[] }) {
  const weekly = daily.length > 60
  const points = useMemo(() => (weekly ? aggregateByWeek(daily) : daily), [daily, weekly])
  if (points.length === 0) {
    return <div className="ds-card ds-sub" style={{ padding: 14, textAlign: 'center' }}>Нет данных за выбранный период</div>
  }
  const unit = weekly ? 'неделям' : 'дням'
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <DayBars title={`Стоимость записей по ${unit}`} points={points} weekly={weekly} value={(p) => p.revenue} format={formatRub} />
      <DayBars title={`Записи по ${unit}`} points={points} weekly={weekly} value={(p) => p.bookings} format={formatBookings} />
    </div>
  )
}

// ── Top list ──
interface TopItem { id: number; name: string; count: number; revenue: number; share_percent: number }

function TopCard({ title, icon, items }: { title: string; icon: React.ReactNode; items: TopItem[] }) {
  return (
    <div className="ds-card" style={{ padding: 14 }}>
      <h3 className="ds-h3" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>{icon}{title}</h3>
      {items.length === 0 ? (
        <p className="ds-sub" style={{ textAlign: 'center', padding: '16px 0' }}>Нет данных за период</p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {items.map((item) => (
            <div key={item.id}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--s-text)', maxWidth: '55%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.name}</span>
                <span className="ds-caption" style={{ whiteSpace: 'nowrap' }}>{item.count} зап. · {formatRub(item.revenue)} ({item.share_percent.toFixed(0)}%)</span>
              </div>
              <div style={{ height: 4, background: 'var(--s-surface-3)', borderRadius: 999, overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${item.share_percent}%`, background: 'var(--s-accent)', borderRadius: 999 }} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Main ──
export function AnalyticsPage() {
  const { businesses, selectedId: businessId, selectBusiness } = useAdminBusiness()
  const [data, setData] = useState<AnalyticsOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [period, setPeriod] = useState<'week' | 'month' | 'year'>('week')

  useEffect(() => {
    if (!businessId) return
    setLoading(true)
    api.getAnalyticsOverview(businessId, PERIOD_DAYS[period]).then(setData).catch(() => setData(null)).finally(() => setLoading(false))
  }, [businessId, period])

  const periods = [
    { value: 'week' as const, label: 'Неделя' },
    { value: 'month' as const, label: 'Месяц' },
    { value: 'year' as const, label: 'Год' },
  ]
  const kpis = data?.kpis

  return (
    <AdminShell
      title="Аналитика"
      headerExtra={<BusinessSelector businesses={businesses} selectedId={businessId} onSelect={selectBusiness} />}
    >
      <div className="ds-context" style={{ paddingBottom: 8 }}>
        <div className="ds-context-eyebrow">Аналитика · последние {PERIOD_DAYS[period]} {plural(PERIOD_DAYS[period], 'день', 'дня', 'дней')}</div>
        <div className="ds-context-help">Подтверждённые и завершённые записи. Суммы по ценам услуг — оценка, не полученные платежи. Загрузка ориентировочная, без индивидуальных графиков дежурств. Стрелка — сравнение с предыдущим периодом.</div>
      </div>

      {loading ? (
        <div>
          <div className="ds-stats-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            {[1, 2, 3, 4].map((i) => <div key={i} className="skeleton" style={{ height: 92, borderRadius: 14 }} />)}
          </div>
          <div className="skeleton" style={{ height: 210, borderRadius: 14, marginTop: 14 }} />
        </div>
      ) : (
        <>
          {/* KPI */}
          <div className="ds-stats-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <KpiCard label="Стоимость записей" value={kpis ? formatRub(kpis.revenue) : '—'} icon={<Wallet size={16} />} color="var(--s-accent)" cur={kpis?.revenue ?? 0} prev={kpis?.revenue_prev ?? 0} />
            <KpiCard label="Записей" value={kpis ? String(kpis.bookings) : '—'} icon={<Calendar size={16} />} color="var(--s-success)" cur={kpis?.bookings ?? 0} prev={kpis?.bookings_prev ?? 0} />
            <KpiCard label="Средняя стоимость" value={kpis ? formatRub(kpis.avg_check) : '—'} icon={<Receipt size={16} />} color="var(--s-warning)" cur={kpis?.avg_check ?? 0} prev={kpis?.avg_check_prev ?? 0} />
            <KpiCard label="Загрузка (оценка)" value={kpis && kpis.load_percent != null ? `${kpis.load_percent.toFixed(1)}%` : '—'} icon={<Activity size={16} />} color="var(--s-danger)" cur={kpis?.load_percent ?? 0} prev={kpis?.load_percent_prev ?? 0} isPoints />
          </div>

          {/* Графики */}
          <div style={{ marginTop: 14 }}>
            <DailyCharts daily={data?.daily ?? []} />
          </div>

          {/* Топы */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 12, marginBottom: 12 }}>
            <TopCard title="Топ услуг" icon={<Scissors size={14} style={{ color: 'var(--s-accent)' }} />} items={data?.top_services ?? []} />
            <TopCard title="Топ сотрудников" icon={<User size={14} style={{ color: 'var(--s-accent)' }} />} items={data?.top_employees ?? []} />
          </div>
          {businessId ? <SeoAttributionCard businessId={businessId} days={PERIOD_DAYS[period]} /> : null}
        </>
      )}

      {/* Период — внизу, у большого пальца */}
      <div className="ds-bottom-controls" style={{ marginTop: 'auto' }}>
        <div className="ds-seg ds-seg--full" role="group" aria-label="Период">
          {periods.map((p) => (
            <button key={p.value} className={period === p.value ? 'on' : ''} aria-pressed={period === p.value} onClick={() => setPeriod(p.value)}>{p.label}</button>
          ))}
        </div>
      </div>
    </AdminShell>
  )
}
