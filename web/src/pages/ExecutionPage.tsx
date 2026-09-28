import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { api, unwrap, type Schemas } from "../api/client";
import { useExecution, useProject, useRun } from "../api/queries";
import { BarList } from "../charts/BarList";
import { Pager } from "../components/Pager";
import { Tabs } from "../components/Tabs";
import { Card, Empty, Facts, Meter, Notice, Query, StatTile, Status } from "../components/ui";
import {
  elapsed,
  formatDateTime,
  formatDuration,
  formatInteger,
  formatPercent,
  words,
} from "../lib/format";
import { isActive } from "../lib/status";

const PAGE = 25;
type Outcome = Schemas["Outcome"];
const OUTCOMES: Outcome[] = ["failed", "error", "passed", "skipped", "xfailed"];

function Tests({ id }: { id: string }) {
  const [outcome, setOutcome] = useState<Outcome | "">("");
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: ["execution-tests", id, outcome, offset],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/test-executions/{execution_id}/tests", {
          params: {
            path: { execution_id: id },
            query: { limit: PAGE, offset, ...(outcome ? { outcome } : {}) },
          },
        }),
      ),
  });
  return (
    <Card title="Tests">
      <div className="toolbar">
        <label className="field">
          Outcome
          <select
            value={outcome}
            onChange={(e) => {
              setOutcome(e.target.value as Outcome | "");
              setOffset(0);
            }}
          >
            <option value="">All</option>
            {OUTCOMES.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </label>
      </div>
      <Query query={query}>
        {(page) =>
          page.items.length === 0 ? (
            <Empty>No tests.</Empty>
          ) : (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Test</th>
                      <th>Outcome</th>
                      <th className="num">Duration</th>
                    </tr>
                  </thead>
                  <tbody>
                    {page.items.map((t) => (
                      <tr key={t.nodeid}>
                        <td>
                          <span className="mono">{t.nodeid}</span>
                          {t.generated && <span className="badge active">generated</span>}
                          {t.message && (
                            <details>
                              <summary className="muted truncate">{t.message}</summary>
                              {t.traceback && <pre className="block">{t.traceback}</pre>}
                            </details>
                          )}
                        </td>
                        <td>
                          <Status kind="outcome" value={t.outcome} />
                        </td>
                        <td className="num">{formatDuration(t.duration_seconds)}</td>
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

function Coverage({ id }: { id: string }) {
  const [offset, setOffset] = useState(0);
  const overview = useQuery({
    queryKey: ["execution-coverage", id],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/test-executions/{execution_id}/coverage", {
          params: { path: { execution_id: id } },
        }),
      ),
  });
  const files = useQuery({
    queryKey: ["execution-coverage-files", id, offset],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/test-executions/{execution_id}/coverage/files", {
          params: { path: { execution_id: id }, query: { sort: "percent", limit: PAGE, offset } },
        }),
      ),
  });
  return (
    <Query query={overview}>
      {(coverage) => (
        <div className="stack">
          <div className="grid grid-tiles">
            <StatTile label="Total coverage" value={formatPercent(coverage.totals.percent)} />
            <StatTile
              label="Lines"
              value={formatPercent(coverage.totals.line_percent)}
              detail={`${formatInteger(coverage.totals.covered_lines)} of ${formatInteger(
                coverage.totals.statements,
              )}`}
            />
            <StatTile
              label="Branches"
              value={coverage.branch_coverage ? formatPercent(coverage.totals.branch_percent) : "—"}
              detail={
                coverage.branch_coverage
                  ? `${formatInteger(coverage.totals.covered_branches)} of ${formatInteger(
                      coverage.totals.branches,
                    )}`
                  : "not measured"
              }
            />
            <StatTile
              label="Functions never run"
              value={formatInteger(coverage.totals.functions_never_executed)}
              detail={`of ${formatInteger(coverage.totals.functions)}`}
            />
          </div>
          <Card
            title="Riskiest gaps"
            subtitle={`Functions below ${coverage.threshold} %, ranked by risk (${coverage.gap_count} in total)`}
          >
            {coverage.top_gaps.length === 0 ? (
              <Empty>No function below the threshold.</Empty>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Function</th>
                      <th className="num">Coverage</th>
                      <th>Why it matters</th>
                    </tr>
                  </thead>
                  <tbody>
                    {coverage.top_gaps.map((g) => (
                      <tr key={`${g.path}:${g.qualname}`}>
                        <td className="mono">
                          {g.qualname}
                          <div className="muted">
                            {g.path}:{g.line}
                          </div>
                        </td>
                        <td className="num">{formatPercent(g.percent)}</td>
                        <td>{g.reasons.join("; ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
          <Card title="Files" subtitle="Least covered first">
            <Query query={files}>
              {(page) => (
                <>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>File</th>
                          <th style={{ minWidth: 180 }}>Coverage</th>
                          <th>Missing lines</th>
                        </tr>
                      </thead>
                      <tbody>
                        {page.items.map((f) => (
                          <tr key={f.path}>
                            <td className="mono">{f.path}</td>
                            <td>
                              <Meter percent={f.percent} label={`Coverage of ${f.path}`} />
                            </td>
                            <td className="mono muted truncate">{f.missing.join(", ")}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <Pager offset={offset} limit={PAGE} total={page.total} onChange={setOffset} />
                </>
              )}
            </Query>
          </Card>
        </div>
      )}
    </Query>
  );
}

function Failures({ id }: { id: string }) {
  const overview = useQuery({
    queryKey: ["execution-failures", id],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/test-executions/{execution_id}/failures", {
          params: { path: { execution_id: id } },
        }),
      ),
  });
  const groups = useQuery({
    queryKey: ["execution-failure-groups", id],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/test-executions/{execution_id}/failures/groups", {
          params: { path: { execution_id: id }, query: { limit: 50 } },
        }),
      ),
  });
  return (
    <Query query={overview}>
      {(failures) => (
        <div className="stack">
          <div className="grid grid-2">
            <Card title="Failed tests by category">
              {failures.failed_tests === 0 ? (
                <Empty>No failed tests.</Empty>
              ) : (
                <BarList
                  label="Failed tests by category"
                  items={Object.entries(failures.by_category).map(([category, count]) => ({
                    key: category,
                    label: words(category),
                    value: count,
                    color: "var(--critical)",
                  }))}
                />
              )}
            </Card>
            <Card title="Deterministic analysis">
              <Facts
                items={[
                  ["Failed tests", formatInteger(failures.failed_tests)],
                  ["Distinct causes", formatInteger(failures.groups)],
                  ["Cause decided", `${formatInteger(failures.decided)} of ${failures.groups}`],
                ]}
              />
            </Card>
          </div>
          <Card title="Failure groups" subtitle="Tests that fail for the same reason">
            <Query query={groups}>
              {(page) =>
                page.items.length === 0 ? (
                  <Empty>No failures.</Empty>
                ) : (
                  <div className="stack">
                    {page.items.map((g) => (
                      <div key={g.id} className="card">
                        <div className="card-header">
                          <div>
                            <h2 className="mono">{g.exception_type ?? words(g.category)}</h2>
                            <p>
                              {words(g.category)} · {g.tests.length} test(s)
                              {g.crash && ` · ${g.crash.path}:${g.crash.line}`}
                            </p>
                          </div>
                        </div>
                        <pre className="block">{g.message}</pre>
                        {g.blame_reason && <p className="muted">{g.blame_reason}</p>}
                        <ul className="plain mono">
                          {g.tests.slice(0, 10).map((t) => (
                            <li key={t.nodeid}>{t.nodeid}</li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                )
              }
            </Query>
          </Card>
        </div>
      )}
    </Query>
  );
}

function ApiCoverage({ id }: { id: string }) {
  const overview = useQuery({
    queryKey: ["execution-api", id],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/test-executions/{execution_id}/api-coverage", {
          params: { path: { execution_id: id } },
        }),
      ),
  });
  const endpoints = useQuery({
    queryKey: ["execution-api-endpoints", id],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/test-executions/{execution_id}/api-coverage/endpoints", {
          params: { path: { execution_id: id }, query: { limit: 200 } },
        }),
      ),
  });
  return (
    <Query query={overview}>
      {(coverage) => (
        <div className="stack">
          <div className="grid grid-tiles">
            <StatTile label="API coverage" value={formatPercent(coverage.totals.percent)} />
            <StatTile
              label="Endpoints tested"
              value={`${coverage.totals.tested}/${coverage.totals.endpoints}`}
            />
            <StatTile label="Gaps" value={formatInteger(coverage.gap_count)} />
            <StatTile
              label="Requests no route answered"
              value={formatInteger(coverage.unmatched_requests)}
            />
          </div>
          {Object.keys(coverage.probes).length > 0 && (
            <Card title="Probes" subtitle="Deterministic invalid requests">
              <BarList
                label="Probe verdicts"
                items={Object.entries(coverage.probes).map(([verdict, count]) => ({
                  key: verdict,
                  label: words(verdict),
                  value: count,
                  color:
                    verdict === "passed"
                      ? "var(--good)"
                      : verdict === "server_error"
                        ? "var(--critical)"
                        : "var(--serious)",
                }))}
              />
            </Card>
          )}
          <Card title="Endpoints">
            <Query query={endpoints}>
              {(page) => (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Endpoint</th>
                        <th className="num">Calls</th>
                        <th>Statuses</th>
                        <th>Tested with</th>
                      </tr>
                    </thead>
                    <tbody>
                      {page.items.map((e) => (
                        <tr key={`${e.method} ${e.path}`}>
                          <td className="mono">
                            {e.method} {e.path}
                          </td>
                          <td className="num">{e.calls}</td>
                          <td className="mono">
                            {Object.entries(e.statuses)
                              .map(([code, n]) => `${code}×${n}`)
                              .join(" ") || "—"}
                          </td>
                          <td>
                            {e.tested
                              ? [e.success_tested && "success", e.error_tested && "errors"]
                                  .filter(Boolean)
                                  .join(" and ")
                              : "untested"}
                            {e.server_errors > 0 && (
                              <div>
                                <span className="badge critical">
                                  {e.server_errors} server error(s)
                                </span>
                              </div>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Query>
          </Card>
        </div>
      )}
    </Query>
  );
}

function Logs({ id }: { id: string }) {
  const query = useQuery({
    queryKey: ["execution-logs", id],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/test-executions/{execution_id}/logs", {
          params: { path: { execution_id: id } },
        }),
      ),
  });
  return (
    <Query query={query}>
      {(logs) => (
        <div className="stack">
          <Card title="pytest output">
            <pre className="block">{logs.output || "(empty)"}</pre>
          </Card>
          {logs.install_log && (
            <Card title="Dependency installation">
              <pre className="block">{logs.install_log}</pre>
            </Card>
          )}
        </div>
      )}
    </Query>
  );
}

export function ExecutionPage() {
  const { executionId = "" } = useParams();
  const query = useExecution(executionId);
  const run = useRun(query.data?.run_id ?? "");
  const project = useProject(run.data?.project_id);
  return (
    <Query query={query}>
      {(execution) => {
        const summary = execution.summary;
        const totals = summary?.totals;
        return (
          <div className="stack">
            <header className="page-header" style={{ marginBottom: 0 }}>
              <div>
                <nav className="breadcrumbs" aria-label="Breadcrumbs">
                  {run.data && (
                    <Link to={`/projects/${run.data.project_id}`}>
                      {project.data?.name ?? "Project"}
                    </Link>
                  )}
                  /<Link to={`/runs/${execution.run_id}`}>Run</Link>
                </nav>
                <h1>Test execution</h1>
                <p>
                  {execution.outcome ? (
                    <Status kind="outcome" value={execution.outcome} />
                  ) : (
                    <Status kind="job" value={execution.status} />
                  )}{" "}
                  · {formatDateTime(execution.created_at)} · took{" "}
                  {formatDuration(elapsed(execution.started_at, execution.finished_at))}
                  {execution.api_probes && " · with API probes"}
                </p>
              </div>
            </header>
            {execution.error_message && (
              <Notice title="The execution failed" error>
                {execution.error_message}
              </Notice>
            )}
            {isActive(execution.status) && (
              <Notice title={`The execution is ${words(execution.status)}`}>
                The tests run in the sandbox; this page refreshes by itself.
              </Notice>
            )}
            {summary && totals && (
              <>
                <div className="grid grid-tiles">
                  <StatTile
                    label="Tests"
                    value={`${totals.passed}/${totals.total}`}
                    detail={`${totals.failed + totals.errors} failed, ${totals.skipped} skipped`}
                  />
                  <StatTile label="Coverage" value={formatPercent(summary.coverage_percent)} />
                  <StatTile
                    label="API coverage"
                    value={formatPercent(summary.api_coverage_percent)}
                  />
                  <StatTile label="Failure causes" value={formatInteger(summary.failure_groups)} />
                </div>
                {summary.coverage_error && (
                  <Notice title="Coverage not measured">{summary.coverage_error}</Notice>
                )}
                <Tabs
                  tabs={[
                    { id: "tests", label: "Tests", render: () => <Tests id={executionId} /> },
                    {
                      id: "coverage",
                      label: "Coverage",
                      render: () => <Coverage id={executionId} />,
                    },
                    {
                      id: "failures",
                      label: "Failures",
                      render: () => <Failures id={executionId} />,
                    },
                    {
                      id: "api",
                      label: "API coverage",
                      render: () => <ApiCoverage id={executionId} />,
                    },
                    { id: "logs", label: "Logs", render: () => <Logs id={executionId} /> },
                  ]}
                />
              </>
            )}
          </div>
        );
      }}
    </Query>
  );
}
