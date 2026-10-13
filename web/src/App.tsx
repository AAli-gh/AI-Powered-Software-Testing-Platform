import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { createBrowserRouter, RouterProvider, type RouteObject } from "react-router";

import { ApiError } from "./api/client";
import { Layout } from "./components/Layout";
import { ExecutionPage } from "./pages/ExecutionPage";
import { MutationRunPage } from "./pages/MutationRunPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { OverviewPage } from "./pages/OverviewPage";
import { ProjectPage } from "./pages/ProjectPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { PullAnalysesPage } from "./pages/PullAnalysesPage";
import { PullAnalysisPage } from "./pages/PullAnalysisPage";
import { RegressionPage } from "./pages/RegressionPage";
import { RunPage } from "./pages/RunPage";
import { WebScanPage } from "./pages/WebScanPage";
import { WebScansPage } from "./pages/WebScansPage";

export const routes: RouteObject[] = [
  {
    element: <Layout />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "projects", element: <ProjectsPage /> },
      { path: "projects/:projectId", element: <ProjectPage /> },
      { path: "runs/:runId", element: <RunPage /> },
      { path: "executions/:executionId", element: <ExecutionPage /> },
      { path: "regressions/:regressionId", element: <RegressionPage /> },
      { path: "mutation-runs/:mutationRunId", element: <MutationRunPage /> },
      { path: "github", element: <PullAnalysesPage /> },
      { path: "github/pull-analyses/:analysisId", element: <PullAnalysisPage /> },
      { path: "web-scans", element: <WebScansPage /> },
      { path: "web-scans/:scanId", element: <WebScanPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5_000,
        // A 4xx will not change by asking again; network errors and 5xx get one retry.
        retry: (failures, error) =>
          failures < 1 && !(error instanceof ApiError && error.status >= 400 && error.status < 500),
      },
    },
  });
}

export function App() {
  const [router] = useState(() => createBrowserRouter(routes));
  const [queryClient] = useState(createQueryClient);
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
