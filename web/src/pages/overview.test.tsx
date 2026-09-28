import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  PROJECT_ID,
  platformOverview,
  project,
  projectOverview,
  REGRESSION_ID,
  regressionRead,
  run,
  RUN_ID,
  page,
} from "../test/fixtures";
import { apiError, mockApi, renderAt } from "../test/harness";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("Overview", () => {
  it("shows tiles, projects with their latest results, and recent jobs", async () => {
    mockApi({ "GET /api/v1/overview": { body: platformOverview() } });
    renderAt("/");

    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    expect(screen.getAllByText("4/4 passed")).toHaveLength(2); // project table and recent list
    expect(screen.getByText("60.0 %")).toBeInTheDocument(); // mutation score is 0–100
    expect(screen.getByText("never analysed")).toBeInTheDocument();
    expect(screen.getByText("1 active")).toBeInTheDocument();
    expect(screen.getByText("11/14 passed, 3 failed")).toBeInTheDocument();
    expect(screen.getByText("acme/shop#7")).toHaveAttribute(
      "href",
      "/github/pull-analyses/66666666-6666-4666-8666-666666666666",
    );
    // Test executions tile: every status summed; failed jobs counted.
    const tile = screen.getByText("Test executions").parentElement;
    expect(tile).toHaveTextContent("4");
    expect(tile).toHaveTextContent("1 failed jobs");
    expect(screen.getByRole("meter", { name: "Coverage of shipping_rates" })).toHaveAttribute(
      "aria-valuenow",
      "88.24",
    );
    expect(screen.getByText("queue is idle")).toBeInTheDocument();
  });

  it("guides an empty platform", async () => {
    mockApi({
      "GET /api/v1/overview": {
        body: platformOverview({
          projects: 0,
          active_jobs: 2,
          jobs: [],
          project_health: [],
          recent_executions: [],
          recent_pull_analyses: [],
        }),
      },
    });
    renderAt("/");
    expect(await screen.findByText(/No projects yet/)).toBeInTheDocument();
    expect(screen.getByText("No test executions yet.")).toBeInTheDocument();
    expect(screen.getByText(/the GitHub App is optional/)).toBeInTheDocument();
    expect(screen.getByText("refreshing every 2 s")).toBeInTheDocument();
  });

  it("shows the API's error when the API fails", async () => {
    mockApi({ "GET /api/v1/overview": apiError(500, "internal_error", "Internal server error") });
    renderAt("/");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Error 500 internal errorInternal server error",
    );
  });
});

describe("Projects", () => {
  it("lists projects and registers a new one", async () => {
    const api = mockApi({
      "GET /api/v1/projects": {
        body: page(
          [
            project(),
            project({
              id: "p2",
              name: "shop",
              source_type: "github",
              remote_repository: "acme/shop",
              remote_commit: "c".repeat(40),
            }),
          ],
          45,
        ),
      },
      "POST /api/v1/projects": { status: 201, body: project({ id: "new-id", name: "blog" }) },
      "GET /api/v1/projects/new-id/overview": {
        body: projectOverview({ project: project({ id: "new-id", name: "blog" }) }),
      },
      "GET /api/v1/projects/new-id/runs": { body: page([]) },
    });
    renderAt("/projects");

    expect(await screen.findByText("acme/shop @ ccccccc")).toBeInTheDocument();
    expect(screen.getByText("1–20 of 45")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => {
      expect(api.calls.some((c) => c.search.includes("offset=20"))).toBe(true);
    });

    await userEvent.type(screen.getByLabelText("Name"), "blog");
    await userEvent.type(screen.getByLabelText("Directory"), "django_blog");
    await userEvent.click(screen.getByRole("button", { name: "Register" }));

    expect(await screen.findByRole("heading", { name: "blog" })).toBeInTheDocument();
    expect(api.calls.find((c) => c.method === "POST")?.body).toEqual({
      name: "blog",
      source_type: "local",
      source_location: "django_blog",
    });
  });

  it("shows why a registration or an import was refused", async () => {
    mockApi({
      "GET /api/v1/projects": { body: page([]) },
      "POST /api/v1/projects": apiError(
        422,
        "invalid_source",
        "'../etc' is outside the repositories root",
      ),
      "POST /api/v1/github/imports": apiError(
        503,
        "github_not_configured",
        "The GitHub App is not configured",
      ),
    });
    renderAt("/projects");
    expect(await screen.findByText("No projects yet.")).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Name"), "x");
    await userEvent.type(screen.getByLabelText("Directory"), "../etc");
    await userEvent.click(screen.getByRole("button", { name: "Register" }));
    expect(await screen.findByText("'../etc' is outside the repositories root")).toBeVisible();

    await userEvent.type(screen.getByLabelText("Repository"), "acme/shop");
    await userEvent.type(screen.getByLabelText("Installation id"), "5");
    await userEvent.click(screen.getByRole("button", { name: "Import" }));
    expect(await screen.findByText("The GitHub App is not configured")).toBeVisible();
  });

  it("confirms a queued import", async () => {
    const api = mockApi({
      "GET /api/v1/projects": { body: page([]) },
      "POST /api/v1/github/imports": {
        status: 202,
        body: {
          id: "import-1",
          repository: "acme/shop",
          ref: "main",
          installation_id: 5,
          status: "queued",
          commit_sha: null,
          project_id: null,
          run_id: null,
          skipped: [],
          error_message: null,
          created_at: "2026-09-27T10:00:00Z",
          started_at: null,
          finished_at: null,
        },
      },
    });
    renderAt("/projects");
    await screen.findByText("No projects yet.");
    await userEvent.type(screen.getByLabelText("Repository"), "acme/shop");
    const ref = screen.getByLabelText("Ref");
    await userEvent.clear(ref);
    await userEvent.type(ref, "main");
    await userEvent.type(screen.getByLabelText("Installation id"), "5");
    await userEvent.click(screen.getByRole("button", { name: "Import" }));
    expect(await screen.findByText("Import queued")).toBeInTheDocument();
    expect(api.calls.at(-1)?.body).toEqual({
      repository: "acme/shop",
      ref: "main",
      installation_id: 5,
    });
  });
});

describe("Project", () => {
  const routes = () => ({
    [`GET /api/v1/projects/${PROJECT_ID}/overview`]: { body: projectOverview() },
    [`GET /api/v1/projects/${PROJECT_ID}/runs`]: {
      body: page([run(), run({ id: "r2", status: "failed", error_message: "Not a directory" })]),
    },
  });

  it("shows trends, runs and regressions", async () => {
    mockApi(routes());
    renderAt(`/projects/${PROJECT_ID}`);

    expect(await screen.findByRole("heading", { name: "shipping_rates" })).toBeInTheDocument();
    expect(
      screen.getByText("The last 3 execution(s), oldest first; select one to open it"),
    ).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Coverage per execution" })).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: "Mutation score per mutation run" }),
    ).toBeInTheDocument();
    expect(screen.getByText("60.0 %")).toBeInTheDocument();
    expect(screen.getByText("main~1 → main")).toBeInTheDocument();
    expect(screen.getByText("regressions")).toBeInTheDocument();
    expect(await screen.findByText("Not a directory")).toBeInTheDocument();
    expect(screen.getByText("1 succeeded, 0 failed, 0 active")).toBeInTheDocument();
  });

  it("opens an execution from its column", async () => {
    mockApi({
      ...routes(),
      "GET /api/v1/test-executions/e1": apiError(404, "not_found", "Execution 'e1' not found"),
    });
    const { router } = renderAt(`/projects/${PROJECT_ID}`);
    const columns = await screen.findAllByRole("link", { name: /4 passed, 0 failed, 0 skipped/ });
    expect(columns).toHaveLength(2); // the queued execution has no results yet
    fireEvent.click(columns[0] as Element);
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/executions/e1");
    });
    expect(await screen.findByText("Execution 'e1' not found")).toBeInTheDocument();
  });

  it("shows empty trends for a new project", async () => {
    mockApi({
      [`GET /api/v1/projects/${PROJECT_ID}/overview`]: {
        body: projectOverview({
          executions: [],
          mutation_runs: [],
          regressions: [],
          latest_run: null,
          latest_succeeded_run: null,
          project: project({
            source_type: "github",
            remote_repository: "acme/shop",
            remote_ref: "main",
            remote_commit: "d".repeat(40),
          }),
          pull_analyses: [
            {
              id: "pa",
              repository: "acme/shop",
              pull_number: 3,
              status: "succeeded",
              conclusion: "success",
              head_sha: "e".repeat(40),
              created_at: "2026-09-27T10:00:00Z",
            },
            {
              id: "pb",
              repository: "acme/shop",
              pull_number: 4,
              status: "queued",
              conclusion: null,
              head_sha: null,
              created_at: "2026-09-27T10:00:00Z",
            },
          ],
        }),
      },
      [`GET /api/v1/projects/${PROJECT_ID}/runs`]: { body: page([]) },
    });
    renderAt(`/projects/${PROJECT_ID}`);
    expect(await screen.findByText(/No test executions yet/)).toBeInTheDocument();
    expect(screen.getByText("No coverage measured yet.")).toBeInTheDocument();
    expect(screen.getByText("No finished mutation run yet.")).toBeInTheDocument();
    expect(screen.getByText("No regression analysis yet.")).toBeInTheDocument();
    expect(screen.getByText("acme/shop @ main (ddddddd)")).toBeInTheDocument();
    expect(await screen.findByText("Not analysed yet.")).toBeInTheDocument();
    // Git comparisons are for local repositories; imported snapshots have no history.
    expect(screen.queryByText("Compare two commits")).toBeNull();
    expect(screen.getByText("#3 · eeeeeee")).toBeInTheDocument();
  });

  it("starts a run and a regression analysis", async () => {
    const api = mockApi({
      ...routes(),
      [`POST /api/v1/projects/${PROJECT_ID}/runs`]: {
        status: 202,
        body: run({ id: RUN_ID, status: "queued" }),
      },
      [`POST /api/v1/projects/${PROJECT_ID}/regressions`]: {
        status: 202,
        body: regressionRead({ status: "queued", verdict: null, summary: null }),
      },
      [`GET /api/v1/regressions/${REGRESSION_ID}`]: {
        body: regressionRead({ status: "queued", verdict: null, summary: null }),
      },
    });
    const { router } = renderAt(`/projects/${PROJECT_ID}`);
    await screen.findByRole("heading", { name: "shipping_rates" });

    const form = screen.getByText("Compare two commits").closest("section");
    if (!form) throw new Error("no regression form");
    await userEvent.selectOptions(within(form).getByLabelText("Tests"), "full");
    await userEvent.click(within(form).getByRole("button", { name: "Compare" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe(`/regressions/${REGRESSION_ID}`);
    });
    expect(api.calls.find((c) => c.path.endsWith("/regressions"))?.body).toEqual({
      base: "HEAD~1",
      head: "HEAD",
      mode: "full",
    });
    expect(await screen.findByText(/The analysis is queued/)).toBeInTheDocument();

    await router.navigate(`/projects/${PROJECT_ID}`);
    await userEvent.click(await screen.findByRole("button", { name: "Analyse now" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe(`/runs/${RUN_ID}`);
    });
  });

  it("shows why a project is missing", async () => {
    mockApi({
      [`GET /api/v1/projects/${PROJECT_ID}/overview`]: apiError(
        404,
        "not_found",
        `Project '${PROJECT_ID}' not found`,
      ),
    });
    renderAt(`/projects/${PROJECT_ID}`);
    expect(await screen.findByText("Not available")).toBeInTheDocument();
  });
});

describe("Layout", () => {
  it("navigates, keeps the theme choice, and has a not-found page", async () => {
    mockApi({ "GET /api/v1/github/pull-analyses": { body: page([]) } });
    const { router } = renderAt("/nowhere");
    expect(await screen.findByText("Page not found")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Theme"), "dark");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(localStorage.getItem("testmind.theme")).toBe("dark");
    await userEvent.selectOptions(screen.getByLabelText("Theme"), "system");
    expect(document.documentElement).not.toHaveAttribute("data-theme");

    await userEvent.click(screen.getByRole("link", { name: "Pull requests" }));
    expect(router.state.location.pathname).toBe("/github");
    expect(screen.getByRole("link", { name: "Pull requests" })).toHaveClass("active");
  });

  it("starts with the stored theme", async () => {
    localStorage.setItem("testmind.theme", "light");
    mockApi({ "GET /api/v1/overview": { body: platformOverview() } });
    renderAt("/");
    await screen.findByRole("heading", { name: "Overview" });
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
  });
});
