// Telegram WebApp types
declare global {
  interface Window {
    Telegram?: {
      WebApp: TelegramWebApp
    }
  }
}

interface TelegramWebApp {
  ready: () => void
  expand: () => void
  close: () => void
  initData: string
  initDataUnsafe: {
    user?: {
      id: number
      first_name: string
      last_name?: string
      username?: string
    }
  }
  themeParams: Record<string, string>
  HapticFeedback: {
    selectionChanged: () => void
    notificationOccurred: (type: 'success' | 'error' | 'warning') => void
    impactOccurred: (style: 'light' | 'medium' | 'heavy') => void
  }
  showConfirm: (message: string, callback: (confirmed: boolean) => void) => void
  showPopup: (
    params: {
      title?: string
      message: string
      buttons?: Array<{ type: string; text: string }>
    },
    callback?: (buttonId: string) => void
  ) => void
  MainButton: {
    text: string
    show: () => void
    hide: () => void
    onClick: (fn: () => void) => void
    offClick: (fn: () => void) => void
    enable: () => void
    disable: () => void
    showProgress: (leaveActive: boolean) => void
    hideProgress: () => void
    isVisible: boolean
    isActive: boolean
    isProgressVisible: boolean
    color: string
    textColor: string
  }
  BackButton: {
    show: () => void
    hide: () => void
    onClick: (fn: () => void) => void
    offClick: (fn: () => void) => void
    isVisible: boolean
  }
  // Отступы от собственного UI Telegram (шапка с меню, safe area устройства).
  // Доступны с Bot API 8.0+ — на старых клиентах будут undefined.
  safeAreaInset?: { top: number; bottom: number; left: number; right: number }
  contentSafeAreaInset?: { top: number; bottom: number; left: number; right: number }
  onEvent: (eventType: string, callback: () => void) => void
  offEvent: (eventType: string, callback: () => void) => void
}

// Helper functions
export const tg = window.Telegram?.WebApp

export const isTelegramWebApp = !!tg

export function initTelegramWebApp() {
  if (!tg) return
  tg.ready()
  tg.expand()

  // Add class to body so CSS can target Telegram WebApp context
  document.documentElement.classList.add('tg-webapp')

  // Реальная высота шапки Telegram — берём у самого Telegram вместо зашитой
  // константы. На проверенном реальном устройстве safeAreaInset/contentSafeAreaInset
  // = 0 (WebView уже не перекрывается родной шапкой сам по себе, живой калибратор
  // подтвердил, что нужный отступ — 0px) — поэтому 0 здесь абсолютно легитимное
  // значение и его нельзя пропускать как "нет данных". CSS-фолбэк тоже 0px:
  // если API когда-нибудь вернёт undefined (совсем старый клиент), лишний отступ
  // добавлять не нужно — именно он и был источником прошлых багов.
  const applySafeArea = () => {
    const inset = tg.contentSafeAreaInset ?? tg.safeAreaInset
    if (inset) {
      document.documentElement.style.setProperty('--tg-safe-top', `${inset.top}px`)
    }
  }
  applySafeArea()
  tg.onEvent?.('safeAreaChanged', applySafeArea)
  tg.onEvent?.('contentSafeAreaChanged', applySafeArea)

  // Apply theme colors as CSS variables
  const params = tg.themeParams
  if (params) {
    const root = document.documentElement.style
    if (params.bg_color) root.setProperty('--tg-bg-color', params.bg_color)
    if (params.text_color) root.setProperty('--tg-text-color', params.text_color)
    if (params.button_color) root.setProperty('--tg-button-color', params.button_color)
    if (params.button_text_color)
      root.setProperty('--tg-button-text-color', params.button_text_color)
    if (params.secondary_bg_color)
      root.setProperty('--tg-secondary-bg-color', params.secondary_bg_color)
  }
}

export function hapticSelection() {
  tg?.HapticFeedback?.selectionChanged()
}

export function hapticSuccess() {
  tg?.HapticFeedback?.notificationOccurred('success')
}

export function hapticError() {
  tg?.HapticFeedback?.notificationOccurred('error')
}

export function hapticImpact(style: 'light' | 'medium' | 'heavy' = 'medium') {
  tg?.HapticFeedback?.impactOccurred(style)
}

export function getTelegramUser() {
  return tg?.initDataUnsafe?.user || null
}

export function getTelegramInitData() {
  return tg?.initData || ''
}

// Confirmation dialog — uses Telegram native if available, browser fallback
export function confirmAction(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    if (tg) {
      tg.showConfirm(message, resolve)
    } else {
      resolve(window.confirm(message))
    }
  })
}

// Show main button
export function showMainButton(text: string, onClick: () => void) {
  if (!tg) return
  tg.MainButton.text = text
  tg.MainButton.onClick(onClick)
  tg.MainButton.show()
}

// Hide main button
export function hideMainButton() {
  if (!tg) return
  tg.MainButton.hide()
}

// Back button
export function showBackButton(onClick: () => void) {
  if (!tg) return
  tg.BackButton.onClick(onClick)
  tg.BackButton.show()
}

export function hideBackButton() {
  if (!tg) return
  tg.BackButton.hide()
}
