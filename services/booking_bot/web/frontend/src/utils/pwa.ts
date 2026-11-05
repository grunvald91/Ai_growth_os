import { api } from '../api/client'

export interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>
}

export function isStandalonePwa() {
  return window.matchMedia?.('(display-mode: standalone)').matches
    || (window.navigator as Navigator & { standalone?: boolean }).standalone === true
}

export function supportsWebPush() {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

export function urlBase64ToUint8Array(value: string) {
  const padding = '='.repeat((4 - (value.length % 4)) % 4)
  const base64 = (value + padding).replace(/-/g, '+').replace(/_/g, '/')
  const raw = window.atob(base64)
  return Uint8Array.from([...raw].map((char) => char.charCodeAt(0)))
}

export async function enableServGoPush(notifyToken?: string | null) {
  if (!supportsWebPush()) {
    throw new Error('Web Push не поддерживается этим браузером')
  }

  const permission = await Notification.requestPermission()
  if (permission !== 'granted') {
    throw new Error(permission === 'denied' ? 'Уведомления запрещены в настройках браузера' : 'Разрешение на уведомления не получено')
  }

  const registration = await navigator.serviceWorker.ready
  const { public_key } = await api.getPushPublicKey()

  let subscription = await registration.pushManager.getSubscription()
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(public_key),
    })
  }

  const json = subscription.toJSON()
  if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) {
    throw new Error('Браузер не вернул полную push-подписку')
  }

  await api.subscribePush({
    endpoint: json.endpoint,
    keys: {
      p256dh: json.keys.p256dh,
      auth: json.keys.auth,
    },
    notify_token: notifyToken || undefined,
  })

  return subscription
}
