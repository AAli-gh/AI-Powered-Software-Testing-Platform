import { Link, useParams } from "react-router";

import type { Schemas } from "../api/client";
import { useWebScan } from "../api/queries";
import { Card, Empty, Facts, Notice, Query, StatTile, Status } from "../components/ui";
import { formatDateTime, formatInteger, words } from "../lib/format";
import { isActive } from "../lib/status";

const SEVERITY_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 };

function Result({ result }: { result: Schemas["SiteScan"] }) {
  const issues = [...result.issues].sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3),
  );
  const high = issues.filter((i) => i.severity === "high").length;
  return (
    <div className="stack">
      <div className="grid grid-tiles">
        <StatTile label="Pages reached" value={formatInteger(result.pages.length)} />
        <StatTile
          label="Issues"
          value={formatInteger(issues.length)}
          detail={`${formatInteger(high)} high severity`}
        />
        <StatTile label="Out of scope" value={formatInteger((result.denied ?? []).length)} />
      </div>

      <Card title="Issues">
        {issues.length === 0 ? (
          <Empty>No issues found.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Severity</th>
                  <th>Kind</th>
                  <th>URL</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {issues.map((issue, index) => (
                  <tr key={`${issue.kind}:${issue.url}:${String(index)}`}>
                    <td>
                      <Status kind="severity" value={issue.severity} />
                    </td>
                    <td>{words(issue.kind)}</td>
                    <td className="mono truncate">{issue.url}</td>
                    <td>
                      {issue.detail}
                      {(issue.evidence ?? []).length > 0 && (
                        <details>
                          <summary className="muted">Evidence</summary>
                          <ul className="plain mono">
                            {(issue.evidence ?? []).map((line) => (
                              <li key={line}>{line}</li>
                            ))}
                          </ul>
                        </details>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Pages crawled">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>URL</th>
                <th>Status</th>
                <th>Depth</th>
                <th>Links</th>
                <th>Buttons</th>
              </tr>
            </thead>
            <tbody>
              {result.pages.map((p) => (
                <tr key={p.url}>
                  <td className="mono truncate">{p.url}</td>
                  <td className="num">{p.status ?? "—"}</td>
                  <td className="num">{p.depth}</td>
                  <td className="num">{p.links}</td>
                  <td className="num">{p.buttons}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {(result.denied ?? []).length > 0 && (
        <Card
          title="Refused as out of scope"
          subtitle="Requests the network policy blocked (other hosts, internal addresses)"
        >
          <ul className="plain mono">
            {(result.denied ?? []).map((d) => (
              <li key={d}>{d}</li>
            ))}
          </ul>
        </Card>
      )}

      {(result.notes ?? []).length > 0 && (
        <Notice title="Notes">
          <ul className="plain">
            {(result.notes ?? []).map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </Notice>
      )}
    </div>
  );
}

export function WebScanPage() {
  const { scanId = "" } = useParams();
  const query = useWebScan(scanId);
  return (
    <Query query={query}>
      {(scan) => (
        <div className="stack">
          <header className="page-header" style={{ marginBottom: 0 }}>
            <div>
              <nav className="breadcrumbs" aria-label="Breadcrumbs">
                <Link to="/web-scans">Websites</Link>
              </nav>
              <h1 className="truncate">{scan.target_url}</h1>
              <p>
                <Status kind="job" value={scan.status} /> · {formatDateTime(scan.created_at)}
                {scan.interact && " · controls exercised"}
              </p>
            </div>
          </header>
          {scan.error_message && (
            <Notice title="The scan failed" error>
              {scan.error_message}
            </Notice>
          )}
          {isActive(scan.status) && (
            <Notice title={`The scan is ${words(scan.status)}`}>
              A real browser is crawling the site; this page refreshes by itself.
            </Notice>
          )}
          <Card title="Scan">
            <Facts
              items={[
                ["Target", <span className="mono">{scan.target_url}</span>],
                ["Page budget", formatInteger(scan.max_pages)],
                ["Max depth", formatInteger(scan.max_depth)],
                ["Controls exercised", scan.interact ? "yes" : "no"],
                ["Security checks", scan.security ? "yes" : "no"],
                ["Active checks", scan.active ? "yes" : "no"],
                ["Finished", formatDateTime(scan.finished_at)],
              ]}
            />
          </Card>
          {scan.result && <Result result={scan.result} />}
        </div>
      )}
    </Query>
  );
}
