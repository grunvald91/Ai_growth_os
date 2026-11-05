import { useEffect, useRef, useState } from 'react';
import { ChevronDown, Check } from 'lucide-react';
import type { Business } from '../api/client';

interface Props {
  businesses: Business[];
  selectedId: number | null;
  onSelect: (id: number) => void;
}

/**
 * Компактный dropdown выбора бизнеса для шапки (headerExtra в AdminShell).
 * Триггер — узкая пилюля с текущим названием (обрезается многоточием),
 * список остальных бизнесов открывается ниже поверх контента.
 */
export function BusinessSelector({ businesses, selectedId, onSelect }: Props) {
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<{ top: number; right: number; width: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  // Меню позиционируется от экрана, а не от кнопки: на узком телефоне
  // оно не выходит за левый край.
  const toggle = () => {
    if (!open && ref.current) {
      const r = ref.current.getBoundingClientRect();
      const width = Math.min(300, window.innerWidth - 24);
      const right = Math.min(Math.max(12, window.innerWidth - r.right), window.innerWidth - width - 12);
      setMenuPos({ top: r.bottom + 6, right, width });
    }
    setOpen((v) => !v);
  };

  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, [open]);

  if (businesses.length <= 1) return null;

  const current = businesses.find((b) => b.id === selectedId);

  return (
    <div ref={ref} style={{ position: 'relative', minWidth: 0, maxWidth: '100%' }}>
      <button
        onClick={toggle}
        className="ds-biz-select"
        style={{ maxWidth: '100%', overflow: 'hidden' }}
        aria-label="Выбрать бизнес"
        aria-expanded={open}
      >
        <span>{current?.name ?? 'Бизнес'}</span>
        <ChevronDown
          size={13}
          style={{ flexShrink: 0, transition: 'transform 0.15s ease', transform: open ? 'rotate(180deg)' : 'none' }}
        />
      </button>

      {open && menuPos && (
        <div
          style={{
            background: 'var(--s-surface)',
            borderRadius: 15,
            position: 'fixed',
            top: menuPos.top,
            right: menuPos.right,
            zIndex: 95,
            width: menuPos.width,
            padding: 6,
            boxShadow: 'var(--sh-pop)',
            maxHeight: '60vh',
            overflowY: 'auto',
          }}
        >
          {businesses.map((b) => {
            const active = b.id === selectedId;
            return (
              <button
                key={b.id}
                onClick={() => {
                  onSelect(b.id);
                  setOpen(false);
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 8,
                  width: '100%',
                  textAlign: 'left',
                  padding: '11px 10px',
                  borderRadius: 11,
                  border: 'none',
                  background: active ? 'var(--s-accent-soft)' : 'transparent',
                  color: active ? 'var(--s-accent)' : 'var(--s-text)',
                  fontSize: 13,
                  fontWeight: active ? 500 : 400,
                  fontFamily: 'inherit',
                  cursor: 'pointer',
                }}
              >
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.name}</span>
                {active && <Check size={15} style={{ flexShrink: 0 }} />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
