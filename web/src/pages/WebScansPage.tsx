import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type SyntheticEvent } from "react";
import { Link, useNavigate } from "react-router";

import { api, unwrap } from "../api/client";
import { useWebScans } from "../api/queries";
import { Pager } from "../components/Pager";
import { Card, Empty, ErrorView, Query, Status } from "../components/ui";
import { formatDateTime } from "../lib/format";

const LIMIT = 20;

function StartScan() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [url, setUrl] = useState("");
  const [maxPages, setMaxPages] = useState(100);
  const [maxDepth, setMaxDepth] = useState(5);
  const [interact, setInteract] = useState(false);
  const [security, setSecurity] = useState(false);
  const [active, setActive] = useState(false);
  const [screenshots, setScreenshots] = useState(false);
  const start = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/web-scans", {
          body: {
            target_url: url,
            max_pages: maxPages,
            max_depth: maxDepth,
            interact,
            security,
            active,
            screenshots,
          },
        }),
      ),
    onSuccess: async (scan) => {
      await client.invalidateQueries({ queryKey: ["web-scans"] });
      await navigate(`/web-scans/${scan.id}`);
    },
  });
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    start.mutate();
  };
  return (
    <Card
      title="Scan a website"
      subtitle="A public http(s) address; internal and loopback hosts are refused"
    >
      <form className="stack" onSubmit={submit}>
        <div className="form-row">
          <label className="field" style={{ flex: "2 1 240px" }}>
            URL
            <input
              required
              type="url"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
              }}
              placeholder="https://example.com/"
            />
          </label>
          <label className="field">
            Max pages
            <input
              type="number"
              min={1}
              max={500}
              value={maxPages}
              onChange={(e) => {
                setMaxPages(Number(e.target.value));
              }}
            />
          </label>
          <label className="field">
            Max depth
            <input
              type="number"
              min={0}
              max={10}
              value={maxDepth}
              onChange={(e) => {
                setMaxDepth(Number(e.target.value));
              }}
            />
          </label>
        </div>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={interact}
            onChange={(e) => {
              setInteract(e.target.checked);
            }}
          />
          Exercise controls (click safe buttons, submit forms) to find dead buttons
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={security}
            onChange={(e) => {
              setSecurity(e.target.checked);
            }}
          />
          Passive security checks (response headers, cookies, mixed content) — no attacks
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={active}
            onChange={(e) => {
              setActive(e.target.checked);
            }}
          />
          Active checks (crafted requests) — only on a host you have verified as an authorized
          target
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={screenshots}
            onChange={(e) => {
              setScreenshots(e.target.checked);
            }}
          />
          Capture a screenshot of each page (shown with its issues)
        </label>
        <div className="form-row">
          <button className="primary" type="submit" disabled={start.isPending}>
            {start.isPending ? "Queueing…" : "Start scan"}
          </button>
        </div>
        {start.error && <ErrorView error={start.error} />}
      </form>
    </Card>
  );
}

export function WebScansPage() {
  const [offset, setOffset] = useState(0);
  const query = useWebScans(offset, LIMIT);
  return (
    <>
      <header className="page-header">
        <div>
          <h1>Websites</h1>
          <p>Crawl a live site and find the problems a visitor would hit.</p>
        </div>
      </header>
      <div className="stack">
        <StartScan />
        <Card title="Scans">
          <Query query={query}>
            {(page) =>
              page.items.length === 0 ? (
                <Empty>No scans yet.</Empty>
              ) : (
                <>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Target</th>
                          <th>Status</th>
                          <th>Issues</th>
                          <th>Started</th>
                        </tr>
                      </thead>
                      <tbody>
                        {page.items.map((s) => (
                          <tr key={s.id}>
                            <td className="truncate">
                              <Link to={`/web-scans/${s.id}`}>{s.target_url}</Link>
                            </td>
                            <td>
                              <Status kind="job" value={s.status} />
                            </td>
                            <td className="num">{s.result ? s.result.issues.length : "—"}</td>
                            <td>{formatDateTime(s.created_at)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <Pager offset={offset} limit={LIMIT} total={page.total} onChange={setOffset} />
                </>
              )
            }
          </Query>
        </Card>
      </div>
    </>
  );
}
