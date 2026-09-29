import type { AssetSummary } from "../api";
import { fmtNumber, healthStatus, idlerNumber, STATUS_LABEL, type Status } from "../format";
import { StatusIcon } from "./Status";

interface Props {
  code: string; // código de la correa (CV-201)
  components: AssetSummary[]; // subunidades y polines
  temps: Record<string, number>; // temperatura máxima por polín
  selected?: string;
  onSelect: (code: string) => void;
}

const W = 1000;
const H = 300;
const X0 = 100; // tambor de cola
const X1 = 820; // tambor motriz
const TOP_Y = 96;
const BOT_Y = 206;

/**
 * Vista esquemática del gemelo de la correa: tramo superior (polines 1-20) y tramo
 * inferior (21-40), tambores, motor y reductor, cada uno con el color de su estado.
 * El color siempre va acompañado del número y del ícono en el detalle.
 */
export function ConveyorSchematic({ code, components, temps, selected, onSelect }: Props) {
  const byCode = new Map(components.map((c) => [c.code, c]));
  const idlers = components
    .filter((c) => idlerNumber(c.code) !== null)
    .sort((a, b) => (idlerNumber(a.code) ?? 0) - (idlerNumber(b.code) ?? 0));
  const perRow = Math.ceil(idlers.length / 2) || 20;
  // Los polines van entre los tambores, sin pisarlos.
  const first = X0 + 72;
  const step = (X1 - 72 - first) / (perRow - 1 || 1);

  const statusOf = (c?: AssetSummary): Status => healthStatus(c?.health_index);
  const fill = (s: Status) => `var(--${s})`;
  const part = (suffix: string) => byCode.get(`${code}-${suffix}`);

  const belt = part("BELT");
  const beltStatus = statusOf(belt);

  return (
    <svg className="conveyor" viewBox={`0 0 ${W} ${H}`} role="group" aria-label={`Esquema de la correa ${code}`}>
      <defs>
        <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="6" stroke="var(--line-strong)" strokeWidth="2" />
        </pattern>
      </defs>

      {/* Banda: recorrido cerrado entre tambores */}
      <Clickable onSelect={onSelect} asset={belt} label="Banda">
        <path
          d={`M${X0},${TOP_Y - 14} L${X1},${TOP_Y - 14} A69,69 0 0 1 ${X1},${BOT_Y + 14} L${X0},${BOT_Y + 14} A69,69 0 0 1 ${X0},${TOP_Y - 14}`}
          fill="none"
          stroke={beltStatus === "good" || beltStatus === "unknown" ? "var(--line-strong)" : fill(beltStatus)}
          strokeWidth={selected === belt?.code ? 9 : 6}
        />
      </Clickable>
      <text x={X1 - 60} y={TOP_Y - 50} textAnchor="end" fontSize={12} fill="var(--text-muted)" fontFamily="var(--font-mono)">
        SENTIDO DE MARCHA →
      </text>

      {/* Tambores */}
      <circle cx={X0} cy={(TOP_Y + BOT_Y) / 2} r={48} fill="url(#hatch)" stroke="var(--line-strong)" strokeWidth={2} />
      <text x={X0} y={(TOP_Y + BOT_Y) / 2 + 4} textAnchor="middle" fontSize={11} fill="var(--text-secondary)">
        COLA
      </text>
      <Clickable onSelect={onSelect} asset={part("PUL")} label="Polea motriz">
        <circle
          cx={X1}
          cy={(TOP_Y + BOT_Y) / 2}
          r={48}
          fill="var(--surface-3)"
          stroke={fill(statusOf(part("PUL")))}
          strokeWidth={selected === part("PUL")?.code ? 5 : 3}
        />
        <text x={X1} y={(TOP_Y + BOT_Y) / 2 + 4} textAnchor="middle" fontSize={11} fill="var(--text-secondary)">
          MOTRIZ
        </text>
      </Clickable>

      {/* Reductor y motor */}
      {[
        { suffix: "GBX", x: 905, label: "RED." },
        { suffix: "MOT", x: 960, label: "MOTOR" },
      ].map(({ suffix, x, label }) => {
        const a = part(suffix);
        const s = statusOf(a);
        return (
          <Clickable key={suffix} onSelect={onSelect} asset={a} label={label}>
            <rect
              x={x - 24}
              y={(TOP_Y + BOT_Y) / 2 - 30}
              width={48}
              height={60}
              rx={4}
              fill="var(--surface-3)"
              stroke={s === "unknown" ? "var(--line-strong)" : fill(s)}
              strokeWidth={selected === a?.code ? 5 : 3}
            />
            <text x={x} y={(TOP_Y + BOT_Y) / 2 + 4} textAnchor="middle" fontSize={11} fill="var(--text-secondary)">
              {label}
            </text>
          </Clickable>
        );
      })}
      <line x1={878} x2={881} y1={(TOP_Y + BOT_Y) / 2} y2={(TOP_Y + BOT_Y) / 2} stroke="var(--line-strong)" strokeWidth={6} />

      {/* Polines */}
      {idlers.map((c, i) => {
        const row = i < perRow ? 0 : 1;
        const col = row === 0 ? i : i - perRow;
        const x = first + col * step;
        const y = row === 0 ? TOP_Y : BOT_Y;
        const s = statusOf(c);
        const n = idlerNumber(c.code);
        const t = temps[c.code];
        const isSel = selected === c.code;
        return (
          <Clickable key={c.code} onSelect={onSelect} asset={c} label={`Polín ${n}`}>
            <rect
              x={x - 12}
              y={y - 9}
              width={24}
              height={18}
              rx={4}
              fill={s === "unknown" ? "var(--surface-3)" : fill(s)}
              stroke={isSel ? "var(--text-primary)" : "var(--surface-1)"}
              strokeWidth={isSel ? 3 : 2}
            />
            <text
              x={x}
              y={row === 0 ? y + 28 : y - 18}
              textAnchor="middle"
              fontSize={11}
              fontFamily="var(--font-mono)"
              fill={s === "critical" || s === "serious" ? "var(--text-primary)" : "var(--text-muted)"}
              fontWeight={s === "critical" || s === "serious" ? 700 : 400}
            >
              {n}
            </text>
            {t !== undefined && (s === "critical" || s === "serious" || isSel) && (
              <text x={x} y={row === 0 ? y - 26 : y + 38} textAnchor="middle" fontSize={12} fontWeight={700} fill="var(--text-primary)" fontFamily="var(--font-mono)">
                {fmtNumber(t, 0)}°
              </text>
            )}
          </Clickable>
        );
      })}
      <text x={first - 12} y={TOP_Y - 50} fontSize={12} fill="var(--text-muted)" fontFamily="var(--font-mono)">
        TRAMO SUPERIOR · POLINES 1–{perRow}
      </text>
      <text x={first - 12} y={BOT_Y + 58} fontSize={12} fill="var(--text-muted)" fontFamily="var(--font-mono)">
        TRAMO INFERIOR · POLINES {perRow + 1}–{idlers.length}
      </text>
    </svg>
  );
}

function Clickable({
  asset,
  children,
  label,
  onSelect,
}: {
  asset?: AssetSummary;
  children: React.ReactNode;
  label: string;
  onSelect: (code: string) => void;
}) {
  if (!asset) return <g>{children}</g>;
  const status = healthStatus(asset.health_index);
  return (
    <g
      className="idler"
      role="button"
      tabIndex={0}
      aria-label={`${label}: ${STATUS_LABEL[status]}`}
      onClick={() => onSelect(asset.code)}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect(asset.code)}
    >
      <title>{`${asset.name} (${asset.code}) · salud ${asset.health_index === null ? "sin datos" : Math.round(asset.health_index)}`}</title>
      {children}
    </g>
  );
}

export function StatusLegend() {
  const items: Status[] = ["good", "warning", "serious", "critical", "unknown"];
  return (
    <div className="legend" aria-label="Referencias de estado">
      {items.map((s) => (
        <span key={s} className="status" style={{ fontWeight: 400 }}>
          <StatusIcon status={s} size={12} /> {STATUS_LABEL[s]}
          <span className="muted">{s === "good" ? "≥80" : s === "warning" ? "60–79" : s === "serious" ? "40–59" : s === "critical" ? "<40" : ""}</span>
        </span>
      ))}
    </div>
  );
}
