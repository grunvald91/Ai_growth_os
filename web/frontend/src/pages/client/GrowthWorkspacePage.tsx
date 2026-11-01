import { Link, useSearchParams } from 'react-router-dom'
import { AdminShell } from '../../components/AdminShell'
import { StudioEntry } from '../../components/StudioEntry'
import { useAdminBusiness } from '../../hooks/useAdminBusiness'
import { useOpenStudio } from '../../hooks/useOpenStudio'

/** First-visit is a business-owner action, so the whole page uses the Admin
 * shell. A scoped HttpOnly cookie (never JavaScript) proves setup consent.
 * Growth checks the signed intent and live owner access on form submission.
 */
const ERROR_MESSAGES: Record<string, { title: string; body: string }> = {
  expired: {
    title: 'Подтверждение устарело',
    body: 'Откройте маркетинг из админки повторно, чтобы получить новую защищённую ссылку.',
  },
  denied: {
    title: 'Нет доступа к маркетингу',
    body: 'ServGo не подтвердил ваш доступ к этому бизнесу. Проверьте выбранную организацию и права.',
  },
  'owner-required': {
    title: 'Сначала откроет владелец',
    body: 'Попросите владельца или совладельца один раз открыть AI Growth OS из админки ServGo.',
  },
  origin: {
    title: 'Не удалось проверить запрос',
    body: 'Вернитесь в админку и повторите переход в маркетинг. Данные бизнеса не изменены.',
  },
  business: {
    title: 'Выбран другой бизнес',
    body: 'Перейдите из админки ещё раз, выбрав нужный бизнес.',
  },
  unavailable: {
    title: 'Маркетинг временно недоступен',
    body: 'Не удалось подключиться к Growth. Попробуйте ещё раз позже.',
  },
  conflict: {
    title: 'Компания уже подключается',
    body: 'Вернитесь в админку и откройте маркетинг повторно.',
  },
}

export function GrowthWorkspacePage() {
  const [searchParams] = useSearchParams()
  const setup = searchParams.get('setup') === '1'
  const state = searchParams.get('setup') === 'error'
    ? (searchParams.get('code') || 'unavailable')
    : searchParams.get('setup') || ''
  const issue = ERROR_MESSAGES[state] ?? (state && !setup ? ERROR_MESSAGES.unavailable : undefined)
  const idParam = searchParams.get('business_id') ?? ''
  const businessId = /^\d+$/.test(idParam) && Number.isSafeInteger(Number(idParam)) && Number(idParam) > 0
    ? Number(idParam) : null
  const { businesses, selectedId } = useAdminBusiness()
  const { open, busy, error } = useOpenStudio(selectedId)
  const businessName = businesses.find((b) => b.id === businessId)?.name

  return (
    <AdminShell title="AI Growth OS" subtitle="Маркетинг вашего бизнеса">
      <div className="ds-form-narrow" style={{ width: '100%', paddingTop: 16 }}>
        {issue ? (
          <section className="ds-card" style={{ padding: 18 }} aria-label="Статус маркетинга">
            <h2 className="ds-h2">{issue.title}</h2>
            <p className="ds-sub" style={{ marginTop: 10, lineHeight: 1.55 }}>{issue.body}</p>
            <button type="button" onClick={open} disabled={busy} className="ds-btn ds-btn--primary"
              style={{ marginTop: 18, width: '100%', minHeight: 46 }}>
              {busy ? 'Открываем…' : 'Попробовать снова'}
            </button>
            {error && <p role="alert" className="ds-sub" style={{ color: 'var(--s-danger)' }}>{error}</p>}
            <Link to="/admin" className="ds-btn ds-btn--ghost"
              style={{ display: 'flex', justifyContent: 'center', marginTop: 10 }}>В админку</Link>
          </section>
        ) : setup ? (
          <section className="ds-card" style={{ padding: 18 }} aria-label="Первое подключение маркетинга">
            <h2 className="ds-h2">Подготовить маркетинг для этого бизнеса?</h2>
            {businessName && <p className="ds-text" style={{ marginTop: 8 }}>{businessName}</p>}
            <p className="ds-sub" style={{ marginTop: 10, fontSize: 13.5, lineHeight: 1.55 }}>
              Создадим пространство AI Growth OS и перенесём основные сведения из ServGo.
              Затем откроем профиль для уточнения деталей. Ничего не публикуется автоматически.
            </p>
            {businessId !== null ? (
              <form method="post" action="/studio/sso/create" style={{ marginTop: 18 }}>
                <input type="hidden" name="business_id" value={businessId} />
                <button className="ds-btn ds-btn--primary" type="submit"
                  style={{ width: '100%', minHeight: 46 }}>Создать и открыть</button>
              </form>
            ) : (
              <p role="alert" className="ds-sub" style={{ marginTop: 16 }}>
                Не удалось определить бизнес. Вернитесь в админку и откройте маркетинг повторно.
              </p>
            )}
            <Link to="/admin" className="ds-btn ds-btn--ghost"
              style={{ display: 'flex', justifyContent: 'center', marginTop: 10 }}>Не сейчас</Link>
            <p className="ds-caption" style={{ marginTop: 12 }}>
              Подтверждение действует ограниченное время. Если оно устарело, повторите вход из админки.
            </p>
          </section>
        ) : (
          <section className="ds-card" style={{ padding: 16 }}>
            <h2 className="ds-h3">Открыть маркетинг для бизнеса</h2>
            <p className="ds-sub">Выберите бизнес. ServGo проверит ваши права и откроет его студию.</p>
            <StudioEntry showUnavailable />
            <Link to="/admin" className="ds-btn ds-btn--ghost"
              style={{ display: 'inline-flex', marginTop: 10 }}>К управлению записями</Link>
          </section>
        )}
      </div>
    </AdminShell>
  )
}
