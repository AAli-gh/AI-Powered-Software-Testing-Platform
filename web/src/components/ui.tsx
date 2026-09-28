import { useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode, type SyntheticEvent } from "react";

import { ApiError, readToken, saveToken } from "../api/client";
import { words } from "../lib/format";
import { toneOf, type StatusKind } from "../lib/status";

export function Card({
  title,
  subtitle,
  actions,
  children,
}: {
  title?: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="card">
      {(title ?? actions) !== undefined && (
        <div className="card-header">
          <div>
            {title !== undefined && <h2>{title}</h2>}
            {subtitle !== undefined && <p>{subtitle}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function StatTile({
  label,
  value,
  detail,
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
}) {
  return (
    <div className="card">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {detail !== undefined && <div className="tile-detail">{detail}</div>}
    </div>
  );
}

/** A state as a badge: a coloured dot plus its words (never colour alone). */
export function Status({ kind, value }: { kind: StatusKind; value: string | null | undefined }) {
  if (!value) return <span className="muted">—</span>;
  return <span className={`badge ${toneOf(kind, value)}`}>{words(value)}</span>;
}

export function Notice({
  title,
  children,
  error = false,
}: {
  title?: string;
  children: ReactNode;
  error?: boolean;
}) {
  return (
    <div className={error ? "notice error" : "notice"} role={error ? "alert" : "status"}>
      {title !== undefined && <strong>{title}</strong>}
      {children}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="empty" role="status" aria-live="polite">
      {label}
    </div>
  );
}

/** Asks for the API token (TESTMIND_API_TOKEN) when the API answers 401. */
export function TokenForm() {
  const client = useQueryClient();
  const [token, setToken] = useState("");
  const rejected = readToken() !== null;
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    saveToken(token.trim());
    setToken(""); // the token lives in session storage only
    void client.resetQueries();
  };
  return (
    <form className="notice error stack" role="alert" onSubmit={submit}>
      <strong>
        {rejected ? "The API token was not accepted" : "This TestMind needs an API token"}
      </strong>
      <span>Enter the value of TESTMIND_API_TOKEN. It is kept only in this browser tab.</span>
      <div className="form-row">
        <label className="field">
          API token
          <input
            type="password"
            autoComplete="off"
            required
            value={token}
            onChange={(e) => {
              setToken(e.target.value);
            }}
          />
        </label>
        <button className="primary" type="submit">
          Use token
        </button>
      </div>
    </form>
  );
}

/** An API error with the API's own message; a 404 reads as "not available", not a failure. */
export function ErrorView({ error }: { error: unknown }) {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return <TokenForm />;
    }
    if (error.notFound) {
      return <Notice title="Not available">{error.message}</Notice>;
    }
    return (
      <Notice title={`Error ${error.status || ""} ${words(error.code)}`.trim()} error>
        {error.message}
      </Notice>
    );
  }
  return (
    <Notice title="Unexpected error" error>
      {error instanceof Error ? error.message : String(error)}
    </Notice>
  );
}

interface QueryLike<T> {
  data: T | undefined;
  error: unknown;
  isPending: boolean;
}

/** Renders loading and error states, then `children` with the data. */
export function Query<T>({
  query,
  children,
}: {
  query: QueryLike<T>;
  children: (data: T) => ReactNode;
}) {
  if (query.data !== undefined) return <>{children(query.data)}</>;
  if (query.error) return <ErrorView error={query.error} />;
  return <Loading />;
}

export function Facts({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="facts">
      {items.map(([label, value]) => (
        <div key={label} style={{ display: "contents" }}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A 0–100 value as a meter, with its number (the number carries the value). */
export function Meter({ percent, label }: { percent: number | null | undefined; label: string }) {
  if (percent === null || percent === undefined) return <span className="muted">—</span>;
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(60px, 1fr) auto", gap: 8 }}>
      <div
        className="meter"
        role="meter"
        aria-label={label}
        aria-valuenow={clamped}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div style={{ width: `${clamped}%` }} />
      </div>
      <span className="num">{percent.toFixed(1)} %</span>
    </div>
  );
}
