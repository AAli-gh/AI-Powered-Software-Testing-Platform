export interface Series {
  key: string;
  label: string;
  /** A CSS colour, normally a token: var(--series-1), var(--good), … */
  color: string;
}

export function Legend({ series, shape }: { series: Series[]; shape: "line" | "box" }) {
  return (
    <div className="legend">
      {series.map((s) => (
        <span key={s.key}>
          <i className={shape === "box" ? "key box" : "key"} style={{ background: s.color }} />
          {s.label}
        </span>
      ))}
    </div>
  );
}

export interface TooltipRow {
  key: string;
  label: string;
  color: string;
  value: string;
}

/** Values lead, labels follow; series are keyed with a short line in their colour. */
export function Tooltip({
  x,
  width,
  top,
  title,
  rows,
}: {
  /** The pointer's x in the chart, in pixels; the tooltip sits beside it, inside the plot. */
  x: number;
  width: number;
  top: number;
  title: string;
  rows: TooltipRow[];
}) {
  const flip = x > width * 0.6;
  return (
    <div
      className="tooltip"
      role="status"
      style={{
        left: x,
        top,
        transform: flip ? "translateX(calc(-100% - 10px))" : "translateX(10px)",
      }}
    >
      <div className="tooltip-title">{title}</div>
      {rows.map((row) => (
        <div className="tooltip-row" key={row.key}>
          <i className="key" style={{ background: row.color }} />
          <strong>{row.value}</strong>
          <span className="muted">{row.label}</span>
        </div>
      ))}
    </div>
  );
}
