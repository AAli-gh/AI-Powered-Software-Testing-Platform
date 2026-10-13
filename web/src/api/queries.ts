import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { isActive } from "../lib/status";
import { api, unwrap, type Schemas } from "./client";

export const POLL_MS = 2_000;

/** Refetch while the job is queued or running; stop once it has finished. */
function whileActive(query: { state: { data: { status: string } | undefined } }) {
  return isActive(query.state.data?.status) ? POLL_MS : false;
}

export function usePlatformOverview(): UseQueryResult<Schemas["PlatformOverview"]> {
  return useQuery({
    queryKey: ["overview"],
    queryFn: () => unwrap(api.GET("/api/v1/overview")),
    refetchInterval: (query) => ((query.state.data?.active_jobs ?? 0) > 0 ? POLL_MS : false),
  });
}

export function useProjects(offset: number, limit: number) {
  return useQuery({
    queryKey: ["projects", offset, limit],
    queryFn: () => unwrap(api.GET("/api/v1/projects", { params: { query: { offset, limit } } })),
  });
}

export function useProjectOverview(projectId: string) {
  return useQuery({
    queryKey: ["project-overview", projectId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/overview", {
          params: { path: { project_id: projectId } },
        }),
      ),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      const busy =
        data.runs.queued + data.runs.running > 0 ||
        data.executions.some((e) => isActive(e.status)) ||
        data.mutation_runs.some((m) => isActive(m.status)) ||
        data.regressions.some((r) => isActive(r.status));
      return busy ? POLL_MS : false;
    },
  });
}

export function useProjectRuns(projectId: string, offset: number, limit: number) {
  return useQuery({
    queryKey: ["project-runs", projectId, offset, limit],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/runs", {
          params: { path: { project_id: projectId }, query: { offset, limit } },
        }),
      ),
  });
}

export function useRun(runId: string) {
  return useQuery({
    queryKey: ["run", runId],
    enabled: runId !== "",
    queryFn: () =>
      unwrap(api.GET("/api/v1/runs/{run_id}", { params: { path: { run_id: runId } } })),
    refetchInterval: whileActive,
  });
}

export function useProject(projectId: string | undefined) {
  return useQuery({
    queryKey: ["project", projectId],
    enabled: projectId !== undefined,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}", {
          params: { path: { project_id: projectId ?? "" } },
        }),
      ),
  });
}

export function useExecution(executionId: string) {
  return useQuery({
    queryKey: ["execution", executionId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/test-executions/{execution_id}", {
          params: { path: { execution_id: executionId } },
        }),
      ),
    refetchInterval: whileActive,
  });
}

export function useRegression(regressionId: string) {
  return useQuery({
    queryKey: ["regression", regressionId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/regressions/{regression_id}", {
          params: { path: { regression_id: regressionId } },
        }),
      ),
    refetchInterval: whileActive,
  });
}

export function useMutationRun(mutationRunId: string) {
  return useQuery({
    queryKey: ["mutation-run", mutationRunId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/mutation-runs/{mutation_run_id}", {
          params: { path: { mutation_run_id: mutationRunId } },
        }),
      ),
    refetchInterval: whileActive,
  });
}

export function useWebScans(offset: number, limit: number) {
  return useQuery({
    queryKey: ["web-scans", offset, limit],
    queryFn: () => unwrap(api.GET("/api/v1/web-scans", { params: { query: { offset, limit } } })),
    refetchInterval: (query) =>
      query.state.data?.items.some((s) => isActive(s.status)) ? POLL_MS : false,
  });
}

export function useWebScan(scanId: string) {
  return useQuery({
    queryKey: ["web-scan", scanId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/web-scans/{scan_id}", { params: { path: { scan_id: scanId } } })),
    refetchInterval: whileActive,
  });
}

export function useAuthorizedTargets() {
  return useQuery({
    queryKey: ["authorized-targets"],
    queryFn: () => unwrap(api.GET("/api/v1/authorized-targets")),
  });
}

export function usePullAnalysis(analysisId: string) {
  return useQuery({
    queryKey: ["pull-analysis", analysisId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/github/pull-analyses/{analysis_id}", {
          params: { path: { analysis_id: analysisId } },
        }),
      ),
    refetchInterval: whileActive,
  });
}
