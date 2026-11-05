/**
 * ServGo wordmark + calendar/pen icon.
 * Matches the design system from claude.ai/design (Serv brand bundle).
 */
interface LogoProps {
  size?: number
  withText?: boolean
  light?: boolean
}

export function Logo({ size = 28, withText = true, light = false }: LogoProps) {
  const ink = light ? '#FFFFFF' : 'var(--s-text, #0D1B2A)'
  const goCol = light ? '#7FA0FF' : 'var(--s-accent, #2F5BEB)'
  const blue = '#2F5BEB'

  return (
    <div className="inline-flex items-center" style={{ gap: size * 0.3 }}>
      <svg width={size} height={size} viewBox="0 0 48 48" fill="none" aria-hidden>
        <rect x="5" y="8" width="34" height="33" rx="8" fill={ink} />
        <rect x="9.5" y="18" width="25" height="18.5" rx="4" fill="#FFFFFF" />
        <rect x="13" y="4" width="4.5" height="9" rx="2.25" fill={ink} />
        <rect x="26.5" y="4" width="4.5" height="9" rx="2.25" fill={ink} />
        <g fill={blue} opacity="0.5">
          <rect x="13" y="22" width="4" height="4" rx="1.2" />
          <rect x="20" y="22" width="4" height="4" rx="1.2" />
          <rect x="27" y="22" width="4" height="4" rx="1.2" />
          <rect x="13" y="29" width="4" height="4" rx="1.2" />
          <rect x="20" y="29" width="4" height="4" rx="1.2" />
        </g>
        <path
          d="M40.5 19.5 L44 23 L31.5 35.5 L27.5 36.5 L28.5 32.5 Z"
          fill={blue}
          stroke="#FFFFFF"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
      </svg>
      {withText && (
        <span
          style={{
            fontSize: size * 0.6,
            fontWeight: 800,
            letterSpacing: '-0.02em',
            color: ink,
            lineHeight: 1,
          }}
        >
          Serv<span style={{ color: goCol }}>Go</span>
        </span>
      )}
    </div>
  )
}
