import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
// Шрифты свои, а не с Google Fonts: у части российских провайдеров Google Fonts не открывается,
// и страница оставалась пустой до таймаута.
import '@fontsource-variable/onest/index.css'
import '@fontsource-variable/montserrat/index.css'
import './index.css'
import App from './App'
import { initTelegramWebApp } from './utils/telegram'
import { initializeServgoTheme } from './utils/servgoTheme'

initTelegramWebApp()
initializeServgoTheme()

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch((error) => {
      console.warn('ServGo service worker registration failed:', error)
    })
  })
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
