import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type SyntheticEvent } from "react";
import { Link, useNavigate } from "react-router";

import { api, unwrap } from "../api/client";
import { Pager } from "../components/Pager";
import { Card, Empty, ErrorView, Query, Status } from "../components/ui";
import { formatDateTime, shortSha } from "../lib/format";

const PAGE = 20;

function RequestAnalysis() {
  const navigate = useNavigate();
  const [repository, setRepository] = useState("");
  const [number, setNumber] = useState("");
  const [installation, setInstallation] = useState("");
  const request = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/github/pull-analyses", {
          body: {
            repository,
            pull_number: Number(number),
            installation_id: Number(installation),
          },
        }),
      ),
    onSuccess: (analysis) => navigate(`/github/pull-analyses/${analysis.id}`),
  });
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    request.mutate();
  };
  return (
    <Card
      title="Analyse a pull request now"
      subtitle="Without waiting for a webhook; needs the GitHub App (docs/github.md)"
    >
      <form className="stack" onSubmit={submit}>
        <div className="form-row">
          <label className="field">
            Repository
            <input
              required
              value={repository}
              onChange={(e) => {
                setRepository(e.target.value);
              }}
              placeholder="owner/name"
            />
          </label>
          <label className="field">
            Pull request
            <input
              required
              type="number"
              min={1}
              value={number}
              onChange={(e) => {
                setNumber(e.target.value);
              }}
            />
          </label>
          <label className="field">
            Installation id
            <input
              required
              type="number"
              min={1}
              value={installation}
              onChange={(e) => {
                setInstallation(e.target.value);
              }}
            />
          </label>
          <button className="primary" type="submit" disabled={request.isPending}>
            Analyse
          </button>
        </div>
        {request.error && <ErrorView error={request.error} />}
      </form>
    </Card>
  );
}

export function PullAnalysesPage() {
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: ["pull-analyses", offset],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/github/pull-analyses", { params: { query: { limit: PAGE, offset } } }),
      ),
  });
  return (
    <>
      <header className="page-header">
        <div>
          <h1>Pull requests</h1>
          <p>Analyses of GitHub pull requests: regressions, new findings and untested changes.</p>
        </div>
      </header>
      <div className="stack">
        <Card title="Analyses" subtitle="Newest first">
          <Query query={query}>
            {(page) =>
              page.items.length === 0 ? (
                <Empty>
                  No pull request has been analysed. With the GitHub App installed, every opened or
                  updated pull request is analysed automatically.
                </Empty>
              ) : (
                <>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Pull request</th>
                          <th>Head</th>
                          <th>Result</th>
                          <th>Summary</th>
                          <th>Requested</th>
                        </tr>
                      </thead>
                      <tbody>
                        {page.items.map((a) => (
                          <tr key={a.id}>
                            <td>
                              <Link to={`/github/pull-analyses/${a.id}`}>
                                {a.repository}#{a.pull_number}
                              </Link>
                            </td>
                            <td className="mono">{shortSha(a.head_sha)}</td>
                            <td>
                              {a.conclusion ? (
                                <Status kind="conclusion" value={a.conclusion} />
                              ) : (
                                <Status kind="job" value={a.status} />
                              )}
                            </td>
                            <td className="truncate">
                              {a.summary?.title ?? a.error_message ?? ""}
                            </td>
                            <td>{formatDateTime(a.created_at)}</td>
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
        <RequestAnalysis />
      </div>
    </>
  );
}
