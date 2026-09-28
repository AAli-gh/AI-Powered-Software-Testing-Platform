import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { api, unwrap, type Schemas } from "../api/client";
import { usePullAnalysis } from "../api/queries";
import { Card, Empty, ErrorView, Notice, Query, StatTile, Status } from "../components/ui";
import { formatDateTime, formatInteger, formatPercent, shortSha, words } from "../lib/format";
import { isActive } from "../lib/status";

function Suggestions({
  analysis,
  report,
}: {
  analysis: Schemas["PullAnalysisRead"];
  report: { suggestions: Schemas["IssueSuggestion"][] };
}) {
  const client = useQueryClient();
  const [chosen, setChosen] = useState<string[]>([]);
  const created = new Map(analysis.issues.map((i) => [i.suggestion, i]));
  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/github/pull-analyses/{analysis_id}/issues", {
          params: { path: { analysis_id: analysis.id } },
          body: { suggestions: chosen },
        }),
      ),
    onSuccess: async () => {
      setChosen([]);
      await client.invalidateQueries({ queryKey: ["pull-analysis", analysis.id] });
    },
  });
  if (report.suggestions.length === 0) {
    return (
      <Card title="Issue suggestions">
        <Empty>Nothing to report.</Empty>
      </Card>
    );
  }
  return (
    <Card
      title="Issue suggestions"
      subtitle="Created on GitHub only when you ask; each suggestion at most once"
      actions={
        <button
          className="primary"
          type="button"
          disabled={chosen.length === 0 || create.isPending}
          onClick={() => {
            create.mutate();
          }}
        >
          Create {chosen.length || ""} issue(s)
        </button>
      }
    >
      {create.error && <ErrorView error={create.error} />}
      <div className="stack">
        {report.suggestions.map((s) => {
          const issue = created.get(s.id);
          return (
            <div key={s.id} className="card">
              <div className="card-header">
                <label className="checkbox">
                  <input
                    type="checkbox"
                    disabled={issue !== undefined}
                    checked={issue !== undefined || chosen.includes(s.id)}
                    onChange={(e) => {
                      setChosen((current) =>
                        e.target.checked ? [...current, s.id] : current.filter((c) => c !== s.id),
                      );
                    }}
                  />
                  <strong>{s.title}</strong>
                </label>
                {issue ? (
                  <a href={issue.url} target="_blank" rel="noreferrer">
                    issue #{issue.number}
                  </a>
                ) : (
                  <span className="badge">{words(s.kind)}</span>
                )}
              </div>
              <details>
                <summary className="muted">Issue text</summary>
                <pre className="block">{s.body}</pre>
              </details>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function Report({ analysis }: { analysis: Schemas["PullAnalysisRead"] }) {
  const query = useQuery({
    queryKey: ["pull-report", analysis.id],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/github/pull-analyses/{analysis_id}/report", {
          params: { path: { analysis_id: analysis.id } },
        }),
      ),
  });
  return (
    <Query query={query}>
      {(report) => {
        const regressed = report.regression.tests.filter((t) => t.change === "regressed");
        return (
          <div className="stack">
            <div className="grid grid-tiles">
              <StatTile label="Regressed tests" value={formatInteger(regressed.length)} />
              <StatTile label="New findings" value={formatInteger(report.new_findings.length)} />
              <StatTile
                label="Untested changes"
                value={formatInteger(report.untested_changes.length)}
              />
              <StatTile
                label="Coverage at head"
                value={formatPercent(report.regression.head_run.coverage_percent)}
                detail={`base ${formatPercent(report.regression.base_run.coverage_percent)}`}
              />
            </div>
            <div className="grid grid-2">
              <Card title="Regressions" subtitle="Passed at the base, fail at the head">
                {regressed.length === 0 ? (
                  <Empty>No test regressed.</Empty>
                ) : (
                  <ul className="plain mono">
                    {regressed.map((t) => (
                      <li key={t.nodeid}>{t.nodeid}</li>
                    ))}
                  </ul>
                )}
              </Card>
              <Card title="New findings in changed files">
                {report.new_findings.length === 0 ? (
                  <Empty>No new finding.</Empty>
                ) : (
                  <ul className="plain">
                    {report.new_findings.map((f) => (
                      <li key={`${f.rule_id}:${f.path}:${f.line}`}>
                        <Status kind="severity" value={f.severity} />{" "}
                        <span className="mono">
                          {f.path}:{f.line}
                        </span>{" "}
                        {f.message}
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>
            <Card title="Changed functions that no test executes">
              {report.untested_changes.length === 0 ? (
                <Empty>Every changed function is executed by a test.</Empty>
              ) : (
                <ul className="plain mono">
                  {report.untested_changes.map((f) => (
                    <li key={`${f.path}:${f.qualname}`}>
                      {f.path}::{f.qualname} <span className="muted">({f.status})</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            <Suggestions analysis={analysis} report={report} />
            {report.notes.length > 0 && (
              <Notice title="Notes">
                <ul className="plain">
                  {report.notes.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              </Notice>
            )}
          </div>
        );
      }}
    </Query>
  );
}

export function PullAnalysisPage() {
  const { analysisId = "" } = useParams();
  const query = usePullAnalysis(analysisId);
  return (
    <Query query={query}>
      {(analysis) => (
        <div className="stack">
          <header className="page-header" style={{ marginBottom: 0 }}>
            <div>
              <nav className="breadcrumbs" aria-label="Breadcrumbs">
                <Link to="/github">Pull requests</Link>
              </nav>
              <h1>
                {analysis.repository}#{analysis.pull_number}
                {analysis.summary && (
                  <span className="muted" style={{ fontWeight: 400 }}>
                    {" "}
                    {analysis.summary.title}
                  </span>
                )}
              </h1>
              <p>
                {analysis.conclusion ? (
                  <Status kind="conclusion" value={analysis.conclusion} />
                ) : (
                  <Status kind="job" value={analysis.status} />
                )}{" "}
                ·{" "}
                <span className="mono">
                  {shortSha(analysis.base_sha)}..{shortSha(analysis.head_sha)}
                </span>{" "}
                · {formatDateTime(analysis.created_at)}
                {analysis.summary && (
                  <>
                    {" · "}
                    <a href={analysis.summary.url} target="_blank" rel="noreferrer">
                      open on GitHub
                    </a>
                  </>
                )}
              </p>
            </div>
          </header>
          {analysis.error_message && (
            <Notice title="The analysis failed" error>
              {analysis.error_message}
            </Notice>
          )}
          {isActive(analysis.status) && (
            <Notice title={`The analysis is ${words(analysis.status)}`}>
              Both commits are downloaded and their tests run in the sandbox; this page refreshes by
              itself.
            </Notice>
          )}
          {analysis.status === "succeeded" && <Report analysis={analysis} />}
        </div>
      )}
    </Query>
  );
}
