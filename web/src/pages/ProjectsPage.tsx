import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type SyntheticEvent } from "react";
import { Link, useNavigate } from "react-router";

import { api, unwrap } from "../api/client";
import { useProjects } from "../api/queries";
import { Pager } from "../components/Pager";
import { Card, Empty, ErrorView, Notice, Query, Status } from "../components/ui";
import { formatDateTime, shortSha } from "../lib/format";

const LIMIT = 20;

function CreateProject() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [location, setLocation] = useState("");
  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/projects", {
          body: { name, source_type: "local", source_location: location },
        }),
      ),
    onSuccess: async (project) => {
      await client.invalidateQueries({ queryKey: ["projects"] });
      await navigate(`/projects/${project.id}`);
    },
  });
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    create.mutate();
  };
  return (
    <Card
      title="Register a local repository"
      subtitle="A directory inside the repositories root (TESTMIND_REPOS_ROOT)"
    >
      <form className="stack" onSubmit={submit}>
        <div className="form-row">
          <label className="field">
            Name
            <input
              required
              value={name}
              onChange={(e) => {
                setName(e.target.value);
              }}
              placeholder="discount-service"
            />
          </label>
          <label className="field">
            Directory
            <input
              required
              value={location}
              onChange={(e) => {
                setLocation(e.target.value);
              }}
              placeholder="discount_service"
            />
          </label>
          <button className="primary" type="submit" disabled={create.isPending}>
            {create.isPending ? "Registering…" : "Register"}
          </button>
        </div>
        {create.error && <ErrorView error={create.error} />}
      </form>
    </Card>
  );
}

function ImportRepository() {
  const [repository, setRepository] = useState("");
  const [ref, setRef] = useState("HEAD");
  const [installation, setInstallation] = useState("");
  const start = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/github/imports", {
          body: { repository, ref, installation_id: Number(installation) },
        }),
      ),
  });
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    start.mutate();
  };
  return (
    <Card
      title="Import from GitHub"
      subtitle="Needs the GitHub App (see docs/github.md); the project appears when the import finishes"
    >
      <form className="stack" onSubmit={submit}>
        <div className="form-row">
          <label className="field">
            Repository
            <input
              required
              pattern="[A-Za-z0-9][A-Za-z0-9\-]*/[A-Za-z0-9._\-]+"
              value={repository}
              onChange={(e) => {
                setRepository(e.target.value);
              }}
              placeholder="owner/name"
            />
          </label>
          <label className="field">
            Ref
            <input
              required
              value={ref}
              onChange={(e) => {
                setRef(e.target.value);
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
          <button type="submit" disabled={start.isPending}>
            {start.isPending ? "Queueing…" : "Import"}
          </button>
        </div>
        {start.error && <ErrorView error={start.error} />}
        {start.data && (
          <Notice title="Import queued">
            Import <span className="mono">{start.data.id}</span> is{" "}
            <Status kind="job" value={start.data.status} />; the project will be listed once the
            worker has downloaded the repository.
          </Notice>
        )}
      </form>
    </Card>
  );
}

export function ProjectsPage() {
  const [offset, setOffset] = useState(0);
  const query = useProjects(offset, LIMIT);
  return (
    <>
      <header className="page-header">
        <div>
          <h1>Projects</h1>
          <p>Repositories TestMind can analyse.</p>
        </div>
      </header>
      <div className="stack">
        <Card title="All projects">
          <Query query={query}>
            {(page) =>
              page.items.length === 0 ? (
                <Empty>No projects yet.</Empty>
              ) : (
                <>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Name</th>
                          <th>Source</th>
                          <th>Location</th>
                          <th>Created</th>
                        </tr>
                      </thead>
                      <tbody>
                        {page.items.map((p) => (
                          <tr key={p.id}>
                            <td>
                              <Link to={`/projects/${p.id}`}>{p.name}</Link>
                            </td>
                            <td>{p.source_type}</td>
                            <td className="mono truncate">
                              {p.remote_repository
                                ? `${p.remote_repository} @ ${shortSha(p.remote_commit)}`
                                : p.source_location}
                            </td>
                            <td>{formatDateTime(p.created_at)}</td>
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
        <div className="grid grid-2">
          <CreateProject />
          <ImportRepository />
        </div>
      </div>
    </>
  );
}
