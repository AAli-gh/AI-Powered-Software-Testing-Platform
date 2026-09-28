import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { api, unwrap, type Schemas } from "../api/client";
import { useProject, useRun } from "../api/queries";
import { BarList } from "../charts/BarList";
import { Pager } from "../components/Pager";
import { Tabs } from "../components/Tabs";
import { Card, Empty, ErrorView, Facts, Notice, Query, StatTile, Status } from "../components/ui";
import {
  elapsed,
  formatDateTime,
  formatDuration,
  formatInteger,
  formatPercent,
  formatRatio,
  words,
} from "../lib/format";
import { isActive } from "../lib/status";

type Severity = Schemas["Severity"];
const SEVERITIES: Severity[] = ["critical", "high", "medium", "low"];
const SEVERITY_COLOR: Record<Severity, string> = {
  critical: "var(--critical)",
  high: "var(--serious)",
  medium: "var(--warning)",
  low: "var(--neutral)",
};
const PAGE = 25;

function Summary({ runId }: { runId: string }) {
  const repository = useQuery({
    queryKey: ["run-analysis", runId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/runs/{run_id}/analysis", { params: { path: { run_id: runId } } })),
  });
  const code = useQuery({
    queryKey: ["run-code", runId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/runs/{run_id}/code", { params: { path: { run_id: runId } } })),
  });
  return (
    <div className="stack">
      <Query query={code}>
        {({ metrics }) => (
          <div className="grid grid-tiles">
            <StatTile label="Modules" value={formatInteger(metrics.modules)} />
            <StatTile
              label="Functions"
              value={formatInteger(metrics.functions + metrics.methods)}
              detail={`${formatInteger(metrics.methods)} methods, ${formatInteger(
                metrics.classes,
              )} classes`}
            />
            <StatTile label="Lines of code" value={formatInteger(metrics.lines)} />
            <StatTile
              label="Complexity"
              value={metrics.average_complexity.toFixed(1)}
              detail={`average; highest ${metrics.max_complexity}`}
            />
          </div>
        )}
      </Query>
      <div className="grid grid-2">
        <Card title="Findings by severity" subtitle="Static analysis of production code">
          <Query query={code}>
            {({ metrics }) => (
              <BarList
                label="Findings by severity"
                items={SEVERITIES.map((s) => ({
                  key: s,
                  label: s,
                  value: metrics.findings_by_severity[s] ?? 0,
                  color: SEVERITY_COLOR[s],
                }))}
              />
            )}
          </Query>
        </Card>
        <Card title="Languages" subtitle="Share of lines">
          <Query query={repository}>
            {(summary) =>
              summary.languages.length === 0 ? (
                <Empty>No source files recognised.</Empty>
              ) : (
                <BarList
                  label="Languages by share of lines"
                  format={(v) => formatPercent(v, 0)}
                  items={summary.languages.slice(0, 8).map((l) => ({
                    key: l.name,
                    label: l.name,
                    value: l.percentage,
                  }))}
                />
              )
            }
          </Query>
        </Card>
      </div>
      <Card title="Repository">
        <Query query={repository}>
          {(summary) => (
            <Facts
              items={[
                [
                  "Files",
                  `${formatInteger(summary.file_count)}${summary.truncated ? " (limit reached)" : ""}`,
                ],
                ["Primary language", summary.primary_language ?? "—"],
                ["Frameworks", summary.frameworks.map((f) => f.name).join(", ") || "none detected"],
                [
                  "Test frameworks",
                  summary.test_frameworks.map((f) => f.name).join(", ") || "none detected",
                ],
                ["Test files", formatInteger(summary.test_files.length)],
                ["Dependencies", formatInteger(summary.dependencies.length)],
                ["CI", summary.ci.map((c) => c.provider).join(", ") || "none"],
              ]}
            />
          )}
        </Query>
      </Card>
    </div>
  );
}

function Findings({ runId }: { runId: string }) {
  const [minSeverity, setMinSeverity] = useState<Severity | "">("");
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: ["run-findings", runId, minSeverity, offset],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/runs/{run_id}/code/findings", {
          params: {
            path: { run_id: runId },
            query: { limit: PAGE, offset, ...(minSeverity ? { min_severity: minSeverity } : {}) },
          },
        }),
      ),
  });
  return (
    <Card title="Findings" subtitle="Most severe first; each with the code it is about">
      <div className="toolbar">
        <label className="field">
          Minimum severity
          <select
            value={minSeverity}
            onChange={(e) => {
              setMinSeverity(e.target.value as Severity | "");
              setOffset(0);
            }}
          >
            <option value="">All</option>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      </div>
      <Query query={query}>
        {(page) =>
          page.items.length === 0 ? (
            <Empty>No findings.</Empty>
          ) : (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Severity</th>
                      <th>Rule</th>
                      <th>Location</th>
                      <th>Finding</th>
                      <th className="num">Confidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {page.items.map((f) => (
                      <tr key={`${f.rule_id}:${f.path}:${f.line}:${f.evidence}`}>
                        <td>
                          <Status kind="severity" value={f.severity} />
                        </td>
                        <td className="mono">{f.rule_id}</td>
                        <td className="mono">
                          {f.path}:{f.line}
                          {f.symbol && <div className="muted">{f.symbol}</div>}
                        </td>
                        <td>
                          {f.message}
                          <div className="mono muted truncate" title={f.evidence}>
                            {f.evidence}
                          </div>
                        </td>
                        <td className="num">{formatRatio(f.confidence)}</td>
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

function Functions({ runId }: { runId: string }) {
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: ["run-functions", runId, offset],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/runs/{run_id}/code/functions", {
          params: { path: { run_id: runId }, query: { sort: "complexity", limit: PAGE, offset } },
        }),
      ),
  });
  return (
    <Card title="Functions" subtitle="Most complex first">
      <Query query={query}>
        {(page) =>
          page.items.length === 0 ? (
            <Empty>No functions.</Empty>
          ) : (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Function</th>
                      <th>Location</th>
                      <th className="num">Complexity</th>
                      <th className="num">Lines</th>
                    </tr>
                  </thead>
                  <tbody>
                    {page.items.map((f) => (
                      <tr key={`${f.path}:${f.qualname}:${f.line}`}>
                        <td className="mono">{f.qualname}</td>
                        <td className="mono muted">
                          {f.path}:{f.line}
                        </td>
                        <td className="num">
                          {f.complexity} <span className="muted">({f.complexity_rank})</span>
                        </td>
                        <td className="num">{f.lines}</td>
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

function Endpoints({ runId }: { runId: string }) {
  const query = useQuery({
    queryKey: ["run-api", runId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/runs/{run_id}/api", { params: { path: { run_id: runId } } })),
  });
  return (
    <Card title="HTTP endpoints" subtitle="Discovered statically (FastAPI, Flask, Django)">
      <Query query={query}>
        {(surface) => (
          <div className="stack">
            {(surface.endpoints ?? []).length === 0 ? (
              <Empty>No HTTP endpoints found.</Empty>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Method</th>
                      <th>Path</th>
                      <th>Handler</th>
                      <th>Framework</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(surface.endpoints ?? []).map((e) => (
                      <tr key={`${e.app ?? ""}:${e.method}:${e.path}`}>
                        <td className="mono">{e.method}</td>
                        <td className="mono">{e.path}</td>
                        <td className="mono muted">
                          {e.handler ? `${e.handler.module}:${e.handler.qualname}` : "—"}
                        </td>
                        <td>{e.framework}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {(surface.notes ?? []).length > 0 && (
              <Notice title="Not resolved statically">
                <ul className="plain">
                  {(surface.notes ?? []).map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              </Notice>
            )}
          </div>
        )}
      </Query>
    </Card>
  );
}

function Executions({ runId }: { runId: string }) {
  const navigate = useNavigate();
  const [probes, setProbes] = useState(false);
  const executions = useQuery({
    queryKey: ["run-executions", runId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/runs/{run_id}/test-executions", {
          params: { path: { run_id: runId }, query: { limit: 50 } },
        }),
      ),
  });
  const mutations = useQuery({
    queryKey: ["run-mutations", runId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/runs/{run_id}/mutation-runs", {
          params: { path: { run_id: runId }, query: { limit: 50 } },
        }),
      ),
  });
  const execute = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/runs/{run_id}/test-executions", {
          params: { path: { run_id: runId } },
          body: { api_probes: probes },
        }),
      ),
    onSuccess: (execution) => navigate(`/executions/${execution.id}`),
  });
  const mutate = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/runs/{run_id}/mutation-runs", { params: { path: { run_id: runId } } }),
      ),
    onSuccess: (run) => navigate(`/mutation-runs/${run.id}`),
  });

  return (
    <div className="grid grid-2">
      <Card
        title="Test executions"
        subtitle="The repository's test suite in the sandbox"
        actions={
          <div className="toolbar" style={{ margin: 0 }}>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={probes}
                onChange={(e) => {
                  setProbes(e.target.checked);
                }}
              />
              API probes
            </label>
            <button
              className="primary"
              type="button"
              disabled={execute.isPending}
              onClick={() => {
                execute.mutate();
              }}
            >
              Run tests
            </button>
          </div>
        }
      >
        {execute.error && <ErrorView error={execute.error} />}
        <Query query={executions}>
          {(page) =>
            page.items.length === 0 ? (
              <Empty>No executions yet.</Empty>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Requested</th>
                    <th>Result</th>
                    <th className="num">Tests</th>
                    <th className="num">Coverage</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((e) => (
                    <tr key={e.id}>
                      <td>
                        <Link to={`/executions/${e.id}`}>{formatDateTime(e.created_at)}</Link>
                      </td>
                      <td>
                        {e.outcome ? (
                          <Status kind="outcome" value={e.outcome} />
                        ) : (
                          <Status kind="job" value={e.status} />
                        )}
                      </td>
                      <td className="num">
                        {e.summary ? `${e.summary.totals.passed}/${e.summary.totals.total}` : "—"}
                      </td>
                      <td className="num">{formatPercent(e.summary?.coverage_percent)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          }
        </Query>
      </Card>
      <Card
        title="Mutation runs"
        subtitle="How many deliberate bugs the tests detect"
        actions={
          <button
            type="button"
            disabled={mutate.isPending}
            onClick={() => {
              mutate.mutate();
            }}
          >
            Run mutation testing
          </button>
        }
      >
        {mutate.error && <ErrorView error={mutate.error} />}
        <Query query={mutations}>
          {(page) =>
            page.items.length === 0 ? (
              <Empty>No mutation runs yet.</Empty>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Requested</th>
                    <th>Status</th>
                    <th className="num">Score</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((m) => (
                    <tr key={m.id}>
                      <td>
                        <Link to={`/mutation-runs/${m.id}`}>{formatDateTime(m.created_at)}</Link>
                      </td>
                      <td>
                        <Status kind="job" value={m.status} />
                      </td>
                      <td className="num">{formatPercent(m.score)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          }
        </Query>
      </Card>
    </div>
  );
}

export function RunPage() {
  const { runId = "" } = useParams();
  const client = useQueryClient();
  const query = useRun(runId);
  const project = useProject(query.data?.project_id);
  const cancel = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/runs/{run_id}/cancel", { params: { path: { run_id: runId } } })),
    onSuccess: () => client.invalidateQueries({ queryKey: ["run", runId] }),
  });

  return (
    <Query query={query}>
      {(run) => (
        <div className="stack">
          <header className="page-header" style={{ marginBottom: 0 }}>
            <div>
              <nav className="breadcrumbs" aria-label="Breadcrumbs">
                <Link to="/projects">Projects</Link>/
                <Link to={`/projects/${run.project_id}`}>{project.data?.name ?? "Project"}</Link>
              </nav>
              <h1>Analysis run</h1>
              <p>
                <Status kind="job" value={run.status} /> · requested{" "}
                {formatDateTime(run.created_at)} · took{" "}
                {formatDuration(elapsed(run.started_at, run.finished_at))}
              </p>
            </div>
            {run.status === "queued" && (
              <button
                type="button"
                onClick={() => {
                  cancel.mutate();
                }}
              >
                Cancel
              </button>
            )}
          </header>
          {cancel.error && <ErrorView error={cancel.error} />}
          {run.error_message && (
            <Notice title="The run failed" error>
              {run.error_message}
            </Notice>
          )}
          {isActive(run.status) && (
            <Notice title={`The run is ${words(run.status)}`}>
              Results appear here when it finishes; this page refreshes by itself.
            </Notice>
          )}
          {run.status === "succeeded" && (
            <Tabs
              tabs={[
                { id: "summary", label: "Summary", render: () => <Summary runId={runId} /> },
                { id: "findings", label: "Findings", render: () => <Findings runId={runId} /> },
                { id: "functions", label: "Functions", render: () => <Functions runId={runId} /> },
                { id: "api", label: "API", render: () => <Endpoints runId={runId} /> },
                { id: "tests", label: "Tests", render: () => <Executions runId={runId} /> },
              ]}
            />
          )}
        </div>
      )}
    </Query>
  );
}
