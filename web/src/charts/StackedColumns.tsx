import { useState } from "react";

import { Legend, Tooltip, type Series } from "./parts";
import { useWidth } from "./useWidth";

export interface Column {
  id: string;
  label: string;
  title: string;
  /** Values per series key; a column without values (the suite did not run) is left empty. */
  values: Record<string, number> | null;
}

const HEIGHT = 200;
const PAD = { top: 12, right: 8, bottom: 26, left: 40 };
const MAX_BAR = 24;
const GAP = 2;

/** A round axis maximum and its ticks: steps of 1, 2 or 5 × 10^n, at most 5 intervals. */
export function axisTicks(value: number): number[] {
  const max = Math.max(1, value);
  const magnitude = 10 ** Math.floor(Math.log10(max));
  const step =
    [0.1, 0.2, 0.5, 1, 2, 5]
      .map((s) => s * magnitude)
      .find((s) => s >= 1 && Math.ceil(max / s) <= 5) ?? 10 * magnitude;
  const top = Math.ceil(max / step) * step;
  return Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
}

/** A column path with a 4px rounded top and a square base. */
function columnPath(x: number, y: number, w: number, h: number, rounded: boolean): string {
  const r = rounded ? Math.min(4, w / 2, h) : 0;
  return (
    `M${x},${y + h}V${y + r}` +
    (r ? `Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}` : `H${x + w}`) +
    `V${y + h}Z`
  );
}

/**
 * Stacked columns (e.g. passed / failed / skipped tests per execution): bars capped at 24px,
 * a 2px surface gap between segments, the top segment rounded, and a per-column tooltip.
 */
export function StackedColumns({
  columns,
  series,
  label,
  onSelect,
}: {
  columns: Column[];
  series: Series[];
  label: string;
  onSelect?: (id: string) => void;
}) {
  const [active, setActive] = useState<number | null>(null);
  const [ref, width] = useWidth<HTMLDivElement>();
  const innerW = width - PAD.left - PAD.right;
  const innerH = HEIGHT - PAD.top - PAD.bottom;
  const totals = columns.map((c) => series.reduce((sum, s) => sum + (c.values?.[s.key] ?? 0), 0));
  const ticks = axisTicks(Math.max(...totals, 0));
  const max = ticks.at(-1) ?? 1;
  const slot = innerW / Math.max(columns.length, 1);
  const barW = Math.min(MAX_BAR, slot * 0.6);
  const y = (v: number) => PAD.top + innerH - (v / max) * innerH;
  const labelEvery = Math.max(1, Math.ceil(columns.length / 8));
  const current = active === null ? undefined : columns[active];

  return (
    <div className="chart" ref={ref}>
      <Legend series={series} shape="box" />
      <div className="plot">
        <svg viewBox={`0 0 ${width} ${HEIGHT}`} role="img" aria-label={label}>
          {ticks.map((t) => (
            <g key={t}>
              <line
                className={t === 0 ? "baseline" : "grid-line"}
                x1={PAD.left}
                x2={width - PAD.right}
                y1={y(t)}
                y2={y(t)}
              />
              <text className="tick" x={PAD.left - 6} y={y(t) + 4} textAnchor="end">
                {t}
              </text>
            </g>
          ))}
          {columns.map((column, i) => {
            const cx = PAD.left + slot * i + slot / 2;
            const x0 = cx - barW / 2;
            const values = column.values;
            const visible = values ? series.filter((s) => (values[s.key] ?? 0) > 0) : [];
            let base = 0;
            return (
              <g key={column.id} className={active !== null && active !== i ? "mark-dim" : ""}>
                {values &&
                  visible.map((s, index) => {
                    const v = values[s.key] ?? 0;
                    const top = y(base + v);
                    const bottom = y(base);
                    base += v;
                    const h = Math.max(1, bottom - top - (index > 0 ? GAP : 0));
                    return (
                      <path
                        key={s.key}
                        d={columnPath(x0, top, barW, h, index === visible.length - 1)}
                        fill={s.color}
                      />
                    );
                  })}
                {(i % labelEvery === 0 || i === columns.length - 1) && (
                  <text className="tick" x={cx} y={HEIGHT - 6} textAnchor="middle">
                    {column.label}
                  </text>
                )}
                <rect
                  className="hit"
                  x={PAD.left + slot * i}
                  y={PAD.top}
                  width={slot}
                  height={innerH}
                  tabIndex={0}
                  role={onSelect ? "link" : undefined}
                  aria-label={`${column.title}: ${
                    values
                      ? series
                          .map((s) => `${values[s.key] ?? 0} ${s.label.toLowerCase()}`)
                          .join(", ")
                      : "no test results"
                  }`}
                  onPointerEnter={() => {
                    setActive(i);
                  }}
                  onPointerLeave={() => {
                    setActive(null);
                  }}
                  onFocus={() => {
                    setActive(i);
                  }}
                  onBlur={() => {
                    setActive(null);
                  }}
                  onClick={() => onSelect?.(column.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") onSelect?.(column.id);
                  }}
                />
              </g>
            );
          })}
        </svg>
        {current !== undefined && active !== null && (
          <Tooltip
            x={PAD.left + slot * active + slot / 2}
            width={width}
            top={PAD.top}
            title={current.title}
            rows={
              current.values
                ? series.map((s) => ({
                    key: s.key,
                    label: s.label,
                    color: s.color,
                    value: String(current.values?.[s.key] ?? 0),
                  }))
                : [{ key: "none", label: "no test results", color: "var(--neutral)", value: "—" }]
            }
          />
        )}
      </div>
    </div>
  );
}
