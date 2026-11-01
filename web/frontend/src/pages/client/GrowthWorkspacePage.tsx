import { Link } from 'react-router-dom'
import { ClientShell } from '../../components/ClientShell'
import { StudioEntry } from '../../components/StudioEntry'

/** Same-origin PWA entry: request a server-authorized SSO ticket before Growth. */
export function GrowthWorkspacePage() {
  return (
    <ClientShell title="Маркетинг" backTo="/admin">
      <div className="ds-form-narrow" style={{ paddingTop: 16 }}>
        <div className="ds-card" style={{ padding: 16 }}>
          <h2 className="ds-h3">Открыть маркетинг для бизнеса</h2>
          <p>Выберите бизнес. ServGo проверит права и откроет его маркетинговую студию.</p>
          <StudioEntry showUnavailable />
          <Link to="/admin" className="bf-account-row">← К управлению записями</Link>
        </div>
      </div>
    </ClientShell>
  )
}
