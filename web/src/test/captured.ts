/** Real answers of the API, captured from runs on the example repositories (see
 * docs/dashboard.md): buggy_api (tests, coverage, API coverage), failing_suite (failures),
 * buggy_inventory (findings) and shipping_rates (mutation testing). */
import type { Schemas } from "../api/client";
import apiCoverage from "./captured/api_coverage.json";
import apiEndpoints from "./captured/api_endpoints.json";
import apiSurface from "./captured/api_surface.json";
import codeSummary from "./captured/code_summary.json";
import coverage from "./captured/coverage.json";
import coverageFiles from "./captured/coverage_files.json";
import failureGroups from "./captured/failure_groups.json";
import failures from "./captured/failures.json";
import findings from "./captured/findings.json";
import functions from "./captured/functions.json";
import logs from "./captured/logs.json";
import mutants from "./captured/mutants.json";
import mutationFunctions from "./captured/mutation_functions.json";
import repositorySummary from "./captured/repository_summary.json";
import tests from "./captured/tests.json";

interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

// JSON imports widen enums to string; the documents are the API's own answers.
export const captured = {
  repositorySummary: repositorySummary as unknown as Schemas["RepositorySummary"],
  codeSummary: codeSummary as unknown as Schemas["CodeSummary"],
  findings: findings as unknown as Page<Schemas["Finding"]>,
  functions: functions as unknown as Page<Schemas["FunctionInfo"]>,
  apiSurface: apiSurface as unknown as Schemas["ApiSurface"],
  tests: tests as unknown as Page<Schemas["TestCaseReport"]>,
  coverage: coverage as unknown as Schemas["CoverageOverview"],
  coverageFiles: coverageFiles as unknown as Page<Schemas["FileCoverage"]>,
  failures: failures as unknown as Schemas["FailureOverview"],
  failureGroups: failureGroups as unknown as Page<Schemas["FailureGroup"]>,
  apiCoverage: apiCoverage as unknown as Schemas["ApiCoverageOverview"],
  apiEndpoints: apiEndpoints as unknown as Page<Schemas["EndpointCoverage"]>,
  logs: logs satisfies Schemas["ExecutionLogs"],
  mutants: mutants as unknown as Page<Schemas["MutantResult"]>,
  mutationFunctions: mutationFunctions as unknown as Page<Schemas["FunctionScore"]>,
};
