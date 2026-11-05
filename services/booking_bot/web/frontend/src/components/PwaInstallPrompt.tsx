import { useEffect, useState } from 'react'
import { Download, X } from 'lucide-react'
import { isStandalonePwa, type BeforeInstallPromptEvent } from '../utils/pwa'

export function PwaInstallPrompt() {
  const [installEvent, setInstallEvent] = useState<BeforeInstallPromptEvent | null>(null)
  const [dismissed, setDismissed] = useState(() => sessionStorage.getItem('servgo-install-dismissed') === '1')
  const [iosHint] = useState(() => /iphone|ipad|ipod/.test(navigator.userAgent.toLowerCase()))

  useEffect(() => {
    if (isStandalonePwa()) return

    const onBeforeInstall = (event: Event) => {
      event.preventDefault()
      setInstallEvent(event as BeforeInstallPromptEvent)
    }
    const onInstalled = () => setInstallEvent(null)

    window.addEventListener('beforeinstallprompt', onBeforeInstall)
    window.addEventListener('appinstalled', onInstalled)
    return () => {
      window.removeEventListener('beforeinstallprompt', onBeforeInstall)
      window.removeEventListener('appinstalled', onInstalled)
    }
  }, [])

  // На компьютере приложение не предлагаем: сайт открывается в браузере в полном виде.
  const isDesktop = window.matchMedia?.('(hover: hover) and (pointer: fine)').matches ?? false
  if (isDesktop || dismissed || isStandalonePwa() || (!installEvent && !iosHint)) return null

  const dismiss = () => {
    sessionStorage.setItem('servgo-install-dismissed', '1')
    setDismissed(true)
  }

  const install = async () => {
    if (!installEvent) return
    await installEvent.prompt()
    const choice = await installEvent.userChoice
    if (choice.outcome === 'accepted') setInstallEvent(null)
  }

  return (
    <div
      style={{
        position: 'fixed',
        left: 12,
        right: 12,
        bottom: 'calc(72px + env(safe-area-inset-bottom))',
        zIndex: 60,
        maxWidth: 520,
        margin: '0 auto',
        padding: 14,
        borderRadius: 16,
        background: 'var(--s-surface)',
        border: '1px solid var(--s-line)',
        boxShadow: '0 10px 30px rgba(0,0,0,.14)',
      }}
    >
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
        <Download size={20} className="ds-accent" style={{ marginTop: 2, flexShrink: 0 }} />
        <div style={{ flex: 1 }}>
          <p className="ds-text" style={{ fontWeight: 750 }}>Установить ServGo</p>
          <p className="ds-sub" style={{ marginTop: 3 }}>
            {installEvent
              ? 'Добавьте ServGo на телефон — он будет открываться как отдельное приложение.'
              : 'На iPhone нажмите «Поделиться» → «На экран Домой», чтобы установить ServGo и использовать Web Push.'}
          </p>
          {installEvent && (
            <button type="button" onClick={install} className="ds-btn ds-btn--primary ds-btn--sm" style={{ marginTop: 8 }}>
              Установить
            </button>
          )}
        </div>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Закрыть"
          style={{ border: 'none', background: 'transparent', cursor: 'pointer', padding: 2 }}
        >
          <X size={18} />
        </button>
      </div>
    </div>
  )
}
