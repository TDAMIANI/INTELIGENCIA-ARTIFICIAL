import { healthStatus, STATUS_LABEL, type Status } from "../format";

/** Ícono de estado: la forma distingue el estado sin depender del color. */
export function StatusIcon({ status, size = 14 }: { status: Status; size?: number }) {
  const color = `var(--${status})`;
  const common = { width: size, height: size, viewBox: "0 0 16 16", "aria-hidden": true } as const;
  switch (status) {
    case "good":
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="7" fill={color} />
          <path d="M4.5 8.2l2.3 2.3 4.7-4.8" stroke="#fff" strokeWidth="1.8" fill="none" strokeLinecap="round" />
        </svg>
      );
    case "warning":
      return (
        <svg {...common}>
          <path d="M8 1.2l7 13H1z" fill={color} />
          <path d="M8 6v3.6" stroke="#141410" strokeWidth="1.8" strokeLinecap="round" />
          <circle cx="8" cy="11.8" r="1" fill="#141410" />
        </svg>
      );
    case "serious":
      return (
        <svg {...common}>
          <path d="M8 1l7 7-7 7-7-7z" fill={color} />
          <path d="M8 4.8v4" stroke="#141410" strokeWidth="1.8" strokeLinecap="round" />
          <circle cx="8" cy="11" r="1" fill="#141410" />
        </svg>
      );
    case "critical":
      return (
        <svg {...common}>
          <path d="M5 1h6l4 4v6l-4 4H5l-4-4V5z" fill={color} />
          <path d="M5.5 5.5l5 5m0-5l-5 5" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      );
    default:
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="6.2" fill="none" stroke={color} strokeWidth="1.6" strokeDasharray="2.5 2" />
        </svg>
      );
  }
}

export function StatusTag({ status, label }: { status: Status; label?: string }) {
  return (
    <span className="status">
      <StatusIcon status={status} />
      {label ?? STATUS_LABEL[status]}
    </span>
  );
}

/** Índice de salud: número + barra + estado (nunca solo color). */
export function HealthBadge({ value, compact = false }: { value: number | null | undefined; compact?: boolean }) {
  const status = healthStatus(value);
  const pct = value === null || value === undefined ? 0 : Math.max(0, Math.min(100, value));
  return (
    <span className="hi-badge" title={`Índice de salud: ${value === null || value === undefined ? "sin datos" : value.toFixed(1)}`}>
      <StatusIcon status={status} />
      <span className="hi-num">{value === null || value === undefined ? "—" : Math.round(value)}</span>
      {!compact && (
        <span className="meter" style={{ width: 64 }}>
          <span style={{ width: `${pct}%`, background: `var(--${status})` }} />
        </span>
      )}
    </span>
  );
}
