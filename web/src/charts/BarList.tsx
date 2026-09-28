export interface BarItem {
  key: string;
  label: string;
  value: number;
  color?: string;
}

/**
 * Horizontal bars for a distribution (findings by severity, languages, …). Each row carries
 * its label and value as text, so the bar only adds the comparison.
 */
export function BarList({
  items,
  format = (v) => String(v),
  label,
}: {
  items: BarItem[];
  format?: (value: number) => string;
  label: string;
}) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <div className="bar-list" role="list" aria-label={label}>
      {items.map((item) => (
        <div className="bar-row" role="listitem" key={item.key}>
          <span className="truncate" title={item.label}>
            {item.label}
          </span>
          <div className="bar-track">
            {item.value > 0 && (
              <div
                className="bar-fill"
                style={{
                  width: `${(item.value / max) * 100}%`,
                  background: item.color ?? "var(--series-1)",
                }}
              />
            )}
          </div>
          <span className="value">{format(item.value)}</span>
        </div>
      ))}
    </div>
  );
}
