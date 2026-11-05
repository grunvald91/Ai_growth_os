// Мета-теги для поисковиков (Яндекс рендерит JS и читает их после отрисовки).
// Статичные значения по умолчанию лежат в index.html; здесь — подмена под маршрут.

export const SITE_URL = 'https://servgo.ru'

export const DEFAULT_TITLE = 'ServGo — ИИ-платформа роста для бизнеса услуг и онлайн-запись'
export const DEFAULT_DESCRIPTION =
  'ServGo — ИИ-платформа роста для бизнеса услуг: онлайн-запись, пустые окна, смены и зарплата сотрудников, контент, ответы клиентам и разведка конкурентов.'

function setMeta(attr: 'name' | 'property', key: string, content: string) {
  let el = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${key}"]`)
  if (!el) {
    el = document.createElement('meta')
    el.setAttribute(attr, key)
    document.head.appendChild(el)
  }
  el.setAttribute('content', content)
}

function setCanonical(href: string | null) {
  let el = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]')
  if (!href) {
    el?.remove()
    return
  }
  if (!el) {
    el = document.createElement('link')
    el.rel = 'canonical'
    document.head.appendChild(el)
  }
  el.href = href
}

export interface SeoParams {
  title?: string
  description?: string
  path: string
  noindex?: boolean
}

export function applySeo({ title = DEFAULT_TITLE, description = DEFAULT_DESCRIPTION, path, noindex = false }: SeoParams) {
  const url = `${SITE_URL}${path === '/' ? '/' : path.replace(/\/$/, '')}`
  document.title = title
  setMeta('name', 'description', description)
  setMeta('name', 'robots', noindex ? 'noindex, nofollow' : 'index, follow')
  setMeta('property', 'og:title', title)
  setMeta('property', 'og:description', description)
  setMeta('property', 'og:url', url)
  setCanonical(noindex ? null : url)
}

export function shortText(text: string, max = 160): string {
  const clean = text.replace(/\s+/g, ' ').trim()
  if (clean.length <= max) return clean
  const cut = clean.slice(0, max - 1)
  const lastSpace = cut.lastIndexOf(' ')
  return `${(lastSpace > 80 ? cut.slice(0, lastSpace) : cut).replace(/[,.;:—-]+$/, '')}…`
}
