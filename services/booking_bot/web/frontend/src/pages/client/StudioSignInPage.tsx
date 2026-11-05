import { Link } from 'react-router-dom'
import { ClientShell } from '../../components/ClientShell'
import { StudioEntry } from '../../components/StudioEntry'

/** Only ServGo users enter here. The backend determines which businesses
 * they may open and issues a one-use Growth SSO ticket for the chosen one.
 */
export function StudioSignInPage() {
  return (
    <ClientShell backTo="/admin" title="Студия ServGo">
      <div className="ds-form-narrow" style={{ paddingTop: 12 }}>
        <section className="ds-card" style={{ padding: 18 }}>
          <h1 className="ds-h2">Ваши компании</h1>
          <p className="ds-sub" style={{ marginTop: 8, marginBottom: 14 }}>
            Вход выполнен через ваш аккаунт ServGo. Выберите компанию для работы в студии.
          </p>
          <StudioEntry autoOpen showUnavailable />
          <p className="ds-caption">
            Если компания уже есть в Growth, привяжите её к этому аккаунту ServGo: существующие материалы сохранятся.
          </p>
          <a href="/studio/identity" className="ds-btn ds-btn--ghost"
            style={{ display: 'inline-flex', marginTop: 12, marginRight: 8 }}>Привязать аккаунт Growth</a>
          <Link to="/account" className="ds-btn ds-btn--ghost"
            style={{ display: 'inline-flex', marginTop: 12 }}>Мой аккаунт</Link>
        </section>
      </div>
    </ClientShell>
  )
}
