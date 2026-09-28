import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { api, unwrap, type Schemas } from "../api/client";
import { useRegression } from "../api/queries";
import { Pager } from "../components/Pager";
import { Card, Empty, Notice, Query, StatTile, Status } from "../components/ui";
import { formatDateTime, formatInteger, shortSha, words } from "../lib/format";
import { isActive } from "../lib/status";

type Change = Schemas["TestChangeKind"];
const CHANGES: Change[] = ["regressed", "fixed", "still_failing", "added", "removed", "not_run"];
const PAGE = 50;

function TestChanges({ id }: { id: string }) {
  const [change, setChange] = useState<Change | "">("regressed");
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: ["regression-tests", id, change, offset],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/regressions/{regression_id}/tests", {
          params: {
            path: { regression_id: id },
            query: { limit: PAGE, offset, ...(change ? { change } : {}) },
          },
        }),
      ),
  });
  return (
    <Card title="Tests" subtitle="Each test's outcome at the base and at the head">
      <div className="toolbar">
        <label className="field">
          Change
          <select
            value={change}
            onChange={(e) => {
              setChange(e.target.value as Change | "");
              setOffset(0);
            }}
          >
            <option value="">All</option>
            {CHANGES.map((c) => (
              <option key={c} value={c}>
                {words(c)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <Query query={query}>
        {(page) =>
          page.items.length === 0 ? (
            <Empty>No test with this change.</Empty>
          ) : (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Test</th>
                      <th>Change</th>
                      <th>Base</th>
                      <th>Head</th>
                    </tr>
                  </thead>
                  <tbody>
                    {page.items.map((t) => (
                      <tr key={t.nodeid}>
                        <td className="mono">
                          {t.nodeid}
                          {t.selected && <div className="muted">selected</div>}
                        </td>
                        <td>
                          <Status kind="change" value={t.change} />
                        </td>
                        <td>
                          <Status kind="outcome" value={t.base} />
                        </td>
                        <td>
                          <Status kind="outcome" value={t.head} />
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

export function RegressionPage() {
  const { regressionId = "" } = useParams();
  const query = useRegression(regressionId);
  return (
    <Query query={query}>
      {(regression) => {
        const summary = regression.summary;
        return (
          <div className="stack">
            <header className="page-header" style={{ marginBottom: 0 }}>
              <div>
                <nav className="breadcrumbs" aria-label="Breadcrumbs">
                  <Link to={`/projects/${regression.project_id}`}>Project</Link>
                </nav>
                <h1>
                  Regression analysis{" "}
                  <span className="mono muted">
                    {regression.base_ref} → {regression.head_ref}
                  </span>
                </h1>
                <p>
                  {regression.verdict ? (
                    <Status kind="verdict" value={regression.verdict} />
                  ) : (
                    <Status kind="job" value={regression.status} />
                  )}{" "}
                  · {words(regression.mode)} mode ·{" "}
                  <span className="mono">
                    {shortSha(regression.base_commit)}..{shortSha(regression.head_commit)}
                  </span>{" "}
                  · {formatDateTime(regression.created_at)}
                </p>
              </div>
            </header>
            {regression.error_message && (
              <Notice title="The analysis failed" error>
                {regression.error_message}
              </Notice>
            )}
            {isActive(regression.status) && (
              <Notice title={`The analysis is ${words(regression.status)}`}>
                The tests run at both commits in the sandbox; this page refreshes by itself.
              </Notice>
            )}
            {summary && (
              <>
                <div className="grid grid-tiles">
                  <StatTile label="Regressed tests" value={formatInteger(summary.regressed)} />
                  <StatTile label="Fixed tests" value={formatInteger(summary.fixed)} />
                  <StatTile
                    label="Changed functions"
                    value={formatInteger(summary.changed_functions)}
                    detail={`in ${formatInteger(summary.changed_files)} file(s)`}
                  />
                  <StatTile
                    label="Missed by selection"
                    value={
                      summary.missed_by_selection === null
                        ? "—"
                        : formatInteger(summary.missed_by_selection)
                    }
                    detail={
                      summary.everything_selected
                        ? "every test was selected"
                        : `${formatInteger(summary.selected_targets)} test target(s) selected`
                    }
                  />
                </div>
                <TestChanges id={regressionId} />
              </>
            )}
          </div>
        );
      }}
    </Query>
  );
}
