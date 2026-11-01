import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { createHash } from 'node:crypto'
import { appendFile, readFile, readdir } from 'node:fs/promises'
import { resolve } from 'node:path'

// Stable first-party font assets for the same-origin Growth HTML shell.
// Source: the Onest package already installed for the BookingBot PWA.
// They are network-only for Growth; no client-private response is cached.
const growthSharedFont: Plugin = {
  name: 'servgo-shared-onest',
  async generateBundle() {
    const folder = resolve(process.cwd(), 'node_modules/@fontsource-variable/onest/files')
    const candidates = await readdir(folder)
    for (const subset of ['latin', 'cyrillic']) {
      const file = candidates.find((name) =>
        name === `onest-${subset}-wght-normal.woff2`)
      if (!file) throw new Error(`Required Onest variable subset unavailable: ${subset}`)
      this.emitFile({
        type: 'asset',
        fileName: `fonts/servgo-onest-${subset}.woff2`,
        source: await readFile(resolve(folder, file)),
      })
    }
  },
}

// Include lazy route bundles, fonts and styles in the offline installation.
// Generated filenames are content-hashed, so SW v4 never guesses stale assets.
const offlineAssetManifest: Plugin = {
  name: 'servgo-offline-assets',
  generateBundle(_options, bundle) {
    const files = Object.keys(bundle)
      .filter((name) => name.startsWith('assets/'))
      .map((name) => '/' + name)
    this.emitFile({
      type: 'asset',
      fileName: 'offline-assets.json',
      source: JSON.stringify(files),
    })
  },
  async closeBundle() {
    // The SW source must change whenever the hashed offline bundle changes;
    // otherwise browsers may not even run the safe staged installer.
    const output = resolve(process.cwd(), 'dist')
    const manifest = await readFile(resolve(output, 'offline-assets.json'))
    const revision = createHash('sha256').update(manifest).digest('hex').slice(0, 16)
    await appendFile(resolve(output, 'sw.js'), `\n// servgo-offline-manifest-sha256: ${revision}\n`)
  },
}

export default defineConfig({
  plugins: [react(), tailwindcss(), growthSharedFont, offlineAssetManifest],
  server: {
    proxy: {
      '/api': 'http://localhost:8000'
    }
  }
})
