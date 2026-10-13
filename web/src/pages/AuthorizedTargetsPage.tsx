import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type SyntheticEvent } from "react";

import { api, unwrap, type Schemas } from "../api/client";
import { useAuthorizedTargets } from "../api/queries";
import { Card, Empty, ErrorView, Query } from "../components/ui";
import { formatDateTime } from "../lib/format";

function RegisterTarget() {
  const client = useQueryClient();
  const [host, setHost] = useState("");
  const register = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/authorized-targets", { body: { host } })),
    onSuccess: async () => {
      setHost("");
      await client.invalidateQueries({ queryKey: ["authorized-targets"] });
    },
  });
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    register.mutate();
  };
  return (
    <Card
      title="Register a host you own"
      subtitle="You will be given a token to publish on the site, then verify ownership"
    >
      <form className="stack" onSubmit={submit}>
        <div className="form-row">
          <label className="field" style={{ flex: "2 1 240px" }}>
            Host
            <input
              required
              value={host}
              onChange={(e) => {
                setHost(e.target.value);
              }}
              placeholder="example.com"
            />
          </label>
          <button className="primary" type="submit" disabled={register.isPending}>
            {register.isPending ? "Registering…" : "Register"}
          </button>
        </div>
        {register.error && <ErrorView error={register.error} />}
      </form>
    </Card>
  );
}

function TargetRow({ target }: { target: Schemas["AuthorizedTargetRead"] }) {
  const client = useQueryClient();
  const verify = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/authorized-targets/{target_id}/verify", {
          params: { path: { target_id: target.id } },
        }),
      ),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["authorized-targets"] });
    },
  });
  return (
    <Card
      title={target.host}
      actions={
        <div className="toolbar">
          {target.verified ? (
            <span className="badge good">verified</span>
          ) : (
            <span className="badge">not verified</span>
          )}
          <button
            type="button"
            disabled={verify.isPending}
            onClick={() => {
              verify.mutate();
            }}
          >
            {verify.isPending ? "Verifying…" : "Verify"}
          </button>
        </div>
      }
    >
      <div className="stack">
        <p className="muted">{target.instructions}</p>
        <pre className="block">{target.token}</pre>
        <p className="muted">
          {target.verified
            ? `Verified ${formatDateTime(target.verified_at)}`
            : (target.last_error ?? "Publish the token, then verify.")}
        </p>
        {verify.error && <ErrorView error={verify.error} />}
      </div>
    </Card>
  );
}

export function AuthorizedTargetsPage() {
  const query = useAuthorizedTargets();
  return (
    <>
      <header className="page-header">
        <div>
          <h1>Authorized targets</h1>
          <p>Hosts you have proven you own — required before a scan may run active checks.</p>
        </div>
      </header>
      <div className="stack">
        <RegisterTarget />
        <Query query={query}>
          {(targets) =>
            targets.length === 0 ? (
              <Empty>No authorized targets yet.</Empty>
            ) : (
              <div className="stack">
                {targets.map((target) => (
                  <TargetRow key={target.id} target={target} />
                ))}
              </div>
            )
          }
        </Query>
      </div>
    </>
  );
}
