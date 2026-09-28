import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type SyntheticEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { api, unwrap, type Schemas } from "../api/client";
import { useProjectOverview, useProjectRuns } from "../api/queries";
import { LineChart } from "../charts/LineChart";
import { StackedColumns } from "../charts/StackedColumns";
import { Pager } from "../components/Pager";
import { Card, Empty, ErrorView, Facts, Query, StatTile, Status } from "../components/ui";
import {
  axisLabels,
  elapsed,
  formatDateTime,
  formatDuration,
  formatPercent,
  shortSha,
} from "../lib/format";

const RUNS_LIMIT = 10;

const TEST_SERIES = [
  { key: "passed", label: "Passed", color: "var(--good)" },
  { key: "failed", label: "Failed", color: "var(--critical)" },
  { key: "skipped", label: "Skipped", color: "var(--neutral)" },
];

function Trends({ overview }: { overview: Schemas["ProjectOverview"] }) {
  const navigate = useNavigate();
  const executions = overview.executions;
  const ran = executions.filter((e) => e.tests !== null);
  const mutations = overview.mutation_runs.filter((m) => m.score !== null);
  const columnLabels = axisLabels(executions.map((e) => e.created_at));
  const coverageLabels = axisLabels(ran.map((e) => e.created_at));
  const mutationLabels = axisLabels(mutations.map((m) => m.created_at));
  return (
    <div className="grid grid-2">
      <Card
        title="Tests per execution"
        subtitle={`The last ${executions.length} execution(s), oldest first; select one to open it`}
      >
        {executions.length === 0 ? (
          <Empty>No test executions yet: open a run and execute its tests.</Empty>
        ) : (
          <StackedColumns
            label="Passed, failed and skipped tests per execution"
            series={TEST_SERIES}
            columns={executions.map((e, i) => ({
              id: e.id,
              label: columnLabels[i] ?? "",
              title: `${formatDateTime(e.created_at)} · ${e.outcome ?? e.status}`,
              values: e.tests
                ? { passed: e.tests.passed, failed: e.tests.failed, skipped: e.tests.skipped }
                : null,
            }))}
            onSelect={(id) => void navigate(`/executions/${id}`)}
          />
        )}
      </Card>
      <Card title="Coverage" subtitle="Line + branch coverage and API coverage, per execution">
        {ran.length === 0 ? (
          <Empty>No coverage measured yet.</Empty>
        ) : (
          <LineChart
            label="Coverage per execution"
            format={(v) => formatPercent(v)}
            tickFormat={(v) => `${Math.round(v)}%`}
            series={[
              { key: "coverage", label: "Code coverage", color: "var(--series-1)" },
              { key: "api", label: "API coverage", color: "var(--series-2)" },
            ]}
            points={ran.map((e, i) => ({
              label: coverageLabels[i] ?? "",
              title: formatDateTime(e.created_at),
              values: { coverage: e.coverage_percent, api: e.api_coverage_percent },
            }))}
          />
        )}
      </Card>
      <Card title="Mutation score" subtitle="Share of mutants the tests detect, per mutation run">
        {mutations.length === 0 ? (
          <Empty>No finished mutation run yet.</Empty>
        ) : (
          <LineChart
            label="Mutation score per mutation run"
            format={(v) => formatPercent(v)}
            tickFormat={(v) => `${Math.round(v)}%`}
            series={[{ key: "score", label: "Mutation score", color: "var(--series-1)" }]}
            points={mutations.map((m, i) => ({
              label: mutationLabels[i] ?? "",
              title: `${formatDateTime(m.created_at)} · ${m.mutants ?? "?"} mutants, ${
                m.survived ?? "?"
              } survived`,
              values: { score: m.score },
            }))}
          />
        )}
      </Card>
      <Card title="Regressions" subtitle="Comparisons between two commits">
        {overview.regressions.length === 0 ? (
          <Empty>No regression analysis yet.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Requested</th>
                  <th>Base → head</th>
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {overview.regressions.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <Link to={`/regressions/${r.id}`}>{formatDateTime(r.created_at)}</Link>
                    </td>
                    <td className="mono">
                      {r.base_ref} → {r.head_ref}
                    </td>
                    <td>
                      {r.verdict ? (
                        <Status kind="verdict" value={r.verdict} />
                      ) : (
                        <Status kind="job" value={r.status} />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function Runs({ projectId }: { projectId: string }) {
  const [offset, setOffset] = useState(0);
  const query = useProjectRuns(projectId, offset, RUNS_LIMIT);
  return (
    <Card title="Analysis runs">
      <Query query={query}>
        {(page) =>
          page.items.length === 0 ? (
            <Empty>Not analysed yet.</Empty>
          ) : (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Requested</th>
                      <th>Status</th>
                      <th className="num">Duration</th>
                      <th>Error</th>
                    </tr>
                  </thead>
                  <tbody>
                    {page.items.map((run) => (
                      <tr key={run.id}>
                        <td>
                          <Link to={`/runs/${run.id}`}>{formatDateTime(run.created_at)}</Link>
                        </td>
                        <td>
                          <Status kind="job" value={run.status} />
                        </td>
                        <td className="num">
                          {formatDuration(elapsed(run.started_at, run.finished_at))}
                        </td>
                        <td className="truncate">{run.error_message ?? ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pager offset={offset} limit={RUNS_LIMIT} total={page.total} onChange={setOffset} />
            </>
          )
        }
      </Query>
    </Card>
  );
}

function RegressionForm({ projectId }: { projectId: string }) {
  const navigate = useNavigate();
  const [base, setBase] = useState("HEAD~1");
  const [head, setHead] = useState("HEAD");
  const [mode, setMode] = useState<"full" | "selected">("selected");
  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/projects/{project_id}/regressions", {
          params: { path: { project_id: projectId } },
          body: { base, head, mode },
        }),
      ),
    onSuccess: (regression) => navigate(`/regressions/${regression.id}`),
  });
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    create.mutate();
  };
  return (
    <Card title="Compare two commits" subtitle="Git repositories only; tests run in the sandbox">
      <form className="stack" onSubmit={submit}>
        <div className="form-row">
          <label className="field">
            Base
            <input
              required
              value={base}
              onChange={(e) => {
                setBase(e.target.value);
              }}
            />
          </label>
          <label className="field">
            Head
            <input
              required
              value={head}
              onChange={(e) => {
                setHead(e.target.value);
              }}
            />
          </label>
          <label className="field">
            Tests
            <select
              value={mode}
              onChange={(e) => {
                setMode(e.target.value as "full" | "selected");
              }}
            >
              <option value="selected">Selected (affected tests)</option>
              <option value="full">Full suite</option>
            </select>
          </label>
          <button type="submit" disabled={create.isPending}>
            {create.isPending ? "Queueing…" : "Compare"}
          </button>
        </div>
        {create.error && <ErrorView error={create.error} />}
      </form>
    </Card>
  );
}

export function ProjectPage() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const query = useProjectOverview(projectId);
  const analyse = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/projects/{project_id}/runs", {
          params: { path: { project_id: projectId } },
        }),
      ),
    onSuccess: async (run) => {
      await client.invalidateQueries({ queryKey: ["project-overview", projectId] });
      await navigate(`/runs/${run.id}`);
    },
  });

  return (
    <Query query={query}>
      {(overview) => {
        const { project } = overview;
        const latest = overview.executions.filter((e) => e.tests !== null).at(-1);
        const score = overview.mutation_runs.filter((m) => m.score !== null).at(-1)?.score;
        return (
          <div className="stack">
            <header className="page-header" style={{ marginBottom: 0 }}>
              <div>
                <nav className="breadcrumbs" aria-label="Breadcrumbs">
                  <Link to="/projects">Projects</Link>
                </nav>
                <h1>{project.name}</h1>
                <p className="mono">
                  {project.remote_repository
                    ? `${project.remote_repository} @ ${project.remote_ref ?? ""} (${shortSha(
                        project.remote_commit,
                      )})`
                    : project.source_location}
                </p>
              </div>
              <button
                className="primary"
                type="button"
                disabled={analyse.isPending}
                onClick={() => {
                  analyse.mutate();
                }}
              >
                {analyse.isPending ? "Queueing…" : "Analyse now"}
              </button>
            </header>
            {analyse.error && <ErrorView error={analyse.error} />}
            <div className="grid grid-tiles">
              <StatTile
                label="Latest run"
                value={
                  overview.latest_run ? (
                    <Link to={`/runs/${overview.latest_run.id}`}>
                      <Status kind="job" value={overview.latest_run.status} />
                    </Link>
                  ) : (
                    "—"
                  )
                }
                detail={formatDateTime(overview.latest_run?.created_at)}
              />
              <StatTile
                label="Tests passing"
                value={latest?.tests ? `${latest.tests.passed}/${latest.tests.total}` : "—"}
                detail={latest ? `latest execution, ${formatDateTime(latest.created_at)}` : ""}
              />
              <StatTile label="Coverage" value={formatPercent(latest?.coverage_percent)} />
              <StatTile label="Mutation score" value={formatPercent(score)} />
            </div>
            <Trends overview={overview} />
            <div className="grid grid-2">
              <Runs projectId={projectId} />
              <div className="stack">
                <Card title="Project">
                  <Facts
                    items={[
                      ["Source", project.source_type],
                      ["Registered", formatDateTime(project.created_at)],
                      [
                        "Runs",
                        `${overview.runs.succeeded} succeeded, ${overview.runs.failed} failed, ${
                          overview.runs.queued + overview.runs.running
                        } active`,
                      ],
                    ]}
                  />
                </Card>
                {project.source_type === "local" && <RegressionForm projectId={projectId} />}
              </div>
            </div>
            {overview.pull_analyses.length > 0 && (
              <Card title="Pull request analyses">
                <ul className="plain">
                  {overview.pull_analyses.map((p) => (
                    <li key={p.id}>
                      <Link to={`/github/pull-analyses/${p.id}`}>
                        #{p.pull_number} · {shortSha(p.head_sha)}
                      </Link>{" "}
                      {p.conclusion ? (
                        <Status kind="conclusion" value={p.conclusion} />
                      ) : (
                        <Status kind="job" value={p.status} />
                      )}
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>
        );
      }}
    </Query>
  );
}
