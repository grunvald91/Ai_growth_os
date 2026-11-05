import { useEffect, useState } from 'react'

/** A failed transport can mean offline even when navigator.onLine reports true. */
export function useConnectivity() {
  const [online, setOnline] = useState(() => navigator.onLine)
  useEffect(() => {
    const connected = () => setOnline(true)
    const disconnected = () => setOnline(false)
    window.addEventListener('online', connected)
    window.addEventListener('offline', disconnected)
    window.addEventListener('servgo-api-online', connected)
    window.addEventListener('servgo-api-offline', disconnected)
    return () => {
      window.removeEventListener('online', connected)
      window.removeEventListener('offline', disconnected)
      window.removeEventListener('servgo-api-online', connected)
      window.removeEventListener('servgo-api-offline', disconnected)
    }
  }, [])
  return online
}

export function isOnlineNow() {
  return navigator.onLine
}
