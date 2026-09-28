import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Schemas } from "../api/client";
import { captured } from "../test/captured";
import { page, PULL_ID, pullRead, suggestion } from "../test/fixtures";
import { apiError, mockApi, renderAt } from "../test/harness";

afterEach(() => {
  vi.unstubAllGlobals();
});

const PULL = `/api/v1/github/pull-analyses/${PULL_ID}`;

function report(overrides: Partial<Schemas["PullReport"]> = {}): unknown {
  return {
    repository: "acme/shop",
    number: 7,
    title: "Change pricing",
    url: "https://github.test/acme/shop/pull/7",
    conclusion: "failure",
    regression: {
      tests: [
        { nodeid: "tests/test_pricing.py::test_discount", change: "regressed", base: "passed",
          head: "failed", selected: true },
        { nodeid: "tests/test_labels.py::test_label", change: "unchanged", base: "passed",
          head: "passed", selected: false },
      ],
      base_run: { coverage_percent: 90.5 },
      head_run: { coverage_percent: 85.25 },
    },
    new_findings: captured.findings.items.slice(0, 1),
    untested_changes: [
      { path: "shop/admin.py", qualname: "run", status: "added", base_lines: null,
        head_lines: [1, 2], head_coverage: 0 },
    ],
    suggestions: [
      suggestion("s1", "Regression: test_discount fails"),
      suggestion("s2", "[TM010] eval of input in shop/admin.py"),
    ],
    notes: ["2 file(s) skipped in the archive"],
    ...overrides,
  }; // prettier-ignore
}

describe("Pull request analyses", () => {
  it("lists analyses with their results", async () => {
    mockApi({
      "GET /api/v1/github/pull-analyses": {
        body: page([
          pullRead(),
          pullRead({
            id: "p2",
            pull_number: 8,
            status: "failed",
            conclusion: null,
            summary: null,
            error_message: "GitHub answered 404: Not Found",
          }),
        ]),
      },
    });
    renderAt("/github");
    expect(await screen.findByRole("link", { name: "acme/shop#7" })).toBeInTheDocument();
    expect(screen.getByText("Change pricing")).toBeInTheDocument();
    expect(screen.getByText("GitHub answered 404: Not Found")).toBeInTheDocument();
    expect(screen.getByText("failure")).toBeInTheDocument();
  });

  it("explains an empty list and a refused manual analysis", async () => {
    const api = mockApi({
      "GET /api/v1/github/pull-analyses": { body: page([]) },
      "POST /api/v1/github/pull-analyses": apiError(
        503,
        "github_not_configured",
        "The GitHub App is not configured (TESTMIND_GITHUB_APP_ID and …)",
      ),
    });
    renderAt("/github");
    expect(await screen.findByText(/No pull request has been analysed/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Repository"), "acme/shop");
    await userEvent.type(screen.getByLabelText("Pull request"), "7");
    await userEvent.type(screen.getByLabelText("Installation id"), "3");
    await userEvent.click(screen.getByRole("button", { name: "Analyse" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("github not configured");
    expect(api.calls.at(-1)?.body).toEqual({
      repository: "acme/shop",
      pull_number: 7,
      installation_id: 3,
    });
  });

  it("opens a manually requested analysis", async () => {
    mockApi({
      "GET /api/v1/github/pull-analyses": { body: page([]) },
      "POST /api/v1/github/pull-analyses": {
        status: 202,
        body: pullRead({ status: "queued", conclusion: null, summary: null }),
      },
      [`GET ${PULL}`]: { body: pullRead({ status: "queued", conclusion: null, summary: null }) },
    });
    const { router } = renderAt("/github");
    await screen.findByText(/No pull request has been analysed/);
    await userEvent.type(screen.getByLabelText("Repository"), "acme/shop");
    await userEvent.type(screen.getByLabelText("Pull request"), "7");
    await userEvent.type(screen.getByLabelText("Installation id"), "3");
    await userEvent.click(screen.getByRole("button", { name: "Analyse" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe(`/github/pull-analyses/${PULL_ID}`);
    });
    expect(await screen.findByText("The analysis is queued")).toBeInTheDocument();
  });
});

describe("Pull request analysis", () => {
  it("shows the report and creates the chosen issues once", async () => {
    let issues: Schemas["CreatedIssue"][] = [
      { suggestion: "s2", number: 4, url: "https://github.test/acme/shop/issues/4" },
    ];
    const api = mockApi({
      [`GET ${PULL}`]: () => ({ body: pullRead({ issues }) }),
      [`GET ${PULL}/report`]: { body: report() },
      [`POST ${PULL}/issues`]: () => {
        issues = [...issues, { suggestion: "s1", number: 5, url: "https://github.test/i/5" }];
        return { status: 201, body: issues };
      },
    });
    renderAt(`/github/pull-analyses/${PULL_ID}`);

    expect(await screen.findByText("tests/test_pricing.py::test_discount")).toBeInTheDocument();
    expect(screen.queryByText("tests/test_labels.py::test_label")).toBeNull();
    expect(screen.getByText("85.3 %")).toBeInTheDocument();
    expect(screen.getByText("base 90.5 %")).toBeInTheDocument();
    expect(screen.getByText("shop/admin.py::run")).toBeInTheDocument();
    expect(screen.getByText("2 file(s) skipped in the archive")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "open on GitHub" })).toHaveAttribute(
      "href",
      "https://github.test/acme/shop/pull/7",
    );

    // s2 already has an issue: its box is checked, disabled, and links to it.
    const existing = screen.getByRole("checkbox", { name: /TM010/ });
    expect(existing).toBeChecked();
    expect(existing).toBeDisabled();
    expect(screen.getByRole("link", { name: "issue #4" })).toBeInTheDocument();

    const create = screen.getByRole("button", { name: /Create/ });
    expect(create).toBeDisabled();
    const box = screen.getByRole("checkbox", { name: /test_discount fails/ });
    await userEvent.click(box);
    await userEvent.click(box);
    await userEvent.click(box);
    expect(create).toHaveTextContent("Create 1 issue(s)");
    await userEvent.click(create);

    expect(await screen.findByRole("link", { name: "issue #5" })).toBeInTheDocument();
    expect(api.calls.find((c) => c.method === "POST")?.body).toEqual({ suggestions: ["s1"] });
    const card = screen.getByText("Regression: test_discount fails").closest(".card");
    expect(within(card as HTMLElement).getByText("Issue text")).toBeInTheDocument();
  });

  it("shows GitHub's refusal to create an issue", async () => {
    mockApi({
      [`GET ${PULL}`]: { body: pullRead() },
      [`GET ${PULL}/report`]: { body: report() },
      [`POST ${PULL}/issues`]: apiError(502, "github_error", "GitHub answered 403: Forbidden"),
    });
    renderAt(`/github/pull-analyses/${PULL_ID}`);
    await userEvent.click(await screen.findByRole("checkbox", { name: /test_discount fails/ }));
    await userEvent.click(screen.getByRole("button", { name: /Create/ }));
    expect(await screen.findByText("GitHub answered 403: Forbidden")).toBeInTheDocument();
  });

  it("has calm empty states for a clean pull request", async () => {
    mockApi({
      [`GET ${PULL}`]: { body: pullRead({ conclusion: "success" }) },
      [`GET ${PULL}/report`]: {
        body: report({
          conclusion: "success",
          new_findings: [],
          untested_changes: [],
          suggestions: [],
          notes: [],
          regression: {
            tests: [],
            base_run: { coverage_percent: null },
            head_run: { coverage_percent: null },
          } as unknown as Schemas["PullReport"]["regression"],
        }),
      },
    });
    renderAt(`/github/pull-analyses/${PULL_ID}`);
    expect(await screen.findByText("No test regressed.")).toBeInTheDocument();
    expect(screen.getByText("No new finding.")).toBeInTheDocument();
    expect(screen.getByText("Every changed function is executed by a test.")).toBeInTheDocument();
    expect(screen.getByText("Nothing to report.")).toBeInTheDocument();
  });

  it("explains a failed analysis", async () => {
    mockApi({
      [`GET ${PULL}`]: {
        body: pullRead({
          status: "failed",
          conclusion: null,
          summary: null,
          error_message: "GitHub answered 500: failure 500",
        }),
      },
    });
    renderAt(`/github/pull-analyses/${PULL_ID}`);
    expect(await screen.findByText("GitHub answered 500: failure 500")).toBeInTheDocument();
    expect(screen.queryByText("Issue suggestions")).toBeNull();
  });
});
