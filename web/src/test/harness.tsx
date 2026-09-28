/**
 * Test harness: the API is replaced at the network boundary (fetch), everything else is real —
 * the generated client, TanStack Query, the router with the application's own routes.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { vi } from "vitest";

import { routes } from "../App";

export interface Answer {
  status?: number;
  body?: unknown;
}

type Handler = (request: Request, url: URL) => Answer | Promise<Answer>;
export type Routes = Record<string, Answer | Handler>;

export interface Recorded {
  method: string;
  path: string;
  search: string;
  body: unknown;
}

/**
 * Route "METHOD /path" (exact path, query ignored) to an answer. An unexpected request fails
 * the test with a 599, so a page cannot silently call an endpoint nobody planned for.
 */
export function mockApi(table: Routes) {
  const calls: Recorded[] = [];
  const fetchMock = vi.fn(async (input: Request) => {
    const url = new URL(input.url);
    const text = input.method === "GET" ? "" : await input.text();
    calls.push({
      method: input.method,
      path: url.pathname,
      search: url.search,
      body: text ? (JSON.parse(text) as unknown) : undefined,
    });
    const route = table[`${input.method} ${url.pathname}`];
    if (route === undefined) {
      return json(599, {
        error: { code: "unplanned", message: `${input.method} ${url.pathname}` },
      });
    }
    const answer = typeof route === "function" ? await route(input, url) : route;
    return json(answer.status ?? 200, answer.body ?? null);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

function json(status: number, body: unknown): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function apiError(status: number, code: string, message: string): Answer {
  return { status, body: { error: { code, message } } };
}

export function renderAt(path: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const view = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...view, router, client };
}
