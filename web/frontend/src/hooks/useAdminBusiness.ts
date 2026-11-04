import { useState, useEffect } from 'react';
import { api } from '../api/client';
import type { Business } from '../api/client';

// A business list is scoped to the exact login token, not merely to a tab.
// Each invalidation bumps the epoch so an in-flight request from the previous
// account cannot repopulate the cache (even if it resolves after account B).
let cachedBusinesses: Business[] | null = null;
let cachedToken: string | null = null;
let fetchPromise: Promise<Business[]> | null = null;
let fetchToken: string | null = null;
let cacheEpoch = 0;

const resetAccountCache = () => {
  cacheEpoch += 1;
  cachedBusinesses = null;
  cachedToken = null;
  fetchPromise = null;
  fetchToken = null;
};
window.addEventListener('servgo-logout', resetAccountCache);
window.addEventListener('servgo-account-changed', resetAccountCache);
window.addEventListener('storage', event => { if (event.key === 'token') resetAccountCache(); });

/** After creation, invalidate old lists and select the newly created business. */
export function rememberCreatedBusiness(id: number) {
  resetAccountCache();
  localStorage.setItem('adminBusinessId', String(id));
  window.dispatchEvent(new Event('servgo-businesses-refresh'));
}

function savedBusinessId(): number | null {
  const raw = localStorage.getItem('adminBusinessId');
  if (!raw) return null;
  const value = Number(raw);
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

export function useAdminBusiness() {
  const [accountVersion, setAccountVersion] = useState(0);
  const currentToken = localStorage.getItem('token');
  const [businesses, setBusinesses] = useState<Business[]>(
    cachedToken === currentToken && cachedBusinesses ? cachedBusinesses : [],
  );
  const [selectedId, setSelectedId] = useState<number | null>(() => (
    cachedToken === currentToken && cachedBusinesses?.some(b => b.id === savedBusinessId())
      ? savedBusinessId() : null
  ));
  const [loading, setLoading] = useState(Boolean(currentToken && !cachedBusinesses));

  // Invalidate mounted consumers as well as the module-level cache. Without
  // this, an admin screen mounted during an account switch can keep A's list.
  useEffect(() => {
    const changed = () => {
      setBusinesses([]);
      setSelectedId(null);
      setLoading(Boolean(localStorage.getItem('token')));
      setAccountVersion(v => v + 1);
    };
    window.addEventListener('servgo-logout', changed);
    window.addEventListener('servgo-account-changed', changed);
    window.addEventListener('servgo-businesses-refresh', changed);
    const stored = (event: StorageEvent) => { if (event.key === 'token') changed(); };
    window.addEventListener('storage', stored);
    return () => {
      window.removeEventListener('servgo-logout', changed);
      window.removeEventListener('servgo-account-changed', changed);
      window.removeEventListener('servgo-businesses-refresh', changed);
      window.removeEventListener('storage', stored);
    };
  }, []);

  useEffect(() => {
    let active = true;
    const token = localStorage.getItem('token');
    const epoch = cacheEpoch;
    const stillCurrent = () => active && epoch === cacheEpoch && token === localStorage.getItem('token');
    const applyList = (list: Business[]) => {
      if (!stillCurrent()) return;
      setBusinesses(list);
      const preferred = savedBusinessId();
      const selected = preferred && list.some(b => b.id === preferred) ? preferred : list[0]?.id ?? null;
      setSelectedId(selected);
      if (selected == null) localStorage.removeItem('adminBusinessId');
      else localStorage.setItem('adminBusinessId', String(selected));
    };
    if (!token) {
      setBusinesses([]);
      setSelectedId(null);
      setLoading(false);
      return () => { active = false; };
    }
    if (cachedBusinesses && cachedToken === token) {
      applyList(cachedBusinesses);
      setLoading(false);
      return () => { active = false; };
    }
    setBusinesses([]);
    setSelectedId(null);
    setLoading(true);
    if (!fetchPromise || fetchToken !== token) {
      fetchToken = token;
      fetchPromise = api.getAdminBusinesses();
    }
    const request = fetchPromise;
    request.then(list => {
      if (!stillCurrent()) return;
      cachedBusinesses = list;
      cachedToken = token;
      applyList(list);
    }).catch(() => {
      if (stillCurrent()) { setBusinesses([]); setSelectedId(null); }
    }).finally(() => {
      if (!stillCurrent()) return;
      if (fetchPromise === request) { fetchPromise = null; fetchToken = null; }
      setLoading(false);
    });
    return () => { active = false; };
  }, [accountVersion]);

  const selectBusiness = (id: number) => {
    // A stale selection (e.g. from the former user's UI) must never persist.
    if (!businesses.some(b => b.id === id)) return;
    setSelectedId(id);
    localStorage.setItem('adminBusinessId', String(id));
  };
  return { businesses, selectedId, loading, selectBusiness };
}
