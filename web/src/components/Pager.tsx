export function Pager({
  offset,
  limit,
  total,
  onChange,
}: {
  offset: number;
  limit: number;
  total: number;
  onChange: (offset: number) => void;
}) {
  if (total <= limit) return null;
  const last = Math.min(offset + limit, total);
  return (
    <div className="toolbar" style={{ justifyContent: "flex-end", marginTop: 12 }}>
      <span className="muted">
        {offset + 1}–{last} of {total}
      </span>
      <button
        type="button"
        disabled={offset === 0}
        onClick={() => {
          onChange(Math.max(0, offset - limit));
        }}
      >
        Previous
      </button>
      <button
        type="button"
        disabled={last >= total}
        onClick={() => {
          onChange(offset + limit);
        }}
      >
        Next
      </button>
    </div>
  );
}
