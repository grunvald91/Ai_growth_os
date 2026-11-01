import { useState, useEffect, useRef } from 'react';
import { api } from '../api/client';
import type { Business } from '../api/client';

// Cache businesses in memory so subsequent hook calls are instant
let cachedBusinesses: Business[] | null = null;
let fetchPromise: Promise<Business[]> | null = null;

// A logout must not reuse the previous account's in-memory business list.
const resetAccountCache = () => { cachedBusinesses = null; fetchPromise = null }
window.addEventListener('servgo-logout', resetAccountCache)
window.addEventListener('servgo-account-changed', resetAccountCache)
window.addEventListener('storage', event => { if (event.key === 'token') resetAccountCache() })

/** После создания бизнеса: выбрать его и сбросить кэш списка, чтобы он появился везде. */
export function rememberCreatedBusiness(id: number) {
  cachedBusinesses = null;
  fetchPromise = null;
  localStorage.setItem('adminBusinessId', String(id));
}

export function useAdminBusiness() {
  // Use cached businessId from localStorage immediately — no waiting for API
  const savedId = useRef((() => {
    const s = localStorage.getItem('adminBusinessId');
    return s ? parseInt(s) : null;
  })());

  const [businesses, setBusinesses] = useState<Business[]>(cachedBusinesses || []);
  const [selectedId, setSelectedId] = useState<number | null>(savedId.current);
  const [loading, setLoading] = useState(!cachedBusinesses);

  useEffect(() => {
    if (cachedBusinesses) {
      setBusinesses(cachedBusinesses);
      // Validate savedId against cache
      if (savedId.current && cachedBusinesses.some(b => b.id === savedId.current)) {
        setSelectedId(savedId.current);
      } else if (cachedBusinesses.length > 0) {
        setSelectedId(cachedBusinesses[0].id);
      }
      setLoading(false);
      return;
    }

    // Deduplicate concurrent fetches
    if (!fetchPromise) {
      fetchPromise = api.getAdminBusinesses();
    }

    fetchPromise
      .then((list) => {
        cachedBusinesses = list;
        setBusinesses(list);
        if (savedId.current && list.some((b) => b.id === savedId.current)) {
          setSelectedId(savedId.current);
        } else if (list.length > 0) {
          const id = list[0].id;
          setSelectedId(id);
          localStorage.setItem('adminBusinessId', String(id));
        }
      })
      .catch(() => {})
      .finally(() => {
        setLoading(false);
        fetchPromise = null;
      });
  }, []);

  const selectBusiness = (id: number) => {
    setSelectedId(id);
    localStorage.setItem('adminBusinessId', String(id));
  };

  return { businesses, selectedId, loading, selectBusiness };
}
