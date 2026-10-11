import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Schemas } from "../api/client";
import { captured } from "../test/captured";
import {
  EXECUTION_ID,
  executionRead,
  MUTATION_ID,
  mutationRead,
  page,
  project,
  PROJECT_ID,
  REGRESSION_ID,
  regressionRead,
  run,
  RUN_ID,
} from "../test/fixtures";
import { apiError, mockApi, renderAt, type Routes } from "../test/harness";

afterEach(() => {
  vi.unstubAllGlobals();
});

const RUN = `/api/v1/runs/${RUN_ID}`;
const EXECUTION = `/api/v1/test-executions/${EXECUTION_ID}`;

function runRoutes(extra: Routes = {}): Routes {
  return {
    [`GET ${RUN}`]: { body: run() },
    [`GET /api/v1/projects/${PROJECT_ID}`]: { body: project({ name: "buggy_api" }) },
    [`GET ${RUN}/analysis`]: { body: captured.repositorySummary },
    [`GET ${RUN}/code`]: { body: captured.codeSummary },
    ...extra,
  };
}

describe("Run", () => {
  it("summarises the repository and its code", async () => {
    mockApi(runRoutes());
    renderAt(`/runs/${RUN_ID}`);
    expect(await screen.findByRole("heading", { name: "Analysis run" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "buggy_api" })).toHaveAttribute(
      "href",
      `/projects/${PROJECT_ID}`,
    );
    expect(await screen.findByText("Lines of code")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Findings by severity" })).toBeInTheDocument();
    expect(
      await screen.findByRole("list", { name: "Languages by share of lines" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Primary language").nextElementSibling).toHaveTextContent("Python");
    expect(screen.getByText("Frameworks").nextElementSibling).toHaveTextContent("Flask");
  });

  it("filters findings and lists functions", async () => {
    const api = mockApi(
      runRoutes({
        [`GET ${RUN}/code/findings`]: (_request, url) => ({
          body:
            url.searchParams.get("min_severity") === "critical"
              ? page(captured.findings.items.filter((f) => f.severity === "critical"))
              : captured.findings,
        }),
        [`GET ${RUN}/code/functions`]: { body: captured.functions },
      }),
    );
    const { router } = renderAt(`/runs/${RUN_ID}?tab=findings`);
    const rows = await screen.findAllByRole("row");
    expect(rows).toHaveLength(captured.findings.items.length + 1);
    await userEvent.selectOptions(screen.getByLabelText("Minimum severity"), "critical");
    await waitFor(() => {
      expect(screen.getAllByRole("row")).toHaveLength(2);
    });
    expect(api.calls.at(-1)?.search).toContain("min_severity=critical");

    await userEvent.click(screen.getByRole("tab", { name: "Functions" }));
    expect(router.state.location.search).toBe("?tab=functions");
    const top = captured.functions.items[0];
    if (!top) throw new Error("fixture has no functions");
    expect(await screen.findByText(top.qualname)).toBeInTheDocument();
    expect(api.calls.at(-1)?.search).toContain("sort=complexity");
  });

  it("lists HTTP endpoints, and moves between tabs with the keyboard", async () => {
    mockApi(runRoutes({ [`GET ${RUN}/api`]: { body: captured.apiSurface } }));
    const { router } = renderAt(`/runs/${RUN_ID}?tab=api`);
    expect(await screen.findAllByText("/books/{book_id}", { selector: "td" })).toHaveLength(3);
    const tab = screen.getByRole("tab", { name: "API" });
    tab.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(router.state.location.search).toBe("?tab=tests");
    await userEvent.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(router.state.location.search).toBe("?tab=functions");
  });

  it("starts test executions and mutation runs", async () => {
    const api = mockApi(
      runRoutes({
        [`GET ${RUN}/test-executions`]: { body: page([executionRead()]) },
        [`GET ${RUN}/mutation-runs`]: { body: page([mutationRead()]) },
        [`POST ${RUN}/test-executions`]: { status: 202, body: executionRead({ status: "queued" }) },
        [`POST ${RUN}/mutation-runs`]: apiError(409, "conflict", "The run has not succeeded"),
        [`GET ${EXECUTION}`]: { body: executionRead({ status: "queued", summary: null }) },
        [`GET ${RUN}`]: { body: run() },
      }),
    );
    const { router } = renderAt(`/runs/${RUN_ID}?tab=tests`);
    expect(await screen.findByText("11/14")).toBeInTheDocument();
    expect(screen.getByText("60.0 %")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Run mutation testing" }));
    expect(await screen.findByText("The run has not succeeded")).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("API probes"));
    await userEvent.click(screen.getByRole("button", { name: "Run tests" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe(`/executions/${EXECUTION_ID}`);
    });
    expect(
      api.calls.find((c) => c.path === `${RUN}/test-executions` && c.method === "POST")?.body,
    ).toEqual({ api_probes: true });
    expect(await screen.findByText(/The execution is queued/)).toBeInTheDocument();
  });

  it("explains a run that is still going, failed, or can be cancelled", async () => {
    const api = mockApi({
      [`GET ${RUN}`]: { body: run({ status: "queued" }) },
      [`GET /api/v1/projects/${PROJECT_ID}`]: { body: project() },
      [`POST ${RUN}/cancel`]: apiError(409, "invalid_state_transition", "The run already started"),
    });
    renderAt(`/runs/${RUN_ID}`);
    expect(await screen.findByText("The run is queued")).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("The run already started")).toBeInTheDocument();
    expect(api.calls.some((c) => c.path.endsWith("/cancel"))).toBe(true);
  });

  it("shows a failed run's error", async () => {
    mockApi({
      [`GET ${RUN}`]: { body: run({ status: "failed", error_message: "Source missing" }) },
      [`GET /api/v1/projects/${PROJECT_ID}`]: apiError(404, "not_found", "gone"),
    });
    renderAt(`/runs/${RUN_ID}`);
    expect(await screen.findByText("Source missing")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Project" })).toBeInTheDocument();
  });
});

function executionRoutes(extra: Routes = {}): Routes {
  return {
    [`GET ${EXECUTION}`]: { body: executionRead() },
    [`GET ${RUN}`]: { body: run() },
    [`GET /api/v1/projects/${PROJECT_ID}`]: { body: project({ name: "buggy_api" }) },
    ...extra,
  };
}

describe("Execution", () => {
  it("lists tests with their failures and filters by outcome", async () => {
    const api = mockApi(
      executionRoutes({
        [`GET ${EXECUTION}/tests`]: (_request, url) => ({
          body:
            url.searchParams.get("outcome") === "failed"
              ? page(captured.tests.items.filter((t) => t.outcome === "failed"))
              : captured.tests,
        }),
      }),
    );
    renderAt(`/executions/${EXECUTION_ID}`);
    expect(await screen.findByRole("link", { name: "buggy_api" })).toBeInTheDocument();
    const summary = screen.getByText("Tests", { selector: ".tile-label" }).parentElement;
    expect(summary).toHaveTextContent("11/14");
    expect(summary).toHaveTextContent("3 failed, 0 skipped");
    expect(await screen.findAllByText("generated")).not.toHaveLength(0);

    await userEvent.selectOptions(screen.getByLabelText("Outcome"), "failed");
    await waitFor(() => {
      expect(screen.getAllByRole("row")).toHaveLength(
        captured.tests.items.filter((t) => t.outcome === "failed").length + 1,
      );
    });
    expect(api.calls.at(-1)?.search).toContain("outcome=failed");
  });

  it("shows coverage totals, gaps and files", async () => {
    mockApi(
      executionRoutes({
        [`GET ${EXECUTION}/coverage`]: { body: captured.coverage },
        [`GET ${EXECUTION}/coverage/files`]: { body: captured.coverageFiles },
      }),
    );
    renderAt(`/executions/${EXECUTION_ID}?tab=coverage`);
    expect(await screen.findByText("Riskiest gaps")).toBeInTheDocument();
    const file = captured.coverageFiles.items[0];
    if (!file) throw new Error("no files");
    expect(await screen.findByText(file.path)).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: `Coverage of ${file.path}` })).toBeInTheDocument();
  });

  it("groups failures by cause", async () => {
    mockApi(
      executionRoutes({
        [`GET ${EXECUTION}/failures`]: { body: captured.failures },
        [`GET ${EXECUTION}/failures/groups`]: { body: captured.failureGroups },
      }),
    );
    renderAt(`/executions/${EXECUTION_ID}?tab=failures`);
    expect(
      await screen.findByRole("list", { name: "Failed tests by category" }),
    ).toBeInTheDocument();
    const group = captured.failureGroups.items[0];
    if (!group) throw new Error("no groups");
    expect(await screen.findAllByText(group.tests[0]?.nodeid ?? "")).not.toHaveLength(0);
  });

  it("ranks the suspect functions of a failure", async () => {
    const [first, ...rest] = captured.failureGroups.items;
    if (!first) throw new Error("no groups");
    const localization: Schemas["Localization"] = {
      suspects: [
        {
          path: "shop/pricing.py",
          qualname: "apply_discount",
          line: 1,
          rank: 1,
          score: 0.8165,
          failed: 2,
          passed: 1,
          in_traceback: false,
        },
        {
          path: "shop/pricing.py",
          qualname: "total",
          line: 9,
          rank: 2,
          score: 0.5,
          failed: 1,
          passed: 1,
          in_traceback: true,
        },
      ],
      confidence: "high",
      failing_tests: 2,
      passing_tests: 3,
      notes: [],
    };
    mockApi(
      executionRoutes({
        [`GET ${EXECUTION}/failures`]: { body: captured.failures },
        [`GET ${EXECUTION}/failures/groups`]: {
          body: { ...captured.failureGroups, items: [{ ...first, localization }, ...rest] },
        },
      }),
    );
    renderAt(`/executions/${EXECUTION_ID}?tab=failures`);
    const section = await screen.findByRole("region", { name: "Suspect functions" });
    expect(within(section).getByText("high confidence")).toBeInTheDocument();
    expect(within(section).getByText("shop/pricing.py:1 apply_discount")).toBeInTheDocument();
    expect(within(section).getByText("0.82")).toBeInTheDocument();
    expect(within(section).getByText("in traceback")).toBeInTheDocument();
    expect(within(section).getByText("2 / 1")).toBeInTheDocument();
  });

  it("says when the failing tests executed no production function", async () => {
    const [first] = captured.failureGroups.items;
    if (!first) throw new Error("no groups");
    const localization: Schemas["Localization"] = {
      suspects: [],
      confidence: "none",
      failing_tests: 0,
      passing_tests: 0,
      notes: [],
    };
    mockApi(
      executionRoutes({
        [`GET ${EXECUTION}/failures`]: { body: captured.failures },
        [`GET ${EXECUTION}/failures/groups`]: {
          body: { ...captured.failureGroups, items: [{ ...first, localization }] },
        },
      }),
    );
    renderAt(`/executions/${EXECUTION_ID}?tab=failures`);
    expect(
      await screen.findByText("The failing tests executed no production function."),
    ).toBeInTheDocument();
  });

  it("shows API coverage with probes", async () => {
    mockApi(
      executionRoutes({
        [`GET ${EXECUTION}/api-coverage`]: { body: captured.apiCoverage },
        [`GET ${EXECUTION}/api-coverage/endpoints`]: { body: captured.apiEndpoints },
      }),
    );
    renderAt(`/executions/${EXECUTION_ID}?tab=api`);
    expect(await screen.findByRole("list", { name: "Probe verdicts" })).toBeInTheDocument();
    expect(await screen.findByText("1 server error(s)")).toBeInTheDocument();
    expect(screen.getAllByText("untested")).not.toHaveLength(0);
  });

  it("shows the logs", async () => {
    mockApi(
      executionRoutes({
        [`GET ${EXECUTION}/logs`]: {
          body: { ...captured.logs, install_log: "Successfully installed fastapi" },
        },
      }),
    );
    renderAt(`/executions/${EXECUTION_ID}?tab=logs`);
    expect(await screen.findByText("pytest output")).toBeInTheDocument();
    expect(screen.getByText("Successfully installed fastapi")).toBeInTheDocument();
  });

  it("explains an execution without results", async () => {
    mockApi(
      executionRoutes({
        [`GET ${EXECUTION}`]: {
          body: executionRead({
            status: "failed",
            outcome: null,
            summary: null,
            error_message: "The sandbox is not available",
          }),
        },
      }),
    );
    renderAt(`/executions/${EXECUTION_ID}`);
    expect(await screen.findByText("The sandbox is not available")).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).toBeNull();
  });

  it("says when coverage could not be measured", async () => {
    const base = executionRead();
    mockApi(
      executionRoutes({
        [`GET ${EXECUTION}`]: {
          body: executionRead({
            summary: base.summary
              ? { ...base.summary, coverage_percent: null, coverage_error: "No data collected" }
              : null,
          }),
        },
        [`GET ${EXECUTION}/tests`]: { body: page([]) },
      }),
    );
    renderAt(`/executions/${EXECUTION_ID}`);
    expect(await screen.findByText("No data collected")).toBeInTheDocument();
    expect(await screen.findByText("No tests.")).toBeInTheDocument();
  });
});

describe("Regression", () => {
  it("summarises the comparison and filters tests by change", async () => {
    const api = mockApi({
      [`GET /api/v1/regressions/${REGRESSION_ID}`]: { body: regressionRead() },
      [`GET /api/v1/regressions/${REGRESSION_ID}/tests`]: (_request, url) => ({
        body:
          url.searchParams.get("change") === "regressed"
            ? page([
                {
                  nodeid: "tests/test_pricing.py::test_discount",
                  change: "regressed",
                  base: "passed",
                  head: "failed",
                  selected: true,
                },
              ])
            : page([]),
      }),
    });
    renderAt(`/regressions/${REGRESSION_ID}`);
    expect(await screen.findByText("tests/test_pricing.py::test_discount")).toBeInTheDocument();
    const regressed = screen.getByText("Regressed tests").parentElement;
    expect(regressed).toHaveTextContent("2");
    expect(screen.getByText("aaaaaaa..bbbbbbb")).toBeInTheDocument();
    expect(screen.getByText("3 test target(s) selected")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Change"), "");
    expect(await screen.findByText("No test with this change.")).toBeInTheDocument();
    expect(api.calls.at(-1)?.search).not.toContain("change=");
  });

  it("explains a regression analysis that is still running or failed", async () => {
    mockApi({
      [`GET /api/v1/regressions/${REGRESSION_ID}`]: {
        body: regressionRead({
          status: "failed",
          verdict: null,
          summary: null,
          error_message: "Unknown revision 'v9'",
        }),
      },
    });
    renderAt(`/regressions/${REGRESSION_ID}`);
    expect(await screen.findByText("Unknown revision 'v9'")).toBeInTheDocument();
  });

  it("shows an analysis where every test was selected", async () => {
    const base = regressionRead();
    mockApi({
      [`GET /api/v1/regressions/${REGRESSION_ID}`]: {
        body: regressionRead({
          summary: base.summary
            ? { ...base.summary, everything_selected: true, missed_by_selection: null }
            : null,
        }),
      },
      [`GET /api/v1/regressions/${REGRESSION_ID}/tests`]: { body: page([]) },
    });
    renderAt(`/regressions/${REGRESSION_ID}`);
    expect(await screen.findByText("every test was selected")).toBeInTheDocument();
  });
});

describe("Mutation run", () => {
  it("shows the score, the survivors and the weakest functions", async () => {
    const api = mockApi({
      [`GET /api/v1/mutation-runs/${MUTATION_ID}`]: { body: mutationRead() },
      [`GET /api/v1/mutation-runs/${MUTATION_ID}/mutants`]: (_request, url) => ({
        body: url.searchParams.get("status") === "survived" ? captured.mutants : page([]),
      }),
      [`GET /api/v1/mutation-runs/${MUTATION_ID}/functions`]: {
        body: captured.mutationFunctions,
      },
    });
    renderAt(`/mutation-runs/${MUTATION_ID}`);
    const score = (await screen.findByText("Mutation score")).parentElement;
    expect(score).toHaveTextContent("60.0 %");
    expect(screen.getByText("2 mutants did not compile and were skipped")).toBeInTheDocument();
    expect(await screen.findAllByText("survived", { selector: ".badge" })).toHaveLength(
      captured.mutants.items.length,
    );
    const weakest = await screen.findByRole("list", {
      name: "Mutation score of the weakest functions",
    });
    expect(within(weakest).getByText("50 %")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Status"), "killed");
    expect(await screen.findByText("No mutant with this status.")).toBeInTheDocument();
    expect(api.calls.at(-1)?.search).toContain("status=killed");
  });

  it("explains a mutation run that is running", async () => {
    mockApi({
      [`GET /api/v1/mutation-runs/${MUTATION_ID}`]: {
        body: mutationRead({ status: "running", summary: null, score: null }),
      },
    });
    renderAt(`/mutation-runs/${MUTATION_ID}`);
    expect(await screen.findByText("Mutation testing is running")).toBeInTheDocument();
  });

  it("shows why a mutation run failed", async () => {
    mockApi({
      [`GET /api/v1/mutation-runs/${MUTATION_ID}`]: {
        body: mutationRead({ status: "failed", summary: null, error_message: "Baseline failed" }),
      },
    });
    renderAt(`/mutation-runs/${MUTATION_ID}`);
    expect(await screen.findByText("Baseline failed")).toBeInTheDocument();
  });
});
