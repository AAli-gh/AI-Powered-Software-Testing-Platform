/** API documents for tests, typed against the generated schema: a schema change that breaks
 * the dashboard's assumptions breaks the type check here first. Values mirror real answers. */
import type { Schemas } from "../api/client";

export const T0 = "2026-09-27T10:00:00Z";
export const T1 = "2026-09-27T10:05:00Z";
export const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
export const RUN_ID = "22222222-2222-4222-8222-222222222222";
export const EXECUTION_ID = "33333333-3333-4333-8333-333333333333";
export const MUTATION_ID = "44444444-4444-4444-8444-444444444444";
export const REGRESSION_ID = "55555555-5555-4555-8555-555555555555";
export const PULL_ID = "66666666-6666-4666-8666-666666666666";
export const SCAN_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

const idle = { queued: 0, running: 0, succeeded: 0, failed: 0, cancelled: 0 };

export function run(
  overrides: Partial<Schemas["AnalysisRunRead"]> = {},
): Schemas["AnalysisRunRead"] {
  return {
    id: RUN_ID,
    project_id: PROJECT_ID,
    status: "succeeded",
    error_message: null,
    created_at: T0,
    updated_at: T0,
    started_at: T0,
    finished_at: "2026-09-27T10:00:01Z",
    ...overrides,
  };
}

export function project(overrides: Partial<Schemas["ProjectRead"]> = {}): Schemas["ProjectRead"] {
  return {
    id: PROJECT_ID,
    name: "shipping_rates",
    source_type: "local",
    source_location: "shipping_rates",
    remote_repository: null,
    remote_ref: null,
    remote_commit: null,
    created_at: T0,
    updated_at: T0,
    ...overrides,
  };
}

export function executionPoint(
  overrides: Partial<Schemas["ExecutionPoint"]> = {},
): Schemas["ExecutionPoint"] {
  return {
    id: EXECUTION_ID,
    run_id: RUN_ID,
    project_id: PROJECT_ID,
    project_name: "shipping_rates",
    status: "succeeded",
    outcome: "passed",
    created_at: T1,
    finished_at: T1,
    tests: { total: 4, passed: 4, failed: 0, skipped: 0 },
    coverage_percent: 88.24,
    api_coverage_percent: null,
    ...overrides,
  };
}

export function platformOverview(
  overrides: Partial<Schemas["PlatformOverview"]> = {},
): Schemas["PlatformOverview"] {
  return {
    projects: 2,
    active_jobs: 0,
    jobs: [
      { kind: "analysis_runs", counts: { ...idle, succeeded: 2 } },
      { kind: "test_executions", counts: { ...idle, succeeded: 3, failed: 1 } },
      { kind: "github_pull_analyses", counts: { ...idle, succeeded: 1 } },
    ],
    project_health: [
      {
        id: PROJECT_ID,
        name: "shipping_rates",
        source_type: "local",
        remote_repository: null,
        latest_run: run(),
        latest_execution: executionPoint(),
        latest_mutation_score: 60,
        active_jobs: 0,
      },
      {
        id: "77777777-7777-4777-8777-777777777777",
        name: "acme/shop",
        source_type: "github",
        remote_repository: "acme/shop",
        latest_run: null,
        latest_execution: null,
        latest_mutation_score: null,
        active_jobs: 1,
      },
    ],
    recent_executions: [
      executionPoint(),
      executionPoint({
        id: "88888888-8888-4888-8888-888888888888",
        outcome: "failed",
        tests: { total: 14, passed: 11, failed: 3, skipped: 0 },
      }),
      executionPoint({
        id: "99999999-9999-4999-8999-999999999999",
        status: "queued",
        outcome: null,
        tests: null,
        coverage_percent: null,
      }),
    ],
    recent_pull_analyses: [
      {
        id: PULL_ID,
        repository: "acme/shop",
        pull_number: 7,
        status: "succeeded",
        conclusion: "failure",
        head_sha: "b".repeat(40),
        created_at: T0,
      },
      {
        id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        repository: "acme/shop",
        pull_number: 8,
        status: "running",
        conclusion: null,
        head_sha: null,
        created_at: T1,
      },
    ],
    ...overrides,
  };
}

export function projectOverview(
  overrides: Partial<Schemas["ProjectOverview"]> = {},
): Schemas["ProjectOverview"] {
  return {
    project: project(),
    runs: { ...idle, succeeded: 1 },
    latest_run: run(),
    latest_succeeded_run: run(),
    executions: [
      executionPoint({ id: "e1", created_at: T0, coverage_percent: 70, api_coverage_percent: 50 }),
      executionPoint({ id: "e2", created_at: T1 }),
      executionPoint({ id: "e3", status: "queued", outcome: null, tests: null, created_at: T1 }),
    ],
    mutation_runs: [
      {
        id: MUTATION_ID,
        run_id: RUN_ID,
        status: "succeeded",
        created_at: T1,
        finished_at: T1,
        score: 60,
        mutants: 20,
        survived: 8,
      },
    ],
    regressions: [
      {
        id: REGRESSION_ID,
        status: "succeeded",
        mode: "full",
        base_ref: "main~1",
        head_ref: "main",
        verdict: "regressions",
        created_at: T0,
      },
      {
        id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        status: "running",
        mode: "selected",
        base_ref: "v1",
        head_ref: "HEAD",
        verdict: null,
        created_at: T1,
      },
    ],
    pull_analyses: [],
    ...overrides,
  };
}

export function page<T>(items: T[], total = items.length) {
  return { items, total, limit: 20, offset: 0 };
}

export function executionRead(
  overrides: Partial<Schemas["TestExecutionRead"]> = {},
): Schemas["TestExecutionRead"] {
  return {
    id: EXECUTION_ID,
    run_id: RUN_ID,
    generation_id: null,
    api_probes: true,
    status: "succeeded",
    outcome: "failed",
    error_message: null,
    created_at: T0,
    started_at: T0,
    finished_at: T1,
    summary: {
      exit_code: 1,
      exit_meaning: "tests failed",
      totals: { total: 14, passed: 11, failed: 2, errors: 1, skipped: 0, xfailed: 0 },
      duration_seconds: 3.8,
      image: "testmind-runner:abc",
      dependencies_from_cache: true,
      install_notes: [],
      project_test_files: ["tests/test_books.py"],
      generated_test_files: [],
      environment: {},
      coverage_percent: 78.95,
      coverage_error: null,
      failure_groups: 3,
      api_coverage_percent: 50,
    },
    ...overrides,
  };
}

export function mutationRead(
  overrides: Partial<Schemas["MutationRunRead"]> = {},
): Schemas["MutationRunRead"] {
  return {
    id: MUTATION_ID,
    run_id: RUN_ID,
    status: "succeeded",
    options: { max_mutants: 60 },
    score: 60,
    error_message: null,
    created_at: T0,
    started_at: T0,
    finished_at: T1,
    summary: {
      baseline_outcome: "passed",
      generated: 20,
      notes: ["2 mutants did not compile and were skipped"],
      totals: {
        total: 20,
        killed: 12,
        survived: 8,
        timeout: 0,
        no_coverage: 0,
        errors: 0,
        score: 60,
        covered_score: 60,
      },
    },
    ...overrides,
  };
}

export function regressionRead(
  overrides: Partial<Schemas["RegressionRead"]> = {},
): Schemas["RegressionRead"] {
  return {
    id: REGRESSION_ID,
    project_id: PROJECT_ID,
    status: "succeeded",
    mode: "full",
    base_ref: "main~1",
    head_ref: "main",
    base_commit: "a".repeat(40),
    head_commit: "b".repeat(40),
    verdict: "regressions",
    error_message: null,
    created_at: T0,
    started_at: T0,
    finished_at: T1,
    summary: {
      changed_files: 2,
      changed_functions: 3,
      everything_selected: false,
      fixed: 0,
      missed_by_selection: 0,
      new_failure_groups: 1,
      regressed: 2,
      selected_targets: 3,
    },
    ...overrides,
  };
}

export function siteScan(overrides: Partial<Schemas["SiteScan"]> = {}): Schemas["SiteScan"] {
  return {
    start_url: "https://example.com/",
    pages: [
      {
        url: "https://example.com/",
        status: 200,
        title: "Home",
        depth: 0,
        from_url: null,
        load_ms: 120,
        links: 4,
        buttons: 2,
      },
      {
        url: "https://example.com/crash",
        status: 500,
        title: null,
        depth: 1,
        from_url: "https://example.com/",
        load_ms: 80,
        links: 0,
        buttons: 0,
      },
    ],
    issues: [
      {
        kind: "server_error",
        severity: "high",
        url: "https://example.com/crash",
        detail: "The server returned 500",
        evidence: [],
        screenshot: null,
      },
      {
        kind: "dead_button",
        severity: "medium",
        url: "https://example.com/",
        detail: "The control 'Does nothing' does nothing when clicked",
        evidence: ["no navigation, no network request, no change to the page"],
        screenshot: null,
      },
      {
        kind: "insecure_header",
        severity: "medium",
        url: "https://example.com/",
        detail: "No Content-Security-Policy response header",
        evidence: [],
        screenshot: null,
      },
    ],
    denied: ["https://tracker.example/ (out of scope)"],
    notes: [],
    ...overrides,
  };
}

export function webScanRead(
  overrides: Partial<Schemas["WebScanRead"]> = {},
): Schemas["WebScanRead"] {
  return {
    id: SCAN_ID,
    target_url: "https://example.com/",
    status: "succeeded",
    max_pages: 100,
    max_depth: 5,
    interact: true,
    security: true,
    active: false,
    error_message: null,
    created_at: T0,
    started_at: T0,
    finished_at: T1,
    result: siteScan(),
    ...overrides,
  };
}

export function suggestion(id: string, title: string): Schemas["IssueSuggestion"] {
  return { id, kind: "regression", title, body: `Body of ${title}`, labels: ["testmind"] };
}

export function pullRead(
  overrides: Partial<Schemas["PullAnalysisRead"]> = {},
): Schemas["PullAnalysisRead"] {
  return {
    id: PULL_ID,
    repository: "acme/shop",
    pull_number: 7,
    installation_id: 1,
    head_sha: "b".repeat(40),
    base_sha: "a".repeat(40),
    status: "succeeded",
    conclusion: "failure",
    check_run_id: 1001,
    comment_id: 1002,
    issues: [],
    error_message: null,
    created_at: T0,
    started_at: T0,
    finished_at: T1,
    summary: {
      title: "Change pricing",
      url: "https://github.test/acme/shop/pull/7",
      regressed_tests: 1,
      new_findings: 1,
      untested_changes: 1,
      suggestions: [suggestion("s1", "Regression: test_discount fails")],
    },
    ...overrides,
  };
}
