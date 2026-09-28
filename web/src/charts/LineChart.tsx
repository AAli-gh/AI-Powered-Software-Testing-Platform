import { useState, type KeyboardEvent, type PointerEvent } from "react";

import { Legend, Tooltip, type Series } from "./parts";
import { useWidth } from "./useWidth";

export interface LinePoint {
  /** Short x-axis label (e.g. a date). */
  label: string;
  /** Tooltip title (e.g. the full timestamp). */
  title: string;
  values: Record<string, number | null>;
}

const HEIGHT = 200;
const PAD = { top: 12, right: 16, bottom: 26, left: 40 };

/**
 * Lines over a fixed 0..max scale (percentages): 2px lines, 8px end markers with a surface
 * ring, a crosshair that snaps to the nearest point and one tooltip listing every series.
 * A missing value breaks the line instead of being drawn as zero.
 */
export function LineChart({
  points,
  series,
  max = 100,
  format,
  tickFormat = format,
  label,
}: {
  points: LinePoint[];
  series: Series[];
  max?: number;
  /** Values in the tooltip. */
  format: (value: number) => string;
  /** Axis ticks (defaults to `format`). */
  tickFormat?: (value: number) => string;
  label: string;
}) {
  const [active, setActive] = useState<number | null>(null);
  const [ref, width] = useWidth<HTMLDivElement>();
  const innerW = width - PAD.left - PAD.right;
  const innerH = HEIGHT - PAD.top - PAD.bottom;
  const x = (i: number) =>
    PAD.left + (points.length === 1 ? innerW / 2 : (i * innerW) / (points.length - 1));
  const y = (v: number) => PAD.top + innerH - (Math.min(v, max) / max) * innerH;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);

  const nearest = (event: PointerEvent<SVGRectElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const svgX = ((event.clientX - box.left) / box.width) * innerW;
    const step = points.length > 1 ? innerW / (points.length - 1) : innerW;
    const index = Math.round(svgX / step);
    setActive(Math.max(0, Math.min(points.length - 1, index)));
  };

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.key === "ArrowRight" ? 1 : -1;
    setActive((current) =>
      Math.max(0, Math.min(points.length - 1, (current ?? points.length - 1) + step)),
    );
  };

  const activePoint = active === null ? undefined : points[active];
  const labelEvery = Math.max(1, Math.ceil(points.length / 8));

  return (
    <div className="chart" ref={ref}>
      {series.length > 1 && <Legend series={series} shape="line" />}
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
                {tickFormat(t)}
              </text>
            </g>
          ))}
          {points.map((p, i) =>
            i % labelEvery === 0 || i === points.length - 1 ? (
              <text key={i} className="tick" x={x(i)} y={HEIGHT - 6} textAnchor="middle">
                {p.label}
              </text>
            ) : null,
          )}
          {series.map((s) => {
            const segments: string[] = [];
            let current = "";
            points.forEach((p, i) => {
              const v = p.values[s.key];
              if (v === null || v === undefined) {
                if (current) segments.push(current);
                current = "";
                return;
              }
              current += `${current ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
            });
            if (current) segments.push(current);
            const last = [...points.entries()]
              .reverse()
              .find(([, p]) => p.values[s.key] !== null && p.values[s.key] !== undefined);
            return (
              <g key={s.key}>
                {segments.map((d) => (
                  <path
                    key={d}
                    d={d}
                    fill="none"
                    stroke={s.color}
                    strokeWidth={2}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                  />
                ))}
                {points.map((p, i) => {
                  const v = p.values[s.key];
                  const isEnd = last !== undefined && last[0] === i;
                  if (v === null || v === undefined || (!isEnd && active !== i)) return null;
                  return (
                    <circle
                      key={i}
                      cx={x(i)}
                      cy={y(v)}
                      r={4}
                      fill={s.color}
                      stroke="var(--surface)"
                      strokeWidth={2}
                    />
                  );
                })}
              </g>
            );
          })}
          {active !== null && (
            <line
              className="crosshair"
              x1={x(active)}
              x2={x(active)}
              y1={PAD.top}
              y2={PAD.top + innerH}
            />
          )}
          <rect
            className="hit"
            x={PAD.left}
            y={PAD.top}
            width={innerW}
            height={innerH}
            tabIndex={0}
            aria-label={`${label}: use the arrow keys to read values`}
            onPointerMove={nearest}
            onPointerLeave={() => {
              setActive(null);
            }}
            onFocus={() => {
              setActive(points.length - 1);
            }}
            onBlur={() => {
              setActive(null);
            }}
            onKeyDown={onKeyDown}
          />
        </svg>
        {activePoint !== undefined && active !== null && (
          <Tooltip
            x={x(active)}
            width={width}
            top={PAD.top}
            title={activePoint.title}
            rows={series.map((s) => {
              const v = activePoint.values[s.key];
              return {
                key: s.key,
                label: s.label,
                color: s.color,
                value: v === null || v === undefined ? "—" : format(v),
              };
            })}
          />
        )}
      </div>
    </div>
  );
}
