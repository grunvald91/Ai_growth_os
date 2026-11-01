/**
 * Offline snapshots are explicitly permitted by the ServGo owner:
 * seven days, read-only offline, erased on logout.
 *
 * Never put authenticated JSON or pages into the shared SW CacheStorage.
 * Each IDB snapshot is scoped to a digest of the *current* login token.
 */
export const OFFLINE_RETENTION_MS = 7 * 24 * 60 * 60 * 1000
const DB_NAME = 'servgo-offline-snapshots-v1'
const STORE = 'snapshots'

type RecordInDb = {
  id: string
  savedAt: number
  expiresAt: number
  data: unknown
}
export type OfflineSnapshot<T> = { data: T; savedAt: number }

export function canSaveOffline(path: string): boolean {
  const p = path.split('?')[0]
  return p === '/my/bookings'
    || p === '/master/me'
    || p === '/master/schedule'
    || p === '/admin/businesses'
    || p === '/admin/bookings'
    || /^\/admin\/businesses\/\d+\/(?:employees|services)$/.test(p)
}

/** Equivalent URLs must select the same snapshot, including sorted query params. */
function canonicalPath(path: string) {
  const url = new URL(path, window.location.origin)
  url.searchParams.sort()
  return url.pathname + url.search
}

async function accountKey(token: string) {
  const bytes = new TextEncoder().encode(token)
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('')
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1)
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) {
        req.result.createObjectStore(STORE, { keyPath: 'id' })
      }
    }
    req.onsuccess = () => {
      // Let logout in another tab delete the database without an IDB block.
      req.result.onversionchange = () => req.result.close()
      resolve(req.result)
    }
    req.onerror = () => reject(req.error)
    req.onblocked = () => reject(new Error('Offline database blocked'))
  })
}

function transact<T>(mode: IDBTransactionMode, task: (store: IDBObjectStore, finish: (v: T) => void) => void): Promise<T> {
  return openDb().then(db => new Promise<T>((resolve, reject) => {
    let outcome: T
    let resultReady = false
    const tx = db.transaction(STORE, mode)
    const store = tx.objectStore(STORE)
    const finish = (value: T) => { outcome = value; resultReady = true }
    tx.oncomplete = () => { db.close(); if (resultReady) resolve(outcome); else reject(new Error('Offline transaction unfinished')) }
    tx.onerror = () => { db.close(); reject(tx.error) }
    tx.onabort = () => { db.close(); reject(tx.error) }
    try { task(store, finish) } catch (e) { tx.abort(); reject(e) }
  }))
}

export async function cleanupExpiredOfflineSnapshots() {
  await transact<void>('readwrite', (store, finish) => {
    const now = Date.now()
    const cursor = store.openCursor()
    cursor.onsuccess = () => {
      const item = cursor.result
      if (!item) { finish(undefined); return }
      if (!item.value || item.value.expiresAt <= now) item.delete()
      item.continue()
    }
  })
}

async function snapshotId(path: string, token: string) {
  return (await accountKey(token)) + ':' + canonicalPath(path)
}

/**
 * A downloaded week covers individual days too. Persist all seven day keys,
 * including empty days, so switching day while offline is reliable.
 */
function splitWeekIntoDays<T>(path: string, data: T): Array<{ path: string; data: T }> {
  if (path.split('?')[0] !== '/admin/bookings' || !Array.isArray(data)) return []
  const params = new URL(path, window.location.origin).searchParams
  const businessId = params.get('business_id')
  const start = params.get('start_date')
  const end = params.get('end_date')
  if (!businessId || !/^\d+$/.test(businessId) || !start || !end
    || !/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end)) return []
  const from = Date.parse(start + 'T00:00:00Z')
  const to = Date.parse(end + 'T00:00:00Z')
  if (!Number.isFinite(from) || !Number.isFinite(to) || to < from || to - from > 6 * 86400000) return []
  const result: Array<{ path: string; data: T }> = []
  for (let day = from; day <= to; day += 86400000) {
    const date = new Date(day).toISOString().slice(0, 10)
    result.push({
      path: `/admin/bookings?business_id=${businessId}&date=${date}`,
      data: data.filter((entry: { date?: string }) => entry.date === date) as T,
    })
  }
  return result
}

export async function saveOfflineSnapshot<T>(path: string, data: T, token: string): Promise<void> {
  if (!canSaveOffline(path) || !token) return
  const account = await accountKey(token)
  const now = Date.now()
  const records: RecordInDb[] = [
    { id: account + ':' + canonicalPath(path), savedAt: now, expiresAt: now + OFFLINE_RETENTION_MS, data },
    ...splitWeekIntoDays(path, data).map(item => ({
      id: account + ':' + canonicalPath(item.path),
      savedAt: now, expiresAt: now + OFFLINE_RETENTION_MS, data: item.data,
    })),
  ]
  // A response from an earlier login must never repopulate cache after logout.
  if (localStorage.getItem('token') !== token) return
  await transact<void>('readwrite', (store, finish) => {
    // Also check after asynchronous database opening: a logout may have started
    // since the initial check, and must win over an in-flight response.
    if (localStorage.getItem('token') === token) {
      records.forEach(record => store.put(record))
    }
    finish(undefined)
  })
  await cleanupExpiredOfflineSnapshots()
}

export async function loadOfflineSnapshot<T>(path: string, token: string): Promise<OfflineSnapshot<T> | null> {
  if (!canSaveOffline(path) || !token) return null
  const id = await snapshotId(path, token)
  if (localStorage.getItem('token') !== token) return null
  const record = await transact<RecordInDb | undefined>('readonly', (store, finish) => {
    const req = store.get(id)
    req.onsuccess = () => finish(req.result as RecordInDb | undefined)
  })
  if (!record) return null
  if (record.expiresAt <= Date.now()) {
    await transact<void>('readwrite', (store, finish) => { store.delete(id); finish(undefined) })
    return null
  }
  if (localStorage.getItem('token') !== token) return null
  return { data: record.data as T, savedAt: record.savedAt }
}

/** Local snapshots contain client contact details; logout waits for this cleanup. */
export function clearOfflineSnapshots(): Promise<void> {
  if (typeof indexedDB === 'undefined') return Promise.resolve()
  return new Promise((resolve, reject) => {
    const req = indexedDB.deleteDatabase(DB_NAME)
    req.onsuccess = () => resolve()
    req.onerror = () => reject(req.error)
    req.onblocked = () => reject(new Error('Offline data cleanup blocked by another tab'))
  })
}

export function publishOfflineSnapshotUsed(savedAt: number, path: string) {
  window.dispatchEvent(new CustomEvent('servgo-offline-snapshot-used', { detail: { savedAt, path } }))
}
