import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

/** An error answered by the API (its uniform error body), or a network failure (status 0). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  get notFound(): boolean {
    return this.status === 404;
  }
}

// Same origin: Vite proxies /api in development, nginx in production. `fetch` is looked up on
// every call so tests can replace it at the network boundary.
export const api = createClient<paths>({
  baseUrl: globalThis.location.origin,
  fetch: (request: Request) => globalThis.fetch(request),
});

interface Answer<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/** The body of a successful answer, or an ApiError carrying the API's code and message. */
export async function unwrap<T>(call: Promise<Answer<T>>): Promise<T> {
  let answer: Answer<T>;
  try {
    answer = await call;
  } catch {
    throw new ApiError(0, "network_error", "The TestMind API could not be reached");
  }
  if (answer.response.ok && answer.data !== undefined) {
    return answer.data;
  }
  const body = answer.error as { error?: { code?: string; message?: string } } | undefined;
  throw new ApiError(
    answer.response.status,
    body?.error?.code ?? "unexpected_answer",
    body?.error?.message ?? `The API answered ${answer.response.status}`,
  );
}
