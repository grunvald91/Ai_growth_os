/** Regression checks for the synchronous, route-specific canonical bootstrap.
 * Run: node --test tests/seo-bootstrap.test.mjs
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'

const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8')
const match = html.match(/<script id="seo-route-bootstrap">([\s\S]*?)<\/script>/)
assert.ok(match, 'route-aware SEO script must be embedded in the HTML head')
assert.ok(html.indexOf('id="seo-route-bootstrap"') < html.indexOf('/src/main.tsx'),
  'canonical must be set before React loads')

function bootstrap(pathname) {
  const elements = []
  const robots = {
    content: 'index, follow',
    setAttribute(name, value) {
      if (name === 'content') this.content = value
    },
  }
  const document = {
    head: {
      querySelector(selector) {
        return selector === 'meta[name="robots"]' ? robots : null
      },
      appendChild(element) { elements.push(element) },
    },
    createElement(tagName) {
      return {
        tagName,
        attributes: {},
        setAttribute(name, value) { this.attributes[name] = value },
      }
    },
  }
  vm.runInNewContext(match[1], { document, window: { location: { pathname } } },
    { timeout: 500 })
  const canonical = elements.find(el => el.rel === 'canonical')
  const ogUrl = elements.find(el => el.attributes?.property === 'og:url')
  return { robots: robots.content, canonical: canonical?.href, ogUrl: ogUrl?.attributes?.content }
}

for (const [path, expected] of [
  ['/', 'https://servgo.ru/'],
  ['/business/42', 'https://servgo.ru/business/42'],
  ['/business/42/', 'https://servgo.ru/business/42'],
  ['/business/00042', 'https://servgo.ru/business/42'],
  ['/business/42/blog', 'https://servgo.ru/business/42/blog'],
  ['/business/00042/blog/', 'https://servgo.ru/business/42/blog'],
  ['/business/42/blog/article-slug', 'https://servgo.ru/business/42/blog/article-slug'],
]) {
  test(`canonical for ${path}`, () => {
    const result = bootstrap(path)
    assert.equal(result.canonical, expected)
    assert.equal(result.ogUrl, expected)
    assert.equal(result.robots, 'index, follow')
  })
}

for (const path of [
  '/business/0', '/business/unknown', '/business/999999999999999999999999',
  '/business/42/blog/sitemap.xml', '/business/42/blog/INVALID', '/book/42', '/admin', '/studio',
]) {
  test(`no premature index of SPA fallback on ${path}`, () => {
    const result = bootstrap(path)
    assert.equal(result.canonical, undefined)
    assert.equal(result.robots, 'noindex, follow')
  })
}
