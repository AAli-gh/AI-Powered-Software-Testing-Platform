import { Link } from "react-router";

import type { Schemas } from "../api/client";
import { usePlatformOverview } from "../api/queries";
import { Card, Empty, Meter, Query, StatTile, Status } from "../components/ui";
import {
  formatCompactDateTime,
  formatInteger,
  formatPercent,
  shortSha,
  words,
} from "../lib/format";

type Overview = Schemas["PlatformOverview"];

function Tests({ point }: { point: Schemas["ExecutionPoint"] | null }) {
  if (!point?.tests) return <span className="muted">—</span>;
  const { passed, failed, total } = point.tests;
  return (
    <span className="num">
      {passed}/{total} passed{failed > 0 ? `, ${failed} failed` : ""}
    </span>
  );
}

function JobTable({ jobs }: { jobs: Overview["jobs"] }) {
  const statuses = ["queued", "running", "succeeded", "failed", "cancelled"] as const;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Job</th>
            {statuses.map((s) => (
              <th key={s} className="num">
                {s}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.kind}>
              <td>{words(job.kind)}</td>
              {statuses.map((s) => (
                <td key={s} className="num">
                  {job.counts[s] || <span className="muted">0</span>}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProjectTable({ projects }: { projects: Overview["project_health"] }) {
  if (projects.length === 0) {
    return (
      <Empty>
        No projects yet. <Link to="/projects">Register a local repository</Link> or import one from
        GitHub.
      </Empty>
    );
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Project</th>
            <th>Latest run</th>
            <th>Tests (latest execution)</th>
            <th style={{ minWidth: 150 }}>Coverage</th>
            <th className="num">Mutation score</th>
          </tr>
        </thead>
        <tbody>
          {projects.map((p) => (
            <tr key={p.id}>
              <td>
                <Link to={`/projects/${p.id}`}>{p.name}</Link>
                {p.remote_repository && <div className="muted">{p.remote_repository}</div>}
              </td>
              <td>
                {p.latest_run ? (
                  <Link to={`/runs/${p.latest_run.id}`}>
                    <Status kind="job" value={p.latest_run.status} />
                  </Link>
                ) : (
                  <span className="muted">never analysed</span>
                )}
                {p.active_jobs > 0 && <div className="muted">{p.active_jobs} active</div>}
              </td>
              <td>
                {p.latest_execution ? (
                  <Link to={`/executions/${p.latest_execution.id}`}>
                    <Tests point={p.latest_execution} />
                  </Link>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
              <td>
                <Meter
                  percent={p.latest_execution?.coverage_percent}
                  label={`Coverage of ${p.name}`}
                />
              </td>
              <td className="num">{formatPercent(p.latest_mutation_score)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RecentExecutions({ executions }: { executions: Overview["recent_executions"] }) {
  if (executions.length === 0) return <Empty>No test executions yet.</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Execution</th>
            <th>Project</th>
            <th>Outcome</th>
            <th>Tests</th>
            <th className="num">Coverage</th>
          </tr>
        </thead>
        <tbody>
          {executions.map((e) => (
            <tr key={e.id}>
              <td>
                <Link to={`/executions/${e.id}`} style={{ whiteSpace: "nowrap" }}>
                  {formatCompactDateTime(e.created_at)}
                </Link>
              </td>
              <td>
                <Link to={`/projects/${e.project_id}`}>{e.project_name}</Link>
              </td>
              <td>
                {e.outcome ? (
                  <Status kind="outcome" value={e.outcome} />
                ) : (
                  <Status kind="job" value={e.status} />
                )}
              </td>
              <td>
                <Tests point={e} />
              </td>
              <td className="num">{formatPercent(e.coverage_percent)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RecentPulls({ pulls }: { pulls: Overview["recent_pull_analyses"] }) {
  if (pulls.length === 0) {
    return <Empty>No pull request analyses (the GitHub App is optional).</Empty>;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Pull request</th>
            <th>Head</th>
            <th>Conclusion</th>
          </tr>
        </thead>
        <tbody>
          {pulls.map((p) => (
            <tr key={p.id}>
              <td>
                <Link to={`/github/pull-analyses/${p.id}`}>
                  {p.repository}#{p.pull_number}
                </Link>
              </td>
              <td className="mono">{shortSha(p.head_sha)}</td>
              <td>
                {p.conclusion ? (
                  <Status kind="conclusion" value={p.conclusion} />
                ) : (
                  <Status kind="job" value={p.status} />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function OverviewPage() {
  const query = usePlatformOverview();
  return (
    <>
      <header className="page-header">
        <div>
          <h1>Overview</h1>
          <p>Every number on this page comes from the jobs TestMind has run.</p>
        </div>
      </header>
      <Query query={query}>
        {(data) => {
          const executions = data.jobs.find((j) => j.kind === "test_executions")?.counts;
          const pulls = data.jobs.find((j) => j.kind === "github_pull_analyses")?.counts;
          const sum = (c: typeof executions) =>
            c ? c.queued + c.running + c.succeeded + c.failed + c.cancelled : 0;
          return (
            <div className="stack">
              <div className="grid grid-tiles">
                <StatTile label="Projects" value={formatInteger(data.projects)} />
                <StatTile
                  label="Active jobs"
                  value={formatInteger(data.active_jobs)}
                  detail={data.active_jobs > 0 ? "refreshing every 2 s" : "queue is idle"}
                />
                <StatTile
                  label="Test executions"
                  value={formatInteger(sum(executions))}
                  detail={`${formatInteger(executions?.failed ?? 0)} failed jobs`}
                />
                <StatTile label="Pull request analyses" value={formatInteger(sum(pulls))} />
              </div>
              <Card title="Projects" subtitle="Most recent activity first">
                <ProjectTable projects={data.project_health} />
              </Card>
              <Card title="Recent test executions">
                <RecentExecutions executions={data.recent_executions} />
              </Card>
              <div className="grid grid-2">
                <Card title="Recent pull request analyses">
                  <RecentPulls pulls={data.recent_pull_analyses} />
                </Card>
                <Card title="Jobs by status" subtitle="Every background job table">
                  <JobTable jobs={data.jobs} />
                </Card>
              </div>
            </div>
          );
        }}
      </Query>
    </>
  );
}
