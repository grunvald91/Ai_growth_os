import { Mail } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

/** Header button: opens the owner mail page, which checks access on the server. */
export function OwnerMailEntry() {
  const navigate = useNavigate()
  return (
    <button
      type="button"
      onClick={() => navigate('/workspace/mail')}
      className="ds-icon-btn bordered"
      style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 10px', width: 'auto' }}
      title="Почта ServGo"
      aria-label="Почта"
    >
      <Mail size={18} /> <span style={{ fontSize: 12, fontWeight: 700 }}>Почта</span>
    </button>
  )
}
