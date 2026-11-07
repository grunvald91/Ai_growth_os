import { useEffect, useState } from 'react'
import { api } from '../api/client'

// The canonical ServGo sign-in also grants access to Studio via existing SSO.
export function VkLoginButton() {
  const [available, setAvailable] = useState(false)
  useEffect(() => {
    api.vkCapabilities().then(result => setAvailable(result.available)).catch(() => setAvailable(false))
  }, [])
  if (!available) return null
  return (
    <a href="/api/auth/vk/start" className="ds-btn ds-btn--primary"
      style={{ display: 'flex', width: '100%', alignItems: 'center', justifyContent: 'center',
        minHeight: 48, gap: 10, background: '#0077ff', color: '#fff', textDecoration: 'none' }}>
      <span aria-hidden="true" style={{ display: 'grid', placeItems: 'center', width: 26, height: 26,
        borderRadius: 8, background: 'rgba(255,255,255,.18)', fontWeight: 800 }}>VK</span>
      Войти через VK ID
    </a>
  )
}
