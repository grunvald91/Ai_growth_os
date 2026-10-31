import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { createHash } from 'node:crypto'
import { appendFile, readFile } from 'node:fs/promises'
import { resolve } from 'node:path'

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
  plugins: [react(), tailwindcss(), offlineAssetManifest],
  server: {
    proxy: {
      '/api': 'http://localhost:8000'
    }
  }
})
