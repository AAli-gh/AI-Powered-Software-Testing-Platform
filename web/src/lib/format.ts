const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const shortDate = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const compact = new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "short" });
const time = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const integer = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });

export function formatDateTime(value: string | null | undefined): string {
  return value ? dateTime.format(new Date(value)) : "—";
}

export function formatShortDate(value: string): string {
  return shortDate.format(new Date(value));
}

export function formatCompactDateTime(value: string): string {
  return compact.format(new Date(value));
}

/** Axis labels for a series of timestamps: times when they all fall on one day, else dates. */
export function axisLabels(values: string[]): string[] {
  const days = new Set(values.map((v) => new Date(v).toDateString()));
  return values.map((v) => (days.size <= 1 ? time : shortDate).format(new Date(v)));
}

export function formatInteger(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : integer.format(value);
}

/** A percentage stored as 0–100. */
export function formatPercent(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(digits)} %`;
}

/** A ratio stored as 0–1 (mutation scores), shown as a percentage. */
export function formatRatio(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : formatPercent(value * 100);
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${Math.round(seconds % 60)} s`;
}

/** Seconds between two timestamps, if both are set. */
export function elapsed(start: string | null, end: string | null): number | null {
  if (!start || !end) return null;
  return (new Date(end).getTime() - new Date(start).getTime()) / 1000;
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 7) : "—";
}

/** snake_case enum values as words: "timed_out" → "timed out". */
export function words(value: string): string {
  return value.replaceAll("_", " ");
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}
