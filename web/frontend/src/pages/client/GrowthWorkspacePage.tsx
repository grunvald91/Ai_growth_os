import { Link, useSearchParams } from 'react-router-dom'
import { ClientShell } from '../../components/ClientShell'
import { StudioEntry } from '../../components/StudioEntry'

/** Same-origin PWA: the Growth SSO gateway redirects here only when a business
 * has no marketing space yet. The signed setup intent stays in an HttpOnly
 * cookie scoped to /studio/sso/create, not in URL or JavaScript.
 */
export function GrowthWorkspacePage() {
  const [searchParams] = useSearchParams()
  const setup = searchParams.get('setup') === '1'
  const idParam = searchParams.get('business_id') ?? ''
  const businessId = /^\d+$/.test(idParam) && Number.isSafeInteger(Number(idParam)) && Number(idParam) > 0
    ? Number(idParam) : null

  return (
    <ClientShell title="Маркетинг" backTo="/admin">
      <div className="ds-form-narrow" style={{ paddingTop: 16 }}>
        {setup ? (
          <div className="ds-card" style={{ padding: 18 }}>
            <h2 className="ds-h2">Подготовить маркетинг для этого бизнеса?</h2>
            <p className="ds-sub" style={{ marginTop: 10, fontSize: 13.5, lineHeight: 1.55 }}>
              Создадим пространство AI Growth OS и перенесём основные сведения из ServGo.
              Затем откроем профиль для уточнения деталей. Ничего не публикуется автоматически.
            </p>
            {businessId !== null ? (
              <form method="post" action="/studio/sso/create" style={{ marginTop: 18 }}>
                <input type="hidden" name="business_id" value={businessId} />
                <button className="ds-btn ds-btn--primary" type="submit" style={{ width: '100%', minHeight: 46 }}>
                  Создать и открыть
                </button>
              </form>
            ) : (
              <p role="alert" className="ds-sub" style={{ marginTop: 16 }}>
                Не удалось определить бизнес. Вернитесь в админку и откройте маркетинг повторно.
              </p>
            )}
            <Link to="/admin" className="ds-btn ds-btn--ghost"
              style={{ display: 'flex', justifyContent: 'center', marginTop: 10 }}>
              Не сейчас
            </Link>
            <p className="ds-caption" style={{ marginTop: 12 }}>
              Подтверждение действует ограниченное время. Если оно устарело, повторите вход из админки.
            </p>
          </div>
        ) : (
          <div className="ds-card" style={{ padding: 16 }}>
            <h2 className="ds-h3">Открыть маркетинг для бизнеса</h2>
            <p>Выберите бизнес. ServGo проверит права и откроет его маркетинговую студию.</p>
            <StudioEntry showUnavailable />
            <Link to="/admin" className="bf-account-row">← К управлению записями</Link>
          </div>
        )}
      </div>
    </ClientShell>
  )
}
