import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { api, unwrap, type Schemas } from "../api/client";
import { useMutationRun } from "../api/queries";
import { BarList } from "../charts/BarList";
import { Pager } from "../components/Pager";
import { Card, Empty, Notice, Query, StatTile, Status } from "../components/ui";
import {
  elapsed,
  formatDateTime,
  formatDuration,
  formatInteger,
  formatPercent,
  words,
} from "../lib/format";
import { isActive } from "../lib/status";

type MutantStatus = Schemas["MutantStatus"];
const PAGE = 25;

function Survivors({ id }: { id: string }) {
  const [status, setStatus] = useState<MutantStatus>("survived");
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: ["mutants", id, status, offset],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/mutation-runs/{mutation_run_id}/mutants", {
          params: { path: { mutation_run_id: id }, query: { status, limit: PAGE, offset } },
        }),
      ),
  });
  return (
    <Card title="Mutants" subtitle="Each one a small deliberate bug; a survivor is a missing test">
      <div className="toolbar">
        <label className="field">
          Status
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as MutantStatus);
              setOffset(0);
            }}
          >
            {(["survived", "no_coverage", "killed", "timeout", "error"] as const).map((s) => (
              <option key={s} value={s}>
                {words(s)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <Query query={query}>
        {(page) =>
          page.items.length === 0 ? (
            <Empty>No mutant with this status.</Empty>
          ) : (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Location</th>
                      <th>Mutation</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {page.items.map((m) => (
                      <tr key={m.id}>
                        <td className="mono">
                          {m.path}:{m.line}
                          <div className="muted">{m.function}</div>
                        </td>
                        <td className="mono">
                          {m.original} → {m.replacement}
                          <div className="muted">{words(m.operator)}</div>
                        </td>
                        <td>
                          <Status kind="mutant" value={m.status} />
                          {m.killed_by && <div className="muted mono">{m.killed_by}</div>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pager offset={offset} limit={PAGE} total={page.total} onChange={setOffset} />
            </>
          )
        }
      </Query>
    </Card>
  );
}

function WeakestFunctions({ id }: { id: string }) {
  const query = useQuery({
    queryKey: ["mutation-functions", id],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/mutation-runs/{mutation_run_id}/functions", {
          params: { path: { mutation_run_id: id }, query: { limit: 10 } },
        }),
      ),
  });
  return (
    <Card title="Weakest functions" subtitle="Lowest mutation score first">
      <Query query={query}>
        {(page) =>
          page.items.length === 0 ? (
            <Empty>No mutated functions.</Empty>
          ) : (
            <BarList
              label="Mutation score of the weakest functions"
              format={(v) => `${Math.round(v)} %`}
              items={page.items.map((f) => ({
                key: `${f.path}:${f.function}`,
                label: f.function,
                value: f.score ?? 0,
              }))}
            />
          )
        }
      </Query>
    </Card>
  );
}

export function MutationRunPage() {
  const { mutationRunId = "" } = useParams();
  const query = useMutationRun(mutationRunId);
  return (
    <Query query={query}>
      {(run) => {
        const totals = run.summary?.totals;
        return (
          <div className="stack">
            <header className="page-header" style={{ marginBottom: 0 }}>
              <div>
                <nav className="breadcrumbs" aria-label="Breadcrumbs">
                  <Link to={`/runs/${run.run_id}?tab=tests`}>Run</Link>
                </nav>
                <h1>Mutation testing</h1>
                <p>
                  <Status kind="job" value={run.status} /> · {formatDateTime(run.created_at)} · took{" "}
                  {formatDuration(elapsed(run.started_at, run.finished_at))}
                </p>
              </div>
            </header>
            {run.error_message && (
              <Notice title="The mutation run failed" error>
                {run.error_message}
              </Notice>
            )}
            {isActive(run.status) && (
              <Notice title={`Mutation testing is ${words(run.status)}`}>
                Every mutant runs the tests that cover it; this page refreshes by itself.
              </Notice>
            )}
            {totals && (
              <>
                <div className="grid grid-tiles">
                  <StatTile
                    label="Mutation score"
                    value={formatPercent(totals.score)}
                    detail={`covered code: ${formatPercent(totals.covered_score)}`}
                  />
                  <StatTile label="Mutants" value={formatInteger(totals.total)} />
                  <StatTile
                    label="Detected"
                    value={formatInteger(totals.killed + totals.timeout)}
                    detail={`${totals.timeout} by time-out`}
                  />
                  <StatTile
                    label="Survived"
                    value={formatInteger(totals.survived)}
                    detail={`${totals.no_coverage} not covered by any test`}
                  />
                </div>
                {run.summary && run.summary.notes.length > 0 && (
                  <Notice title="Notes">
                    <ul className="plain">
                      {run.summary.notes.map((n) => (
                        <li key={n}>{n}</li>
                      ))}
                    </ul>
                  </Notice>
                )}
                <div className="grid grid-2">
                  <Survivors id={mutationRunId} />
                  <WeakestFunctions id={mutationRunId} />
                </div>
              </>
            )}
          </div>
        );
      }}
    </Query>
  );
}
