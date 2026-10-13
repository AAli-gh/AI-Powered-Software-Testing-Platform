import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { authorizedTarget, TARGET_ID } from "../test/fixtures";
import { apiError, mockApi, renderAt, type Routes } from "../test/harness";

afterEach(() => {
  vi.unstubAllGlobals();
});

const TARGETS = "/api/v1/authorized-targets";

describe("Authorized targets", () => {
  it("registers a host and shows its token and instructions", async () => {
    let registered: ReturnType<typeof authorizedTarget>[] = [];
    const api = mockApi({
      [`GET ${TARGETS}`]: () => ({ body: registered }),
      [`POST ${TARGETS}`]: () => {
        registered = [authorizedTarget()];
        return { status: 201, body: authorizedTarget() };
      },
    });
    renderAt("/authorized-targets");
    expect(await screen.findByText("No authorized targets yet.")).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Host"), "example.com");
    await userEvent.click(screen.getByRole("button", { name: "Register" }));

    expect(await screen.findByText("testmind-site-verification=abc123")).toBeInTheDocument();
    expect(screen.getByText(/Publish a file/)).toBeInTheDocument();
    expect(api.calls.some((c) => c.method === "POST" && c.path === TARGETS)).toBe(true);
  });

  it("verifies a target and reflects the new status", async () => {
    let target = authorizedTarget();
    const routes: Routes = {
      [`GET ${TARGETS}`]: () => ({ body: [target] }),
      [`POST ${TARGETS}/${TARGET_ID}/verify`]: () => {
        target = authorizedTarget({ verified: true, verified_at: "2026-09-27T10:05:00Z" });
        return { body: target };
      },
    };
    mockApi(routes);
    renderAt("/authorized-targets");

    expect(await screen.findByText("not verified")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => {
      expect(screen.getByText("verified")).toBeInTheDocument();
    });
  });

  it("shows the API's error when verification fails to load", async () => {
    mockApi({ [`GET ${TARGETS}`]: apiError(401, "unauthorized", "This TestMind needs a token") });
    renderAt("/authorized-targets");
    expect(await screen.findByText(/needs an API token/)).toBeInTheDocument();
  });
});
